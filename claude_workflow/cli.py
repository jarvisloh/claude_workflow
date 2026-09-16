"""Command line interface for :mod:`claude_workflow`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .lifecycle import (
    LifecycleError,
    bootstrap,
    check_update,
    configure,
    disable,
    enable,
    install,
    remove,
    report_command,
    status,
    update,
)
from .releases import ReleaseError, build_release, validate_plugin


def _project(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", required=True, type=Path, help="project directory to change")


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="json_output", help="write a JSON result")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-workflow", description="Manage a project-scoped Claude Workflow")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    command = commands.add_parser("bootstrap", help="install a checksum-verified release into a project")
    _project(command)
    command.add_argument("--package", required=True, help="local Claude Workflow release ZIP")
    command.add_argument("--sha256", "--checksum", dest="checksum", required=True, help="SHA256 for the release ZIP")
    _json_flag(command)

    command = commands.add_parser("install", help="install the bundled or supplied plugin")
    _project(command)
    command.add_argument("--source", help="plugin directory or checksum-verified ZIP archive")
    command.add_argument("--sha256", "--checksum", dest="checksum", help="SHA256 for an archive source")
    _json_flag(command)

    command = commands.add_parser("status", help="inspect installation and ownership integrity")
    _project(command)
    _json_flag(command)

    command = commands.add_parser("configure", help="configure route and model IDs")
    _project(command)
    command.add_argument("--route", choices=("light", "medium", "heavy"))
    command.add_argument(
        "--model",
        action="append",
        metavar="ROLE=MODEL_ID",
        help="set coordinator, worker, context, or senior model (repeatable)",
    )
    command.add_argument("--coordinator-model")
    command.add_argument("--worker-model")
    command.add_argument("--context-model")
    command.add_argument("--senior-model")
    _json_flag(command)

    for name, help_text in (("enable", "restore active project entry points"), ("disable", "remove active project entry points")):
        command = commands.add_parser(name, help=help_text)
        _project(command)
        _json_flag(command)

    command = commands.add_parser("remove", help="preview or apply removal")
    _project(command)
    command.add_argument("--apply", action="store_true", help="apply removal; preview is the default")
    _json_flag(command)

    command = commands.add_parser("check-update", help="compare an explicitly supplied source")
    _project(command)
    command.add_argument("--source", help="plugin directory or archive; omit for no-source report")
    command.add_argument("--sha256", "--checksum", dest="checksum", help="SHA256 for an archive source")
    _json_flag(command)

    command = commands.add_parser("update", help="update from an explicitly supplied source")
    _project(command)
    command.add_argument("--source", required=True, help="plugin directory or checksum-verified ZIP archive")
    command.add_argument("--sha256", "--checksum", dest="checksum", help="SHA256 for an archive source")
    _json_flag(command)

    command = commands.add_parser("report", help="report usage from explicitly selected JSONL transcripts")
    command.add_argument("--transcript", action="append", required=True, type=Path, help="JSONL transcript path (repeatable)")
    command.add_argument("--since", help="inclusive ISO timestamp bound")
    command.add_argument("--until", help="inclusive ISO timestamp bound")
    _json_flag(command)

    command = commands.add_parser("validate", help="validate a plugin source")
    command.add_argument("--source", help="plugin source; defaults to bundled plugin")
    command.add_argument("--sha256", "--checksum", dest="checksum", help="SHA256 for an archive source")
    command.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="allow a minimal source (useful while developing a plugin)",
    )
    command.add_argument("--complete", action="store_true", help=argparse.SUPPRESS)
    _json_flag(command)

    command = commands.add_parser("build-release", help="build a deterministic ZIP release")
    command.add_argument("--source", type=Path, help="plugin source; defaults to bundled plugin")
    command.add_argument("--output", required=True, type=Path, help="ZIP output path")
    _json_flag(command)
    return parser


def _models(args: argparse.Namespace) -> dict[str, str]:
    models: dict[str, str] = {}
    for item in args.model or []:
        if "=" not in item:
            raise LifecycleError("--model expects ROLE=MODEL_ID")
        role, model = item.split("=", 1)
        role = role.strip().lower()
        models[role] = model.strip()
    for role in ("coordinator", "worker", "context", "senior"):
        value = getattr(args, f"{role}_model")
        if value is not None:
            models[role] = value
    return models


def _print_result(result: Any, *, json_output: bool) -> None:
    if isinstance(result, str):
        if json_output:
            # Reports already own their JSON schema; preserve it when valid.
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                print(json.dumps({"report": result}))
            else:
                print(json.dumps(parsed, indent=2, sort_keys=True))
        else:
            sys.stdout.write(result)
            if result and not result.endswith("\n"):
                sys.stdout.write("\n")
        return
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    if isinstance(result, dict):
        status_value = result.get("status", "ok")
        print(f"{status_value}: {result.get('project', '')}".rstrip())
        for key in (
            "version",
            "managed_count",
            "active_count",
            "preserved_count",
            "config",
            "source",
            "update_available",
            "candidate_version",
            "source_kind",
            "reason",
            "path",
            "checksums",
        ):
            if key in result:
                print(f"{key}: {result[key]}")
        if "paths" in result:
            print("paths:")
            for path in result["paths"]:
                print(f"  {path}")
        if "preserved_paths" in result:
            print("preserved_paths:")
            for path in result["preserved_paths"]:
                print(f"  {path}")
        if "warnings" in result:
            print("warnings:")
            for warning in result["warnings"]:
                print(f"  {warning}")
        if "agent_actions" in result:
            print("agent_actions:")
            for action in result["agent_actions"]:
                print(f"  {action.get('task_id', 'action')}: {action.get('agent', 'worker')}")
        return
    print(result)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "bootstrap":
            result = bootstrap(args.project, package=args.package, checksum=args.checksum)
        elif args.command == "install":
            result = install(args.project, source=args.source, checksum=args.checksum)
        elif args.command == "status":
            result = status(args.project)
        elif args.command == "configure":
            result = configure(args.project, route=args.route, models=_models(args))
        elif args.command == "enable":
            result = enable(args.project)
        elif args.command == "disable":
            result = disable(args.project)
        elif args.command == "remove":
            result = remove(args.project, apply=args.apply)
        elif args.command == "check-update":
            result = check_update(args.project, source=args.source, checksum=args.checksum)
        elif args.command == "update":
            result = update(args.project, source=args.source, checksum=args.checksum)
        elif args.command == "report":
            result = report_command(
                transcripts=args.transcript,
                since=args.since,
                until=args.until,
                json_output=args.json_output,
            )
        elif args.command == "validate":
            source = args.source if args.source is not None else Path(__file__).parent / "plugin"
            result = validate_plugin(
                source,
                checksum=args.checksum,
                require_complete=(args.complete or not args.allow_incomplete),
            )
        elif args.command == "build-release":
            source = args.source if args.source is not None else Path(__file__).resolve().parent.parent
            result = build_release(source, args.output)
        else:  # argparse guarantees this branch is unreachable
            parser.error(f"unknown command {args.command}")
            return 2
        _print_result(result, json_output=args.json_output)
        return 0
    except (LifecycleError, ReleaseError, OSError, ValueError) as exc:
        if getattr(args, "json_output", False):
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
        else:
            print(f"claude-workflow: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
