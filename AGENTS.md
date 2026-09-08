# PengToolsHub engineering guidance

This file covers durable repository conventions. The user task and current source are authoritative for task scope and dynamic facts.

## Project handoff and current UI decisions

- Requirements and development agents start at `docs/project/README.md`; use its product/source map and collaboration protocol. Historical handoffs are context, not automatic current authority.
- The user confirmed the single Prism `calm` theme. Follow the V2.1 specification linked there; do not restore the superseded dual-theme selector.
- Report actual branch/SHA and distinguish current source behavior, approved targets, runtime evidence, and pending visual acceptance. Git-only agents cannot see uncommitted local changes.

## Start each task

- Read this file, inspect `git status --short --branch`, and read only the source needed for the task.
- If `.workbuddy/memory/project_memory.json` exists and is relevant, read it. Its absence is not a blocker. Do not store or synchronize credentials in repository guidance.
- For a task that names a branch, base commit, or review target, verify that exact target. Do not switch to `main`, pull, merge, rebase, reset, stash, or clean merely to make a checkout look uniform.
- Before a commit or push, fetch the relevant remote branch and stop if it moved in a way that needs integration. Never force-push without explicit user instruction.

## Architecture

```text
run -> main_window -> panels -> tools / ui / config
```

- `run.py` owns application startup and shutdown.
- `main_window.py` owns navigation, panel lifecycle, and cross-module wiring.
- `panels/` owns page-level workflow composition.
- `tools/` contains non-QWidget business logic and must not import `ui` or `panels`.
- `ui/` provides reusable presentation and adapters; it must not import `panels`.
- Keep Bridge code in `ui/`, inject business data through callers, and keep Python and TypeScript DTOs aligned.

## Hybrid UI

- Vue 3/TypeScript/Vite is for Chrome, sidebar, dashboard, and other lightweight global presentation.
- PyQt widgets remain the default for database, SQL, SSH/terminal, capture, file/system interaction, and dense workbenches.
- The authoritative sources are `ui/theme_manager.py` for semantic theme tokens, `ui/layout_metrics.py` for responsive modes, and `ui/navigation_model.py` for navigation.
- Frontend code must not directly access local files, databases, or business Python; do not load CDN assets or add a public service.
- Vue source belongs in `frontend/`; regenerate `resources/webui/vue/` from the source in the same commit. Do not hand-edit bundles.

## Scope, safety, and verification

- Make only changes traceable to the current task. Report unrelated defects instead of repairing them.
- Do not alter user `data`, real configuration, credentials, keys, captured payloads, or existing user sessions.
- Choose focused verification for the changed risk. Do not run full discovery, package builds, or disruptive GUI automation unless the task requires them.
- For Python changes, run the relevant `python -m unittest tests.test_<area> -v` target. For frontend changes, use `npm --prefix frontend run typecheck`; run `build:embedded` and `verify:embedded` when embedded assets change.
- Changes to startup, WebEngine policy, single-instance ownership, packaging, user-data paths, navigation authority, bridge contracts, or SSH session ownership need explicit task scope.

## Delivery

- One logical task normally produces one reviewable commit. Before delivery inspect the diff, run `git diff --check`, and verify the working tree.
- Push only to the user-specified branch. Never push `main` unless that is explicitly requested.
- Report the result, commit, changed files, verification, and meaningful limitations. Do not claim automated or visual verification that did not occur.
