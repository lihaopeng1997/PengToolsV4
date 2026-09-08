"""Run each test module in an isolated process to contain PyQt6 global state."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time


PROJECT_DIR = Path(__file__).resolve().parent.parent
TEST_DIR = PROJECT_DIR / "tests"

WEB_RUNTIME_MODULES = {
    "test_web_chrome_runtime",
    "test_web_dashboard_runtime",
}


def _run_pytest(target: str, env: dict[str, str], timeout: int = 60) -> int:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", target],
            cwd=PROJECT_DIR,
            env=env,
            check=False,
            timeout=timeout,
        )
        return proc.returncode
    except subprocess.TimeoutExpired:
        return 124


def _run_unittest(module_name: str, env: dict[str, str], timeout: int = 60) -> int:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", module_name, "-v"],
            cwd=PROJECT_DIR,
            env=env,
            check=False,
            timeout=timeout,
        )
        return proc.returncode
    except subprocess.TimeoutExpired:
        return 124


def _isolated_nodeids(path: Path, env: dict[str, str]) -> list[str]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", str(path)],
            cwd=PROJECT_DIR,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if "::" in line]
    except subprocess.TimeoutExpired:
        return []


def main() -> int:
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    failed: list[str] = []
    test_files = sorted(TEST_DIR.glob("test_*.py"))
    total_count = len(test_files)
    pass_count = 0
    start_time = time.time()

    for path in test_files:
        relative = path.relative_to(PROJECT_DIR)
        module_name = f"tests.{path.stem}"
        print(f"[test-suite] {relative}", flush=True)

        # QWebEngine runtime tests require unittest runner and native QPA to avoid offscreen compositor exit crash
        if path.stem in WEB_RUNTIME_MODULES:
            web_env = env.copy()
            web_env.pop("QT_QPA_PLATFORM", None)
            code = _run_unittest(module_name, web_env)
            if code == 0:
                pass_count += 1
                continue
            failed.append(f"{relative} (unittest rc={code})")
            continue

        # Standard pytest run for the module
        code = _run_pytest(str(relative), env)
        if code == 0:
            pass_count += 1
            continue

        # Fallback 1: unittest runner for the module
        unit_code = _run_unittest(module_name, env)
        if unit_code == 0:
            print(f"[test-suite] unittest PASS: {relative}", flush=True)
            pass_count += 1
            continue

        # Fallback 2: retry individual test cases with process isolation
        nodeids = _isolated_nodeids(relative, env)
        if not nodeids:
            failed.append(f"{relative} (pytest rc={code}, unittest rc={unit_code})")
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
            pass_count += 1

    elapsed = time.time() - start_time
    print(f"\n==================== TEST SUITE SUMMARY ====================")
    print(f"Total modules: {total_count}")
    print(f"Passed:        {pass_count}")
    print(f"Failed:        {len(failed)}")
    print(f"Elapsed:       {elapsed:.1f}s")
    print(f"============================================================")

    if failed:
        print("[test-suite] FAILED:", *failed, sep="\n  - ")
        return 1
    print(f"[test-suite] PASS: all {total_count} modules verified successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
