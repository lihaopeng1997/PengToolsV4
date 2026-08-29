"""Static guards for product network and release boundaries."""

from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_DIR = Path(__file__).resolve().parent.parent


class RuntimeBoundaryTests(unittest.TestCase):
    def test_capture_probe_is_loopback_only(self):
        source = (PROJECT_DIR / "panels" / "interface_debug_panel.py").read_text(
            encoding="utf-8"
        )
        start = source.index("    def _probe_capture_pipeline")
        end = source.index("    def _mark_listen_success", start)
        probe = source[start:end]
        self.assertIn("127.0.0.1", probe)
        self.assertNotIn("example.com", probe)
        self.assertNotIn("urllib", probe)

    def test_capture_disables_vulnerable_http2_path(self):
        source = (PROJECT_DIR / "tools" / "http_capture.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("('http2', False)", source)


if __name__ == "__main__":
    unittest.main()
