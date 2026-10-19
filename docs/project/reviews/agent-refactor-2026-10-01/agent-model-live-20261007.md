# Agent workbench live-model boundary receipt

Date: 2026-10-07 (Asia/Shanghai)

## Outcome

This run reached the Agent Workbench’s ordinary model-selection gate and stopped because the existing `list_enabled_items()` path supplied no enabled model profile with a Base URL. The selected model was therefore unavailable in the workbench. The private-host check was not reached; the host adapter and model transport were not constructed; model turns and HTTP open attempts were both zero. No live model request, file read by the Agent, generated preview, confirmation, or patch application occurred. The result does not establish whether a configured endpoint is reachable or whether the full live workflow succeeds.

The harness did not alter model settings or production sessions. `config.save_settings` and config-directory creation were disabled for the run, layout persistence was redirected to TEMP, and all AgentStore paths pointed to the synthetic TEMP root. The final harness run removed its TEMP root after collecting the synthetic evidence.

## Public route examined

Static source inspection located this route:

1. `panels.agent_workbench_panel.AgentWorkbenchPanel._send` reads the selected profile and starts `_WorkbenchWorker`.
2. `_WorkbenchWorker.run` calls `tools.agent_runtime.run_agent_loop`.
3. With no injected host adapter, `run_agent_loop` constructs `tools.agent_core.AgentModelHostAdapter` using the selected profile ID.
4. The host adapter resolves the selected profile through `tools.intranet_llm._cfg_for_call` and delegates turns to the streaming `ModelAdapter` and `UrlLibStreamingTransport`.

The normal workbench gate returned no enabled profile, so this run did not reach steps 3–4. The acceptance runner was prepared to use the existing workbench adapter seam with a real `AgentModelHostAdapter`, but did not use it after the gate stopped the task.

## Verification

`python -m unittest tests.test_programming_agent_runtime tests.test_agent_workbench_runtime -v` passed: 18 tests, 0 skipped. These tests exercise scripted adapters and workbench lifecycle behavior; they do not demonstrate a live model call.

The TEMP arithmetic project began with `calc.add(2, 3)` incorrectly returning `-1`. Its one `unittest` case failed as expected (`AssertionError: -1 != 5`; 1 test, 0 skipped). The final test was not run because no model patch was applied. No TEMP patch was authorized by a model preview, and the final synthetic diff is empty.

## Run bounds and evidence

The live runner was bounded to 20 seconds per model turn, at most one extra retry for retryable transport failures, three Agent turns, and a 72-second wall deadline. The model call path was never entered, so none of those bounds or retry behavior was exercised.

The initial and final branch remained `dev/2026-09-23`; HEAD remained `818fba72bf437de9bd49fcd5b198fb1270096a53`. SHA-256 values for the inspected route, adapter, configuration-loader, confirmation-dialog, and targeted test files match before and after. See [source_hashes_before.json](evidence/agent-model-live-20261007/source_hashes_before.json), [source_hashes_after.json](evidence/agent-model-live-20261007/source_hashes_after.json), and the [safe receipt](evidence/agent-model-live-20261007/receipt.json).

Evidence files are under [evidence/agent-model-live-20261007](evidence/agent-model-live-20261007/):

- `run_agent_model_live.py` is the TEMP-only, redacting runner. It records no config values, endpoint, credentials, request body, or raw exception text.
- `receipt.json` and `safe_run.log` record the selected-profile gate, zero model/HTTP attempts, timing, and stable hashes.
- `targeted_regression.log` records the 18-test scripted-adapter regression result.
- `synthetic/before/` and `synthetic/after/` preserve the synthetic source and unittest fixture; `synthetic/final.diff` is empty because the live workflow did not apply a change.
- `unittest_before.log` records the expected synthetic baseline failure; `unittest_after.log` records that verification was not run because no TEMP patch was applied.

No product source or existing tests were edited. The broader real-model read → diff preview → explicit TEMP apply → unittest acceptance remains unverified until the ordinary workbench loader exposes an enabled profile.
