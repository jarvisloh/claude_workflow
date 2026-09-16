import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from claude_workflow.releases import (
    ReleaseError,
    build_release,
    compare_versions,
    validate_plugin,
)

from test_lifecycle import make_plugin


class ReleaseTests(unittest.TestCase):
    def test_validate_plugin_and_deterministic_release(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            source = make_plugin(work / "source")
            report = validate_plugin(source)
            self.assertEqual(report["name"], "claude-workflow")
            one = work / "one.zip"
            two = work / "two.zip"
            first = build_release(source, one)
            second = build_release(source, two)
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertEqual(one.read_bytes(), two.read_bytes())
            with zipfile.ZipFile(one) as archive:
                self.assertIn("SHA256SUMS", archive.namelist())
                sums = archive.read("SHA256SUMS").decode()
                self.assertIn("plugin/.claude-plugin/plugin.json", sums)

    def test_archive_checksum_and_path_validation(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            source = make_plugin(work / "source")
            archive = work / "release.zip"
            build_release(source, archive)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(validate_plugin(archive, checksum=digest)["name"], "claude-workflow")
            with self.assertRaises(ReleaseError):
                validate_plugin(archive, checksum="0" * 64)
            bad = work / "bad.zip"
            with zipfile.ZipFile(bad, "w") as zf:
                zf.writestr("../escape", "x")
            with self.assertRaises(ReleaseError):
                validate_plugin(bad)

    def test_semver_comparison(self):
        self.assertLess(compare_versions("0.1.0", "0.2.0"), 0)
        self.assertEqual(compare_versions("v1.0.0", "1.0.0"), 0)
        self.assertLess(compare_versions("1.0.0-rc.1", "1.0.0"), 0)


if __name__ == "__main__":
    unittest.main()
