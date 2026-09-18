# -*- coding: utf-8 -*-
"""Run the isolated real-Windows Prism native menu runtime probe.

The probe loads the checked-in production Chrome bundle in QWebEngineView at
72px and 248px, maps the WebView-local menu anchor into a dummy host window,
and captures the native popup.  It never imports ``main_window``, creates a
business panel, reads user configuration, or contacts a remote URL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_prism_native_menu_runtime import (  # noqa: E402
    NativeMenuProbeUnavailable,
    run_native_menu_probe,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_capture = ROOT / 'scripts' / 'diagnostics' / 'prism_native_menu_runtime'
    parser.add_argument(
        '--capture-dir',
        default=str(default_capture),
        help='保存实际 WebView/原生弹层截图与 report.json 的目录',
    )
    parser.add_argument(
        '--no-capture',
        action='store_true',
        help='只运行行为诊断，不保存截图',
    )
    args = parser.parse_args(argv)
    try:
        report = run_native_menu_probe(capture_dir=None if args.no_capture else args.capture_dir)
    except NativeMenuProbeUnavailable as exc:
        print(f'PENDING_ENVIRONMENT: {exc}')
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
