# DeepSeek profile integration

**Baseline:** branch `dev/2026-09-23`, HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`, with the user's existing dirty worktree preserved. The exact pre-edit bytes for the seven existing owned files are archived under [`evidence/deepseek-profile-before-20261007`](evidence/deepseek-profile-before-20261007/manifest.json). No commit or push was made.

This change adds DeepSeek as an explicit per-profile provider through the existing encrypted model catalog and Settings page. Legacy profiles with no `provider` remain on the intranet route. An explicit unknown provider fails before credential decryption or transport. DeepSeek profiles require the exact HTTPS host `api.deepseek.com`, root or `/v1` path, port 443 or its default, and a typed `ssl_verify=True`. Requests use the default certificate-validating TLS context, bypass configured proxies, reject redirects, omit the intranet application tag, and require a locally entered API key before HTTP. New and copied profiles remain disabled unless the user enables them; switching providers or creating a new ID cannot inherit the old profile's token. Existing intranet probe/save behavior remains covered by the compatibility tests.

Settings now offers `DeepSeek` beside the existing intranet-compatible provider. Switching providers clears the credential and old model list, selects a provider-specific model suggestion (`deepseek-flash` or `qwen3.6`), and leaves the profile disabled with Agent capability `unknown`. A successful probe does not enable a DeepSeek profile or elevate Agent capability. The UI keeps the existing encrypted token field and lets the user enter a new key locally. No key was loaded, printed, or included in test data.

The change only selects a model transport per profile; it does not change SQL tools, database connectors, or data-center access policy.

DeepSeek thinking mode remains enabled on requests, including native `tools` requests when the profile explicitly declares `native_tools`. The streaming parser captures `reasoning_content` in a private bounded buffer; it never becomes a `ModelDelta`, public Agent field, event, or runner message. The model adapter caches it only after a complete terminal tool-call response with the field present (an empty string is valid), and binds it to the full immutable run context, selected profile/model, assistant content, and complete tool-call identity. It replays only the matching continuation in a copied outbound request. Missing reasoning, incomplete terminal frames, identity conflicts, or byte/exchange limits fail before tool execution. Retryable failures before stream start preserve already validated exchanges; terminal failure, cancellation, and runner completion release the run cache. Outbound history filtering removes only an uncached assistant tool-call and its matching tool result, preserving system/user/ordinary assistant messages and leaving the canonical history untouched.

The work is limited to these owned paths:

- `tools/intranet_llm.py`, `tools/agent_core/model_config.py`, `tools/agent_core/streaming.py`, `tools/agent_core/model_adapter.py`, `tools/agent_core/host_model_adapter.py`, `tools/agent_core/runner.py`, and `panels/settings_panel.py`.
- `tests/test_deepseek_profile_policy.py`, `tests/test_deepseek_private_reasoning.py`, and `tests/test_deepseek_settings_profile.py`.
- This report.

The protocol choices follow DeepSeek's current [Quick Start](https://api-docs.deepseek.com/quick_start), [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/), [Thinking Mode guide](https://api-docs.deepseek.com/guides/thinking_mode/), and [Models API](https://api-docs.deepseek.com/api/list-models/). These sources document the OpenAI-compatible base URL, current model IDs, thinking option, native tool-call fields, `reasoning_content`, and continuation replay for tool requests. Redirect refusal, proxy bypass, host allowlisting, and typed TLS enforcement are client safety policy, not vendor guarantees.

Verification on the preserved dirty checkout:

- `py_compile` passed for the seven owned Python sources and three new test modules.
- The focused suite passed: 74 tests across the three new modules, `tests.test_intranet_llm`, `tests.test_data_center_host_model_adapter`, `tests.test_data_center_streaming`, and `tests.test_programming_agent_runtime`.
- `AcceptanceCorrectionsTest.test_supports_vision_persistence_and_normalization` passed once with all mutable Settings/catalog/layout paths redirected to TEMP and harness project discovery stubbed.
- Four calm-theme compact Settings captures at 960×640 were inspected after the source freeze: `zh/en` at 17/21 px, under `C:\Users\Lenovo\AppData\Local\Temp\deepseek-settings-freeze-20261007-1aakseg8\`. They use a synthetic disabled profile and an empty token field. The 21 px view scrolls vertically as expected; the main form remains horizontally usable.

No DeepSeek account, real API key, real catalog, or network model request was used. A user must enter their own key in the new profile and explicitly enable it before using DeepSeek; real-provider behavior remains pending that user's local validation.
