#!/usr/bin/env python3
"""Report token usage observed in explicitly selected Claude JSONL transcripts.

The script intentionally has no package imports so that a materialized project
can run it from any working directory. It handles the common Claude Code
assistant/message, API request, and terminal result shapes while preserving
visible limitations for data it cannot attribute or reconcile.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence


METRICS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
)

_ALIASES: dict[str, tuple[str, ...]] = {
    "input_tokens": ("input_tokens", "inputTokens", "uncached_input_tokens", "uncachedInputTokens"),
    "cache_read_input_tokens": (
        "cache_read_input_tokens",
        "cacheReadInputTokens",
        "cache_read_tokens",
        "cacheReadTokens",
    ),
    "cache_creation_input_tokens": (
        "cache_creation_input_tokens",
        "cacheCreationInputTokens",
        "cache_creation_tokens",
        "cacheCreationTokens",
    ),
    "output_tokens": ("output_tokens", "outputTokens", "total_output_tokens", "totalOutputTokens"),
}

_IDENTITY_FIELDS = (
    ("message", "id"),
    ("message", "uuid"),
    ("message.uuid", None),
    ("message_uuid", None),
    ("message_id", None),
    ("messageId", None),
    ("request_id", None),
    ("requestId", None),
    ("response_id", None),
    ("responseId", None),
    ("generation_id", None),
    ("generationId", None),
)

_NON_ROLE_QUERY_SOURCES = {"main", "auxiliary", "compact", "repl_main_thread", "unknown"}


class ReportError(ValueError):
    """Raised for invalid command arguments or inaccessible transcripts."""


@dataclass
class UsageCandidate:
    identity: str
    usage: dict[str, int]
    observed: set[str]
    timestamp: datetime | None
    model: str | None
    role: str | None
    kind: str
    path: str
    line_number: int
    session_id: str | None
    derived: set[str]

    @property
    def quality(self) -> tuple[int, int, int]:
        # A later complete snapshot supersedes an earlier partial snapshot.
        return (
            len(self.observed),
            self.usage.get("output_tokens", -1),
            self.line_number,
        )


class Totals:
    def __init__(self) -> None:
        self.values: dict[str, int] = {metric: 0 for metric in METRICS}
        self.observed: set[str] = set()

    def add(self, usage: Mapping[str, int]) -> None:
        for metric, value in usage.items():
            if metric in self.values:
                self.values[metric] += value
                self.observed.add(metric)

    def as_dict(self) -> dict[str, int | None]:
        values = {
            metric: self.values[metric] if metric in self.observed else None
            for metric in METRICS
        }
        # Keep the standard API spelling while making the accounting category
        # explicit for readers of the report schema.
        values["uncached_input_tokens"] = values["input_tokens"]
        return values


def _parse_iso(value: str | None, *, label: str) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ReportError(f"invalid {label} timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_from(record: Mapping[str, Any]) -> tuple[datetime | None, bool]:
    values: list[Any] = []
    for key in ("timestamp", "created_at", "createdAt", "event_timestamp", "eventTimestamp", "event.timestamp"):
        if key in record:
            values.append(record[key])
    event = record.get("event")
    if isinstance(event, Mapping):
        for key in ("timestamp", "created_at", "createdAt", "event.timestamp"):
            if key in event:
                values.append(event[key])
    message = record.get("message")
    if isinstance(message, Mapping):
        for key in ("timestamp", "created_at", "createdAt"):
            if key in message:
                values.append(message[key])
    if not values:
        return None, False
    value = values[0]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc), False
        except (OverflowError, OSError, ValueError):
            return None, True
    if not isinstance(value, str):
        return None, True
    try:
        parsed = _parse_iso(value, label="record")
    except ReportError:
        return None, True
    # A naive transcript timestamp is accepted as UTC, but callers should see
    # that an assumption was made.
    naive = not (value.endswith("Z") or value.endswith("z") or "+" in value[10:] or "-" in value[10:])
    return parsed, naive


def _decode_json_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _walk(value: Any, *, seen: set[int] | None = None, depth: int = 0) -> Iterable[Mapping[str, Any]]:
    """Yield mappings nested in a transcript record, decoding JSON bodies."""
    if depth > 8:
        return
    if seen is None:
        seen = set()
    value = _decode_json_string(value)
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            return
        seen.add(marker)
        yield value
        for child in value.values():
            yield from _walk(child, seen=seen, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, seen=seen, depth=depth + 1)


def _number(value: Any, *, field: str, warnings: list[str]) -> int | None:
    if isinstance(value, bool):
        warnings.append(f"usage field {field!r} is boolean and was ignored")
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        warnings.append(f"usage field {field!r} is not an integer and was ignored")
        return None
    if number < 0:
        warnings.append(f"usage field {field!r} is negative and was ignored")
        return None
    return number


def _metrics_from(mapping: Mapping[str, Any], warnings: list[str]) -> tuple[dict[str, int], set[str], set[str]]:
    usage: dict[str, int] = {}
    observed: set[str] = set()
    derived: set[str] = set()
    for metric, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in mapping:
                number = _number(mapping[alias], field=alias, warnings=warnings)
                if number is not None:
                    usage[metric] = number
                    observed.add(metric)
                break

    # Claude statusline/context snapshots may expose only the combined input
    # count. Infer uncached input only when both cache components are present;
    # otherwise preserve the unavailable distinction.
    if "input_tokens" not in observed and any(key in mapping for key in ("total_input_tokens", "totalInputTokens")):
        total_key = "total_input_tokens" if "total_input_tokens" in mapping else "totalInputTokens"
        total = _number(mapping[total_key], field=total_key, warnings=warnings)
        if total is not None and {"cache_read_input_tokens", "cache_creation_input_tokens"}.issubset(observed):
            uncached = total - usage["cache_read_input_tokens"] - usage["cache_creation_input_tokens"]
            if uncached >= 0:
                usage["input_tokens"] = uncached
                observed.add("input_tokens")
                derived.add("input_tokens")
            else:
                warnings.append("total input tokens are smaller than cache components; uncached input is unavailable")
        elif total is not None:
            warnings.append("combined total input tokens cannot be separated into uncached and cache input")
    return usage, observed, derived


def _usage_mappings(record: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], str]]:
    found: list[tuple[Mapping[str, Any], str]] = []
    seen: set[int] = set()
    for mapping in _walk(record):
        for key in ("usage", "current_usage", "currentUsage"):
            child = mapping.get(key)
            child = _decode_json_string(child)
            if isinstance(child, Mapping) and id(child) not in seen:
                seen.add(id(child))
                found.append((child, key))
        # API request and statusline records sometimes put the usage fields
        # directly on the event object.
        if any(alias in mapping for aliases in _ALIASES.values() for alias in aliases) or any(
            key in mapping for key in ("total_input_tokens", "totalInputTokens")
        ):
            if id(mapping) not in seen:
                seen.add(id(mapping))
                found.append((mapping, "direct"))
    return found


def _model_usage_mappings(record: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    result: list[tuple[str, Mapping[str, Any]]] = []
    for mapping in _walk(record):
        for key in ("model_usage", "modelUsage"):
            value = _decode_json_string(mapping.get(key))
            if not isinstance(value, Mapping):
                continue
            for model, usage in value.items():
                usage = _decode_json_string(usage)
                if isinstance(usage, Mapping):
                    result.append((str(model), usage))
    return result


def _first_value(record: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for mapping in _walk(record):
        for key in keys:
            if key in mapping and mapping[key] not in (None, ""):
                return mapping[key]
    return None


def _role_from(record: Mapping[str, Any]) -> str | None:
    role_keys = (
        "agent_type",
        "agentType",
        "subagent_type",
        "subagentType",
        "agent_name",
        "agentName",
        "query_source",
        "querySource",
        "query_source_safe",
        "querySourceSafe",
        "agent.name",
    )
    for mapping in _walk(record):
        for key in role_keys:
            value = mapping.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            normalized = value.strip().casefold()
            if key in {"query_source", "querySource", "query_source_safe", "querySourceSafe"} and normalized in _NON_ROLE_QUERY_SOURCES:
                continue
            return value.strip()
    for mapping in _walk(record):
        agent = mapping.get("agent")
        if isinstance(agent, Mapping):
            name = agent.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    # Telemetry identifies a subagent even when detailed role names are
    # redacted. Preserve that fact without pretending the identifier is a
    # workflow role name.
    value = _first_value(record, ("agent_id", "agentId", "subagent_id", "subagentId"))
    if isinstance(value, str) and value.strip():
        return f"agent:{value.strip()}"
    return None


def _model_from(record: Mapping[str, Any]) -> str | None:
    value = _first_value(record, ("model", "model_id", "modelId"))
    return value.strip() if isinstance(value, str) and value.strip() else None


def _session_from(record: Mapping[str, Any]) -> str | None:
    value = _first_value(record, ("session_id", "sessionId"))
    return value.strip() if isinstance(value, str) and value.strip() else None


def _identity_from(record: Mapping[str, Any], *, usage: Mapping[str, int], kind: str, model: str | None) -> str:
    # A modelUsage object contains one aggregate per model. Its enclosing
    # result may also carry a message id, but that id identifies the terminal
    # record rather than each model entry.
    if kind == "model_aggregate":
        session = _session_from(record) or ""
        return f"model-aggregate:{session}:{model or ''}"
    record_type = str(record.get("type", record.get("event.name", ""))).lower()
    # A raw API response body stores the response id in the decoded body. It
    # corresponds to message.uuid in the persisted assistant entry.
    if record_type in {"api_response_body", "api_response"}:
        for mapping in _walk(record):
            if mapping is record:
                continue
            response_id = mapping.get("id")
            if response_id not in (None, ""):
                return f"message:{response_id}"
    for field, nested in _IDENTITY_FIELDS:
        if nested == "id":
            parent = record.get(field)
            if isinstance(parent, Mapping) and parent.get(nested) not in (None, ""):
                return f"message:{parent[nested]}"
            if isinstance(parent, Mapping) and parent.get("uuid") not in (None, ""):
                return f"message:{parent['uuid']}"
        elif nested is None and record.get(field) not in (None, ""):
            if field in {"message.uuid", "message_uuid"}:
                return f"message:{record[field]}"
            return f"{field}:{record[field]}"
    record_type = str(record.get("type", ""))
    session = _session_from(record) or ""
    if kind == "terminal" and session:
        return f"terminal:{session}"
    # Assistant snapshots can receive a fresh event UUID while retaining the
    # same message content and usage. Prefer their stable payload hash so a
    # missing message id does not make snapshots look like separate turns.
    has_message_snapshot = isinstance(record.get("message"), Mapping) or "content" in record
    if record.get("uuid") not in (None, "") and not (kind == "per_response" and has_message_snapshot):
        return f"uuid:{record['uuid']}"
    relevant = {
        "type": record_type,
        "session": session,
        "model": model,
        "usage": dict(usage),
        "message": record.get("message"),
        "result": record.get("result"),
    }
    canonical = json.dumps(relevant, sort_keys=True, separators=(",", ":"), default=str)
    return "hash:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _kind_for(record: Mapping[str, Any], source: str) -> str:
    record_type = str(record.get("type", "")).lower()
    if record_type in {"result", "summary", "session_result", "terminal"} or source in {"result", "summary"}:
        return "terminal"
    return "per_response"


def _terminal_is_main(record: Mapping[str, Any]) -> bool:
    """Return whether a terminal snapshot belongs to the main session.

    Claude's persisted transcript format is versioned and internal. Current
    transcripts mark sidechain results with ``isSidechain``; telemetry and
    exported records may instead expose an agent identifier. In ambiguous
    cases, retain the record and let the report describe the attribution gap.
    """
    sidechain_marker = _first_value(record, ("isSidechain", "is_sidechain", "sidechain"))
    if isinstance(sidechain_marker, bool):
        return not sidechain_marker
    if isinstance(sidechain_marker, str):
        lowered = sidechain_marker.strip().casefold()
        if lowered in {"true", "yes", "1"}:
            return False
        if lowered in {"false", "no", "0"}:
            return True
    agent_id = _first_value(record, ("agent_id", "agentId", "subagent_id", "subagentId"))
    if isinstance(agent_id, str) and agent_id.strip():
        return False
    return True


def _candidate_from(
    record: Mapping[str, Any],
    mapping: Mapping[str, Any],
    source: str,
    path: str,
    line_number: int,
    *,
    model_override: str | None = None,
    kind_override: str | None = None,
    warnings: list[str],
) -> UsageCandidate | None:
    usage, observed, derived = _metrics_from(mapping, warnings)
    if not observed:
        return None
    model = model_override or _model_from(record)
    kind = kind_override or _kind_for(record, source)
    return UsageCandidate(
        identity=_identity_from(record, usage=usage, kind=kind, model=model),
        usage=usage,
        observed=observed,
        timestamp=_timestamp_from(record)[0],
        model=model,
        role=_role_from(record),
        kind=kind,
        path=path,
        line_number=line_number,
        session_id=_session_from(record),
        derived=derived,
    )


def _deduplicate(candidates: Iterable[UsageCandidate], warnings: list[str]) -> tuple[list[UsageCandidate], int]:
    selected: dict[str, UsageCandidate] = {}
    duplicates = 0
    for candidate in candidates:
        previous = selected.get(candidate.identity)
        if previous is None:
            selected[candidate.identity] = candidate
            continue
        duplicates += 1
        if previous.model != candidate.model or previous.role != candidate.role:
            warnings.append(f"duplicate usage identity {candidate.identity!r} has conflicting attribution")
        if candidate.quality >= previous.quality:
            selected[candidate.identity] = candidate
    return list(selected.values()), duplicates


def _in_bounds(candidate: UsageCandidate, since: datetime | None, until: datetime | None) -> bool:
    if since is None and until is None:
        return True
    if candidate.timestamp is None:
        return False
    if since is not None and candidate.timestamp < since:
        return False
    if until is not None and candidate.timestamp > until:
        return False
    return True


def _merge_totals(target: Totals, values: Mapping[str, int | None]) -> None:
    target.add({metric: int(value) for metric, value in values.items() if value is not None})


def build_report(
    transcript_paths: Sequence[str | Path],
    *,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
) -> dict[str, Any]:
    """Build a JSON-compatible observational report for selected JSONL paths."""
    if not transcript_paths:
        raise ReportError("at least one transcript path is required")
    if isinstance(since, str):
        since_dt = _parse_iso(since, label="since")
    else:
        since_dt = since.astimezone(timezone.utc) if since is not None else None
    if isinstance(until, str):
        until_dt = _parse_iso(until, label="until")
    else:
        until_dt = until.astimezone(timezone.utc) if until is not None else None
    if since_dt is not None and until_dt is not None and since_dt > until_dt:
        raise ReportError("since timestamp must be earlier than or equal to until timestamp")

    limitations: list[str] = [
        "This report is observational transcript accounting; exact billing reconciliation is unavailable.",
    ]
    warnings: list[str] = []
    all_candidates: list[UsageCandidate] = []
    records: dict[str, int] = {
        "transcripts": len(transcript_paths),
        "lines_read": 0,
        "parsed": 0,
        "usage_records": 0,
        "counted": 0,
        "deduplicated": 0,
        "malformed_lines": 0,
        "without_usage": 0,
        "malformed_usage": 0,
        "incomplete_usage": 0,
        "outside_time_bounds": 0,
        "without_timestamp": 0,
        "aggregates_preferred": 0,
        "cumulative_excluded_for_bounds": 0,
        "ambiguous_scope_excluded": 0,
        "ignored_sidechain_terminals": 0,
    }

    for raw_path in transcript_paths:
        path = Path(raw_path)
        if not path.is_file():
            raise ReportError(f"transcript does not exist or is not a file: {path}")
        try:
            handle = path.open("r", encoding="utf-8")
        except OSError as exc:
            raise ReportError(f"cannot read transcript {path}: {exc}") from exc
        with handle:
            for line_number, line in enumerate(handle, start=1):
                records["lines_read"] += 1
                text = line.strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError:
                    records["malformed_lines"] += 1
                    continue
                if not isinstance(record, Mapping):
                    records["malformed_lines"] += 1
                    continue
                records["parsed"] += 1
                timestamp, timestamp_issue = _timestamp_from(record)
                if timestamp_issue:
                    warnings.append(f"{path}:{line_number} has an invalid or naive timestamp")
                if _kind_for(record, "") == "terminal" and not _terminal_is_main(record):
                    records["ignored_sidechain_terminals"] += 1
                    continue
                model_aggregates = _model_usage_mappings(record)
                line_candidates: list[UsageCandidate] = []
                if model_aggregates:
                    for model, mapping in model_aggregates:
                        candidate = _candidate_from(
                            record,
                            mapping,
                            "model_usage",
                            str(path),
                            line_number,
                            model_override=model,
                            kind_override="model_aggregate",
                            warnings=warnings,
                        )
                        if candidate is not None:
                            line_candidates.append(candidate)
                for mapping, source in _usage_mappings(record):
                    candidate = _candidate_from(
                        record,
                        mapping,
                        source,
                        str(path),
                        line_number,
                        warnings=warnings,
                    )
                    if candidate is not None:
                        line_candidates.append(candidate)
                if not line_candidates:
                    records["without_usage"] += 1
                    continue
                records["usage_records"] += len(line_candidates)
                all_candidates.extend(line_candidates)

    # Apply requested time bounds before deduplication and aggregate source
    # selection. A cumulative snapshot outside the window must not replace a
    # response that is inside it, and duplicate snapshots must be resolved
    # among the records that can actually answer the bounded question.
    bounded = since_dt is not None or until_dt is not None
    candidates_in_window: list[UsageCandidate] = []
    for candidate in all_candidates:
        if not bounded:
            candidates_in_window.append(candidate)
            continue
        if candidate.timestamp is None:
            records["without_timestamp"] += 1
            records["outside_time_bounds"] += 1
            continue
        if not _in_bounds(candidate, since_dt, until_dt):
            records["outside_time_bounds"] += 1
            continue
        candidates_in_window.append(candidate)

    # Deduplicate repeated message snapshots and repeated explicit transcript
    # selections before handling aggregate records.
    unique, duplicates = _deduplicate(candidates_in_window, warnings)
    records["deduplicated"] = duplicates

    # Whole-tree modelUsage and terminal snapshots are cumulative observations;
    # they cannot be sliced to an arbitrary time window. Keep in-window
    # responses available for bounded reports and disclose when a cumulative
    # record had to be excluded instead of treating its full history as the
    # window's usage.
    if bounded:
        usable: list[UsageCandidate] = []
        for candidate in unique:
            if candidate.kind in {"model_aggregate", "terminal"}:
                records["cumulative_excluded_for_bounds"] += 1
                continue
            usable.append(candidate)
        unique = usable

    # A modelUsage aggregate is a whole-tree snapshot. Prefer it over its
    # component response records. Scope that preference by session across all
    # selected paths so a main aggregate does not get summed with a worker
    # transcript carrying the same session id. Terminal result usage is
    # main-session cumulative usage; when per-response records exist, use those
    # instead of adding the cumulative result a second time.
    by_source: dict[str, list[UsageCandidate]] = {}
    for candidate in unique:
        by_source.setdefault(candidate.path, []).append(candidate)
    aggregate_sessions = {
        candidate.session_id
        for candidate in unique
        if candidate.kind == "model_aggregate" and candidate.session_id
    }
    aggregate_origins = {
        (candidate.path, candidate.line_number)
        for candidate in unique
        if candidate.kind == "model_aggregate"
    }
    has_unscoped_aggregate_any = any(
        candidate.kind == "model_aggregate" and candidate.session_id is None
        for candidate in unique
    )
    selected: list[UsageCandidate] = []
    for path, candidates in by_source.items():
        aggregates = [candidate for candidate in candidates if candidate.kind == "model_aggregate"]
        aggregate_sessions_here = {
            candidate.session_id for candidate in aggregates if candidate.session_id
        }
        has_unscoped_aggregate_here = any(candidate.session_id is None for candidate in aggregates)
        retained: list[UsageCandidate] = []
        ignored_for_aggregate = 0
        for candidate in candidates:
            if candidate.kind == "model_aggregate":
                retained.append(candidate)
                continue
            # A missing session identity cannot establish that a selected
            # component transcript is disjoint from a whole-tree aggregate.
            # Exclude it rather than adding a potentially overlapping record.
            # The same rule applies when the aggregate itself lacks scope: a
            # scoped worker record may still belong to that aggregate.
            if has_unscoped_aggregate_any or (aggregate_sessions and candidate.session_id is None):
                # A result line can expose both modelUsage and a duplicate
                # direct usage block while walking nested JSON. That record
                # is known to overlap the aggregate from the same line; keep
                # it in the ordinary aggregate-preference count. Only count
                # records from another origin as ambiguous scope.
                if (candidate.path, candidate.line_number) in aggregate_origins:
                    ignored_for_aggregate += 1
                else:
                    records["ambiguous_scope_excluded"] += 1
                continue
            same_session = candidate.session_id and candidate.session_id in aggregate_sessions
            if same_session:
                ignored_for_aggregate += 1
                continue
            retained.append(candidate)
        if ignored_for_aggregate:
            records["aggregates_preferred"] += ignored_for_aggregate
            if aggregate_sessions_here:
                warnings.append(
                    f"ignored {ignored_for_aggregate} per-response or terminal record(s) in {path} because a whole-tree model usage aggregate covers the same session"
                )
            else:
                warnings.append(
                    f"ignored {ignored_for_aggregate} per-response or terminal record(s) because model usage is a whole-tree aggregate in {path}"
                )
        if has_unscoped_aggregate_here:
            warnings.append(
                f"model usage aggregate scope is unavailable in {path}; same-path usage was treated as overlapping"
            )
        responses = [candidate for candidate in retained if candidate.kind == "per_response"]
        terminals = [candidate for candidate in retained if candidate.kind == "terminal"]
        if responses and terminals:
            selected.extend(responses)
            records["aggregates_preferred"] += len(terminals)
            warnings.append(f"ignored {len(terminals)} terminal cumulative record(s) beside per-response usage in {path}")
        else:
            selected.extend(retained)

    totals = Totals()
    by_role: dict[str, Totals] = {}
    by_model: dict[str, Totals] = {}
    attribution_unavailable = 0
    derived_fields: set[str] = set()
    for candidate in selected:
        records["counted"] += 1
        if candidate.observed != set(METRICS):
            records["incomplete_usage"] += 1
        totals.add(candidate.usage)
        derived_fields.update(candidate.derived)
        if candidate.role:
            by_role.setdefault(candidate.role, Totals()).add(candidate.usage)
        else:
            attribution_unavailable += 1
        if candidate.model:
            by_model.setdefault(candidate.model, Totals()).add(candidate.usage)

    if records["malformed_lines"]:
        limitations.append(f"{records['malformed_lines']} malformed transcript line(s) were skipped.")
    if records["without_usage"]:
        limitations.append(f"{records['without_usage']} parsed record(s) had no recognized usage block.")
    if records["without_timestamp"] and (since_dt is not None or until_dt is not None):
        limitations.append(f"{records['without_timestamp']} usage record(s) lacked timestamps and were excluded from bounded reporting.")
    if records["outside_time_bounds"]:
        limitations.append(f"{records['outside_time_bounds']} usage record(s) fell outside the requested time bounds.")
    if records["aggregates_preferred"]:
        limitations.append("Cumulative terminal or whole-tree model usage was kept separate from per-response usage to avoid double counting.")
    if records.get("cumulative_excluded_for_bounds"):
        limitations.append(
            f"{records['cumulative_excluded_for_bounds']} cumulative terminal or whole-tree record(s) were excluded from bounded reporting because their history cannot be sliced to the requested window."
        )
    if records["ambiguous_scope_excluded"]:
        limitations.append(
            f"{records['ambiguous_scope_excluded']} usage record(s) were excluded because a whole-tree aggregate could overlap them without a trustworthy shared session identity; missing identity is not evidence of disjoint sessions."
        )
    if records["ignored_sidechain_terminals"]:
        limitations.append(
            f"{records['ignored_sidechain_terminals']} sidechain terminal result(s) were excluded; terminal accounting is main-session only."
        )
    if attribution_unavailable:
        limitations.append(f"Role attribution was unavailable for {attribution_unavailable} counted record(s); those records are excluded from by_role groups.")
    if records["counted"] == 0:
        limitations.append("No usable usage record was counted; numeric totals are unavailable rather than assumed to be zero.")
        limitations.append("Role attribution was unavailable because no usage record was counted.")
    if derived_fields:
        limitations.append("Uncached input was derived from a combined input total and cache components for some record(s).")
    for warning in warnings:
        if "usage field" in warning or "combined total" in warning or "negative" in warning:
            records["malformed_usage"] += 1
    if records["malformed_usage"]:
        limitations.append(f"{records['malformed_usage']} usage field warning(s) were encountered; affected fields may be unavailable.")
    if records["incomplete_usage"]:
        limitations.append(
            f"{records['incomplete_usage']} counted usage record(s) were incomplete because one or more token categories were omitted; omitted categories remain unavailable."
        )
    # Preserve warning detail without flooding normal reports.
    if warnings:
        unique_warnings = list(dict.fromkeys(warnings))
        limitations.extend(f"Data note: {warning}." for warning in unique_warnings[:20])

    report: dict[str, Any] = {
        "schema_version": "1",
        "bounds": {
            "since": since_dt.isoformat().replace("+00:00", "Z") if since_dt else None,
            "until": until_dt.isoformat().replace("+00:00", "Z") if until_dt else None,
            "inclusive": True,
        },
        "totals": totals.as_dict(),
        "by_role": {role: values.as_dict() for role, values in sorted(by_role.items())},
        "by_model": {model: values.as_dict() for model, values in sorted(by_model.items())},
        "records": records,
        "attribution": {
            "status": "available" if records["counted"] and not attribution_unavailable else (
                "partial" if records["counted"] else "unavailable"
            ),
            "counted_records": records["counted"],
            "unattributed_records": attribution_unavailable,
            "groups": sorted(by_role),
        },
        "accounting": {
            "status": "unavailable",
            "exact_billing": False,
            "message": "Transcript token observations do not provide exact billing reconciliation or a price schedule.",
        },
        "limitations": list(dict.fromkeys(limitations)),
    }
    return report


def render_text(report: Mapping[str, Any]) -> str:
    totals = report["totals"]
    records = report["records"]
    lines = [
        "Claude Workflow usage report (observational)",
        "",
        "Totals:",
    ]
    labels = {
        "input_tokens": "uncached input",
        "cache_read_input_tokens": "cache read input",
        "cache_creation_input_tokens": "cache creation input",
        "output_tokens": "output",
    }
    for metric in METRICS:
        value = totals.get(metric)
        lines.append(f"  {labels[metric]}: {value if value is not None else 'unavailable'}")
    lines.extend(
        [
            "",
            "Records:",
            f"  counted: {records['counted']}",
            f"  deduplicated: {records['deduplicated']}",
            f"  malformed lines: {records['malformed_lines']}",
            f"  outside time bounds: {records['outside_time_bounds']}",
            f"  ignored sidechain terminals: {records['ignored_sidechain_terminals']}",
            "",
            f"Attribution: {report['attribution']['status']} ({report['attribution']['unattributed_records']} unavailable)",
            "Accounting: unavailable (no exact billing reconciliation)",
        ]
    )
    limitations = report.get("limitations", [])
    if limitations:
        lines.append("Limitations:")
        lines.extend(f"  - {item}" for item in limitations)
    return "\n".join(lines) + "\n"


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", action="append", required=True, metavar="PATH", help="JSONL transcript path; repeat to select more than one")
    parser.add_argument("--since", metavar="ISO", help="inclusive ISO-8601 lower timestamp bound")
    parser.add_argument("--until", metavar="ISO", help="inclusive ISO-8601 upper timestamp bound")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        report = build_report(args.transcript, since=args.since, until=args.until)
    except ReportError as exc:
        print(f"usage report error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
