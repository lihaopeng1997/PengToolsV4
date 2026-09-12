"""DPI simulation in disposable child processes; never changes OS display settings."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--nav', type=int, default=7)
    parser.add_argument('--tab', type=int, default=None)
    parser.add_argument('--font', type=int, default=16)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, QT_QPA_PLATFORM='windows', PYTHONUTF8='1', QT_SCALE_FACTOR='1')
    calibration = subprocess.run([
        sys.executable, '-c',
        'from PyQt6.QtWidgets import QApplication; '
        'app=QApplication(["prism-dpi-calibration"]); '
        'print(app.primaryScreen().devicePixelRatio())',
    ], cwd=root, env=env, capture_output=True, text=True, check=True)
    native_dpr = float(calibration.stdout.strip())
    print(json.dumps({'native_dpr': native_dpr, 'font': args.font}), flush=True)
    for target in (1, 1.25, 1.5, 2):
        child_env = dict(env, QT_SCALE_FACTOR=str(target / native_dpr))
        command = [sys.executable, '-u', 'scripts/diagnostics/prism_web_runtime.py',
                   '--shell', '--width', '960', '--height', '640', '--nav', str(args.nav),
                   '--font', str(args.font), '--expected-dpr', str(target)]
        if args.tab is not None:
            command += ['--tab', str(args.tab)]
        subprocess.run(command, cwd=root, env=child_env, check=True, timeout=45)


if __name__ == '__main__':
    main()
