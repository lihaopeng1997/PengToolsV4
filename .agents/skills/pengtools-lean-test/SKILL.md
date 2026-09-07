---
name: pengtools-lean-test
description: Choose and run the smallest meaningful verification for a PengToolsHub change. Use for code, UI, lifecycle, packaging, or release work; do not use for unrelated repositories.
---

# PengToolsHub lean verification

Match verification to the changed risk, then report the exact command and result. Do not treat a build or a structural assertion as proof of unrelated runtime or visual behavior.

| Change risk | Minimum evidence |
| --- | --- |
| Documentation or guidance | Diff review and `git diff --check` |
| Vue or presentation-only UI | Typecheck; build only when production assets change; user visual review remains separate |
| Python business or panel behavior | Relevant targeted tests; add a focused regression only when it protects a demonstrated bug |
| Runtime, lifecycle, IPC, WebEngine, SSH, capture | Targeted regression plus a minimal safe runtime smoke |
| Dependency, security, package, release | Task-specific isolated validation, audit, or package smoke |

- Do not run full discovery, package builds, or large GUI automation by default.
- Preserve unrelated existing failures as `EXISTING_FAILURE`; do not change unrelated product code to make them pass.
- Avoid tests that merely assert source text. Prefer observable behavior and the actual production callback or boundary when practical.
- For user-visible UI, state whether visual acceptance is still pending.
