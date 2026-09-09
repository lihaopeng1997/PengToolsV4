"""The SQL workspace scrolls inside its tab instead of widening the main window."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ReleaseLayoutTests(unittest.TestCase):
    def test_processing_tab_fits_wide_and_narrow(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='prism-release-layout-') as directory:
            for width in (680, 1144):
                with self.subTest(width=width):
                    result = subprocess.run([
                        sys.executable, str(root / 'scripts/diagnostics/prism_preview.py'),
                        'release', '--width', str(width), '--height', '740', '--tab', '1',
                        '--inspect-layout', '--output', str(Path(directory) / f'{width}.png'),
                    ], cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    records = [json.loads(line) for line in result.stdout.splitlines()
                               if line.startswith('{')]
                    self.assertFalse([row for row in records if 'overflow' in row], records)
                    self.assertEqual((records[-1]['width'], records[-1]['height']), (width, 740))


if __name__ == '__main__':
    unittest.main()
