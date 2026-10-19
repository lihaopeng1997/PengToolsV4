# UI5 final package audit and prior UI package history — 2026-09-30

## UI5 final isolated package

The root gave GO after the Gateway layout and geometry review, intrinsic-text and icon checks, and focused regressions passed. The reviewed UI5 wrapper ran once. The unchanged official builder completed successfully in a fresh isolated target. This agent did not launch the app; root owns the separate runtime smoke.

- Target: D:\PengTools\build_new_20260930_UI5
- Source identity: dev/2026-09-23 at 818fba72bf437de9bd49fcd5b198fb1270096a53; dirty source snapshot
- Frozen Gateway source SHA-256: A519EB515B639AD1E379F19B39B9FC766B26D1F75B6D2B113F333B9C8482C7D7
- Frozen main_window.py SHA-256: C21AE9A4913683A32A25E040A476386DF80F7E2CE6CFC8307E843C311C43F4E0
- Source manifest: D:\PengTools\build_new_20260930_UI5\source_snapshot.json
- Manifest inputs: 258 approved paths; user_data_copied=false
- Package result: D:\PengTools\build_new_20260930_UI5\package_result.json
- Archive: D:\PengTools\build_new_20260930_UI5\PengToolsHub_UI5_20260930.zip
- Archive SHA-256: c0718f95b0fe5a6ef55bbc950996fe5f047c0964a1724d07ef726d5a75f9a0f0
- Dist executable: D:\PengTools\build_new_20260930_UI5\source\dist\PengToolsHub\PengToolsHub.exe
- Installer executable: D:\PengTools\build_new_20260930_UI5\source\Installer\PengToolsHub\PengToolsHub.exe
- Matching executable SHA-256: cd1939951f906b3f2370d284fe083aa7ac2a91814c272c2ec6d61377b9fca4e4

Post-build verification found zero live-source hash mismatches across the 258 inputs, zero missing whitelist files, zero copied-input hash mismatches except the expected isolated build stamp, and zero unexpected non-build source files. All 13 root-protected hashes match. The ZIP has 2,068 entries, zero paths with a data component, and zero bytecode/cache entries. The builder created three incidental __pycache__ files under the isolated source; they are outside the approved input list and absent from the ZIP.

Only the isolated resources/build_info.json was stamped by the builder. Its isolated hash is 8e21a5f73cc4f89e0dd6ee7542f545e9d7cf2c737199b4cc029d454e0bad5041. The live workspace copy matches the GO-time snapshot hash 254403bd9fc39440a67b1b75e6001758123255a810848643e1e24a8e3490c63f. The wrapper copied only approved files from the live workspace, not from previous package outputs. It did not modify UI2/UI3/UI4/UI4 Final, their runtime data, workspace data, or a user application session.

## UI5 build gates and runtime boundary

The official isolated builder exited 0. Its dependency check reported “No broken requirements found.” Its configured release scanner roots, resources and packaging, reported zero high-risk findings, zero warnings, and PASS. The build transcript is C:\Users\Lenovo\AppData\Local\Temp\pengtools-ui5-prep-20260930-3f24eb6f476341369a3cc37a\ui5-build.log.

PyInstaller emitted non-fatal unresolved-library warnings for platform/system dependencies and optional Qt components. The list is D:\PengTools\build_new_20260930_UI5\source\build\PengToolsHub\warn-PengToolsHub.txt. No broader scanner was rerun for UI5, and no archive-wide scanner-clean claim is made. The earlier UI4 broader-scope finding remains documented below. No release upload, signing, installation, or distribution approval is claimed. Root's separate UI5 runtime smoke is outside this build audit; the package build itself is not runtime-start evidence.

## UI5 wrapper provenance

The wrapper used was C:\Users\Lenovo\AppData\Local\Temp\pengtools-ui5-prep-20260930-3f24eb6f476341369a3cc37a\package_after_go.ps1. Its preparation plan, with the exact 258-path whitelist and no pre-GO live hash snapshot, is C:\Users\Lenovo\AppData\Local\Temp\pengtools-ui5-prep-20260930-3f24eb6f476341369a3cc37a\ui5-whitelist-plan.json. Before GO it was syntax-parsed with zero errors and checked to require -Approval GO, reject any existing UI5 target, exclude all prior package roots and archive names, and preserve the 13 protected files. At GO it captured live hashes, copied only approved paths from D:\PengTools, verified copied/live source state, and audited the archive and executable hashes.

## Earlier UI4 package history and scanner scope

UI4 Final remains preserved at D:\PengTools\build_new_20260930_UI4_Final\PengToolsHub_UI4_Final_20260930.zip with SHA-256 93a8911f6afb13334a5b2e664814be3d59a9c82e7f512aff3daaf4ed431efc9b; its matching executable SHA-256 was 1da63d45e7811eae6455b38ed9764c75e5c4868546eab5a15b98d65a9d8f3aed. After its package audit, root reported the existing smoke entry displayed Web Chrome/dashboard, then its timer exited; a direct sky launch failed. The UI4 Final archive and its smoke-generated runtime data remain preserved. It predates the Gateway layout fix. The earlier UI4 archive remains at D:\PengTools\build_new_20260930_UI4\PengToolsHub_UI4_20260930.zip with SHA-256 43da92113f232e9aac857e0e405e032ff5a40621152055c4ae4355c2b35a9ce2 and predates the English cold-start fix. The UI4 and UI4 Final archive hashes were rechecked unchanged during UI5 audit.

A broader default scanner run on the earlier UI4 build included generated Installer and PrivateInstaller and returned nine high-risk regex matches plus one warning. Eight password-assignment matches and one private-key-marker match were in cryptography, msoffcrypto metadata, and oracledb files under Installer\PengToolsHub\_internal; the warning was VPN text in mitmproxy's onboarding template. All ten matched files were byte-identical to the corresponding approved .venv-build\Lib\site-packages dependency files and are present in the ZIP. Scanner samples were not retained or reproduced. This is a scanner-scope finding, not a suppressed result or a clean full-archive scan. No broader scan was rerun for UI5.

## UI review status

The screenshot review found the Gateway embedded-JSON toolbar labels/path field overlap at English 12/16 in horizontal layout. The module's narrow container/layout fix, geometry, intrinsic-fit and icon checks, and seven focused regressions were reviewed before UI5 freeze; native zh/en at 12/16 across four tested sizes reported zero intrinsic-fit or path-overlap failures. The shared JsonViewer was untouched. See the shell and NoSQL reports in docs/project/reviews/ui-completeness-2026-09-30/shell-dialogs.md and docs/project/reviews/ui-completeness-2026-09-30/nosql-loading.md. Physical multi-monitor/DPI/Snap/transparency acceptance and live-service latency are not inferred from these checks.
