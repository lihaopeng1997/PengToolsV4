"""Real isolated renders protect the log rail and both work modes from overflow."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LogLayoutTests(unittest.TestCase):
    def test_session_export_and_narrow_fit_without_overflow(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='prism-log-layout-') as directory:
            for width, tab in ((1144, 0), (1144, 1), (680, 0)):
                with self.subTest(width=width, tab=tab):
                    result = subprocess.run([
                        sys.executable, str(root / 'scripts/diagnostics/prism_preview.py'),
                        'logs', '--width', str(width), '--height', '740', '--tab', str(tab),
                        '--inspect-layout', '--output', str(Path(directory) / f'{width}-{tab}.png'),
                    ], cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    records = [json.loads(line) for line in result.stdout.splitlines()
                               if line.startswith('{')]
                    self.assertTrue(records)
                    self.assertFalse([row for row in records if 'overflow' in row], records)
                    self.assertEqual((records[-1]['width'], records[-1]['height']), (width, 740))


if __name__ == '__main__':
    unittest.main()
