from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "claude_workflow" / "plugin" / "scripts" / "usage_report.py"
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "usage"


class UsageReportTests(unittest.TestCase):
    def run_report(self, fixture: str, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--transcript", str(FIXTURE_ROOT / fixture), *extra, "--json"],
            cwd=Path("/"),
            check=False,
            capture_output=True,
            text=True,
        )

    def test_json_report_deduplicates_repeated_assistant_snapshot(self) -> None:
        result = self.run_report("duplicate_assistant.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["output_tokens"], 9)
        self.assertEqual(report["totals"]["input_tokens"], 11)
        self.assertEqual(report["totals"]["cache_read_input_tokens"], 4)
        self.assertEqual(report["totals"]["cache_creation_input_tokens"], 2)
        self.assertEqual(report["records"]["deduplicated"], 1)
        self.assertEqual(report["records"]["counted"], 1)

    def test_json_report_keeps_cache_categories_separate_and_reports_attribution(self) -> None:
        result = self.run_report("attributed_records.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["input_tokens"], 7)
        self.assertEqual(report["totals"]["uncached_input_tokens"], 7)
        self.assertEqual(report["totals"]["cache_read_input_tokens"], 3)
        self.assertEqual(report["totals"]["cache_creation_input_tokens"], 5)
        self.assertEqual(report["totals"]["output_tokens"], 12)
        self.assertEqual(report["by_role"]["cw-executor"]["output_tokens"], 8)
        self.assertEqual(report["attribution"]["unattributed_records"], 1)
        self.assertTrue(report["limitations"])
        self.assertTrue(any("incomplete" in item.lower() for item in report["limitations"]))

    def test_malformed_and_missing_usage_are_visible(self) -> None:
        result = self.run_report("malformed_and_missing.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["records"]["malformed_lines"], 1)
        self.assertEqual(report["records"]["without_usage"], 1)
        self.assertIsNone(report["totals"]["output_tokens"])
        self.assertTrue(any("malformed" in item.lower() for item in report["limitations"]))
        self.assertTrue(any("usage" in item.lower() for item in report["limitations"]))
        self.assertTrue(any("attribution" in item.lower() for item in report["limitations"]))
        self.assertEqual(report["accounting"]["status"], "unavailable")
        self.assertNotIn("cost_usd", report["totals"])

    def test_since_and_until_bounds_are_inclusive(self) -> None:
        result = self.run_report(
            "timestamp_bounds.jsonl",
            "--since",
            "2026-09-16T10:00:00Z",
            "--until",
            "2026-09-16T11:00:00Z",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["output_tokens"], 5)
        self.assertEqual(report["records"]["outside_time_bounds"], 2)

    def test_repeated_transcript_arguments_are_combined_and_deduplicated(self) -> None:
        fixture = str(FIXTURE_ROOT / "duplicate_assistant.jsonl")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--transcript", fixture, "--transcript", fixture, "--json"],
            cwd=Path("/"),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["output_tokens"], 9)
        self.assertEqual(report["records"]["transcripts"], 2)
        self.assertGreaterEqual(report["records"]["deduplicated"], 2)

    def test_whole_tree_model_usage_is_not_added_to_terminal_or_response_snapshots(self) -> None:
        result = self.run_report("aggregate_records.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["input_tokens"], 10)
        self.assertEqual(report["totals"]["output_tokens"], 6)
        self.assertEqual(report["by_model"]["claude-sonnet-4-6"]["output_tokens"], 6)
        self.assertGreaterEqual(report["records"]["aggregates_preferred"], 2)
        self.assertTrue(any("whole-tree" in item.lower() for item in report["limitations"]))

    def test_whole_tree_aggregate_suppresses_same_session_worker_file(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--transcript",
                str(FIXTURE_ROOT / "aggregate_cross_file_main.jsonl"),
                "--transcript",
                str(FIXTURE_ROOT / "aggregate_cross_file_worker.jsonl"),
                "--json",
            ],
            cwd=Path("/"),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["output_tokens"], 100)
        self.assertEqual(report["records"]["counted"], 1)
        self.assertGreaterEqual(report["records"]["aggregates_preferred"], 1)
        self.assertTrue(any("same session" in item.lower() for item in report["limitations"]))

    def test_whole_tree_aggregate_does_not_assume_unscoped_worker_is_disjoint(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--transcript",
                str(FIXTURE_ROOT / "aggregate_cross_file_main.jsonl"),
                "--transcript",
                str(FIXTURE_ROOT / "aggregate_cross_file_worker_unscoped.jsonl"),
                "--json",
            ],
            cwd=Path("/"),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["output_tokens"], 100)
        self.assertEqual(report["records"]["counted"], 1)
        self.assertEqual(report["records"]["ambiguous_scope_excluded"], 1)
        self.assertTrue(any("missing identity" in item.lower() for item in report["limitations"]))

    def test_unscoped_whole_tree_aggregate_does_not_assume_scoped_worker_is_disjoint(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--transcript",
                str(FIXTURE_ROOT / "aggregate_cross_file_main_unscoped.jsonl"),
                "--transcript",
                str(FIXTURE_ROOT / "aggregate_cross_file_worker.jsonl"),
                "--json",
            ],
            cwd=Path("/"),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["output_tokens"], 100)
        self.assertEqual(report["records"]["counted"], 1)
        self.assertEqual(report["records"]["ambiguous_scope_excluded"], 1)
        self.assertTrue(any("missing identity" in item.lower() for item in report["limitations"]))

    def test_later_cumulative_snapshot_does_not_hide_in_window_response(self) -> None:
        result = self.run_report(
            "cumulative_after_window.jsonl",
            "--until",
            "2026-09-16T09:30:00Z",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["output_tokens"], 20)
        self.assertEqual(report["records"]["counted"], 1)
        self.assertEqual(report["records"]["outside_time_bounds"], 2)
        self.assertEqual(report["records"]["cumulative_excluded_for_bounds"], 0)

    def test_in_window_cumulative_snapshot_is_not_treated_as_window_usage(self) -> None:
        result = self.run_report(
            "aggregate_records.jsonl",
            "--since",
            "2026-09-16T08:00:00Z",
            "--until",
            "2026-09-16T12:00:00Z",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["output_tokens"], 1)
        self.assertEqual(report["records"]["cumulative_excluded_for_bounds"], 2)
        self.assertTrue(any("cannot be sliced" in item.lower() for item in report["limitations"]))

    def test_sidechain_terminal_results_are_excluded_from_main_accounting(self) -> None:
        result = self.run_report("sidechain_terminal.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["output_tokens"], 5)
        self.assertEqual(report["records"]["ignored_sidechain_terminals"], 1)
        self.assertTrue(any("sidechain" in item.lower() for item in report["limitations"]))

    def test_model_usage_keeps_each_model_when_one_record_lists_multiple_models(self) -> None:
        result = self.run_report("multiple_models.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(set(report["by_model"]), {"claude-haiku-4-5", "claude-sonnet-4-6"})
        self.assertEqual(report["by_model"]["claude-haiku-4-5"]["output_tokens"], 2)
        self.assertEqual(report["by_model"]["claude-sonnet-4-6"]["output_tokens"], 4)

    def test_model_usage_identity_is_model_specific_even_when_terminal_has_message_id(self) -> None:
        result = self.run_report("aggregate_message_id.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["output_tokens"], 6)
        self.assertEqual(set(report["by_model"]), {"claude-haiku-4-5", "claude-sonnet-4-6"})

    def test_snapshot_fallback_identity_ignores_event_uuid_when_message_id_is_missing(self) -> None:
        result = self.run_report("fallback_snapshots.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["output_tokens"], 5)
        self.assertEqual(report["records"]["deduplicated"], 1)

    def test_api_response_body_and_otel_fields_are_read_with_message_uuid_attribution(self) -> None:
        result = self.run_report("api_response_and_otel.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["input_tokens"], 13)
        self.assertEqual(report["totals"]["output_tokens"], 7)
        self.assertEqual(report["records"]["deduplicated"], 1)
        self.assertIn("agent.custom", report["by_role"])

    def test_otel_assistant_response_message_uuid_deduplicates_transcript_entry(self) -> None:
        result = self.run_report("otel_message_uuid.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totals"]["output_tokens"], 4)
        self.assertEqual(report["records"]["deduplicated"], 1)

    def test_main_query_source_is_not_presented_as_a_role(self) -> None:
        result = self.run_report("main_query_source.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["by_role"], {})
        self.assertEqual(report["attribution"]["unattributed_records"], 1)
        self.assertTrue(any("attribution" in item.lower() for item in report["limitations"]))

    def test_missing_transcript_is_a_reported_cli_error(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--transcript", "/tmp/claude-workflow-does-not-exist.jsonl", "--json"],
            cwd=Path("/"),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("transcript", result.stderr.lower())

    def test_script_is_importable_from_any_working_directory(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            cwd=Path("/tmp"),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--transcript", result.stdout)


if __name__ == "__main__":
    unittest.main()
