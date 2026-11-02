import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import batch_port


class BatchPortTests(unittest.TestCase):
    def make_project(self, root):
        paths = [
            "input/android/map_hok_andr/Resources/1.64.1/assetbundle/scene/lv3.assetbundle",
            "input/android/map_hok_andr/Resources/1.64.1/assetbundle/ui/map.assetbundle",
            "input/android/map_hok_andr/Extra/assetbundle/scene/lv5.assetbundle",
            "input/ios/map_ios_orgi/Resources/1.64.1/assetbundle/ui/map.assetbundle",
            "input/ios/map_ios_orgi/Extra/assetbundle/scene/lv3.assetbundle",
            "input/ios/map_ios_orgi/Extra/assetbundle/scene/lv5.assetbundle",
            "input/hok_sence_ios/scene/lv3.assetbundle",
        ]
        for item in paths:
            path = root / item
            path.parent.mkdir(parents=True, exist_ok=True)
        (root / paths[0]).write_bytes(b"same-scene")
        (root / paths[1]).write_bytes(b"android-map")
        (root / paths[2]).write_bytes(b"android-lv5")
        (root / paths[3]).write_bytes(b"ios-map")
        (root / paths[4]).write_bytes(b"same-scene")
        (root / paths[5]).write_bytes(b"ios-lv5")
        (root / paths[6]).write_bytes(b"reference-scene-is-not-the-same")

    def run_tool(self, root, *args):
        with patch("sys.argv", ["batch_port.py", "--base", str(root), *args]):
            batch_port.main()
        return json.loads((root / "output/batch_port_report.json").read_text())

    def test_cross_tier_exact_match_is_staged_to_ios_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_project(root)
            report = self.run_tool(root, "--stage-identical")
            rows = {
                row["source_path"]: row
                for row in report["mapping"]
                if row.get("source_path")
            }
            scene = rows["assetbundle/scene/lv3.assetbundle"]
            self.assertEqual(scene["status"], "exact_identical")
            self.assertEqual(scene["target_root"], "ios_extra")
            staged = root / "output/ios_candidate/Extra/assetbundle/scene/lv3.assetbundle"
            self.assertEqual(staged.read_bytes(), b"same-scene")

    def test_nonidentical_same_path_is_never_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_project(root)
            report = self.run_tool(root, "--stage-identical")
            rows = {
                row["source_path"]: row
                for row in report["mapping"]
                if row.get("source_root") == "android_resources"
            }
            row = rows["assetbundle/ui/map.assetbundle"]
            self.assertEqual(row["status"], "format_specific_conversion_required")
            self.assertFalse((root / "output/ios_candidate/Resources/1.64.1/assetbundle/ui/map.assetbundle").exists())

    def test_reference_candidate_does_not_override_same_path_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_project(root)
            report = self.run_tool(root)
            rows = [
                row for row in report["mapping"]
                if row.get("source_root") == "android_extra"
            ]
            row = next(x for x in rows if x["source_path"] == "assetbundle/scene/lv5.assetbundle")
            self.assertEqual(row["status"], "format_specific_conversion_required")
            self.assertTrue(row["reference_candidates"])

    def test_sources_are_not_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_project(root)
            before = (root / "input/android/map_hok_andr/Resources/1.64.1/assetbundle/scene/lv3.assetbundle").read_bytes()
            self.run_tool(root, "--stage-identical")
            after = (root / "input/android/map_hok_andr/Resources/1.64.1/assetbundle/scene/lv3.assetbundle").read_bytes()
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
