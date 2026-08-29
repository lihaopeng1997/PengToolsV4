"""Run each test module in an isolated process to contain PyQt6 global state."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


PROJECT_DIR = Path(__file__).resolve().parent.parent
TEST_DIR = PROJECT_DIR / "tests"


def _run_pytest(target: str, env: dict[str, str]) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", target],
        cwd=PROJECT_DIR,
        env=env,
        check=False,
    ).returncode


def _isolated_nodeids(path: Path, env: dict[str, str]) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(path)],
        cwd=PROJECT_DIR,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if "::" in line]


def main() -> int:
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    failed: list[str] = []
    test_files = sorted(TEST_DIR.glob("test_*.py"))
    for path in test_files:
        relative = path.relative_to(PROJECT_DIR)
        print(f"[test-suite] {relative}", flush=True)
        if _run_pytest(str(relative), env) == 0:
            continue
        nodeids = _isolated_nodeids(relative, env)
        if not nodeids:
            failed.append(str(relative))
            continue
        print(
            f"[test-suite] retrying {len(nodeids)} cases with process isolation",
            flush=True,
        )
        node_failures = [nodeid for nodeid in nodeids if _run_pytest(nodeid, env) != 0]
        if node_failures:
            failed.extend(node_failures)
        else:
            print(f"[test-suite] isolated PASS: {relative}", flush=True)
    if failed:
        print("[test-suite] FAILED:", *failed, sep="\n  - ")
        return 1
    print(f"[test-suite] PASS: {len(test_files)} modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
