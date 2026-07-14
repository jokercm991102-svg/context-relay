# Context Relay V2: Fixed-input V1/V2 validation

Date: 2026-07-14

## Verdict

The fixed scanner and generated-bundle comparison passes every deterministic acceptance threshold. V1 recovered `0/3` objective elements, while V2 recovered `3/3`, recorded the confirmation target as `V2 規格` with status `approved`, and no longer instructed the next task to ask for the goal. V2's original median scanner time was `0.190960` seconds, with `39.889%` overhead over V1, and the target remained unchanged. A post-fix scanner-only rerun also passed at `0/3` versus `3/3`, with a V2 median of `0.221369` seconds, `49.153%` overhead, and an unchanged target.

The isolated receiver comparison is `UNVERIFIED`. An external-data authorization boundary prevented the required service call, and no alternate route was used. The exact harness retained `scanner-results.json` but did not produce `handoff-results.json`; its generic reason was `error: one or more receiver runs failed`. Receiver behavior, wall time, token counts, and tool calls are therefore not claimed.

## Frozen input and implementations

- Approved snapshot SHA-256: `3c5bec8e95eebb5c7a07e977213960c066e00b9c077be6ba0d511c6942d94723`.
- Snapshot size: `3,709,194` bytes across `1,557` JSONL lines.
- Approved ground-truth sentence: `建立下版功能並核准實測，最好能測出優化的差距`.
- V1 source and shared read-only target: detached commit `b09e5f55df21ed9804f88d4e6f22ecb6edfa3629`.
- V2 implementation under test: branch `codex/context-relay-v2` at pre-documentation commit `e5a42c470007a9f99154f653da19abe58547c79f`.

Both scanners received the same frozen snapshot and scanned the same detached target. The harness requested V1 and then V2 receiver runs with the installed Codex executable, an ephemeral task, `--ignore-user-config`, a read-only sandbox, the same prompt, and the same response schema. Local paths are represented here only by role labels such as `$FIXED_SESSION`, `$V1_TARGET`, and `$OUTPUT_DIR`.

## Scanner measurements

| Version | Run 1 (s) | Run 2 (s) | Run 3 (s) | Median (s) | Median overhead | Final bundle bytes | Objective completeness |
|---|---:|---:|---:|---:|---:|---:|---:|
| V1 | 2.543843 | 0.134234 | 0.136508 | 0.136508 | baseline | 9,430 | 0/3 |
| V2 | 0.190804 | 0.190960 | 0.191863 | 0.190960 | 39.889% | 11,505 | 3/3 |

The overhead calculation is `(0.190960 - 0.136508) / 0.136508 × 100 = 39.889%`. Bundle bytes are the total regular-file bytes in the final bundle selected by the harness. The first V1 repetition was substantially slower than its later two repetitions; the specified median limits its effect on the comparison.

## Post-fix scanner-only rerun

After the whole-branch remediation through commit `20d045f2e4dd5267f735e40a56fe21314a5468b3`, the frozen input was independently rechecked against its recorded SHA-256, byte count, and line count. The V1 target was independently rechecked at the exact detached HEAD with empty raw porcelain status. The harness then ran only the local V1 and V2 scanners into a fresh `$POST_FIX_OUTPUT`; no receiver executable, response schema, or receiver prompt was supplied.

| Version | Run 1 (s) | Run 2 (s) | Run 3 (s) | Median (s) | Median overhead | Final bundle bytes | Objective completeness |
|---|---:|---:|---:|---:|---:|---:|---:|
| V1 | 0.149514 | 0.148406 | 0.148417 | 0.148417 | baseline | 9,431 | 0/3 |
| V2 | 0.220954 | 0.222382 | 0.221369 | 0.221369 | 49.153% | 16,187 | 3/3 |

The post-fix overhead calculation is `(0.221369 - 0.148417) / 0.148417 × 100 = 49.153%`. The scanner-only harness exited `0`, retained `scanner-results.json`, recorded `target_unchanged: true`, and intentionally produced no `handoff-results.json`. Both selected manifests recorded equal before/after HEAD values, empty status arrays, `target_unchanged: true`, and `stale: false`. An independent post-run Git check again found the exact detached HEAD and empty raw porcelain. Receiver behavior and receiver-side cost remain `UNVERIFIED`.

## Semantic handoff result

The completeness rubric checks three elements: building the next-version feature, performing real validation, and comparing the improvement.

| Generated-bundle criterion | V1 | V2 | Result |
|---|---|---|---|
| Objective completeness | 0/3 | 3/3 | PASS |
| Goal must be restated | Yes; the checkpoint instructs the next task to ask | No; `Requires confirmation: no` | PASS |
| Confirmation target | Not established | `V2 規格` | PASS |
| Confirmation status | Not established | `approved` | PASS |

These are deterministic scanner and bundle observations. A receiver must independently read the four permitted handoff files before receiver behavior can be claimed.

## Isolated receiver observations

No structured receiver result was available for either version. The table keeps every unavailable observation explicit instead of substituting estimates or an answer from the active task.

| Version | Wall time | Tool calls | Input tokens | Cached input tokens | Output tokens | Structured handoff |
|---|---:|---:|---:|---:|---:|---|
| V1 | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| V2 | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |

If available, receiver wall time, token counts, and tool calls would be single-execution observations, not statistical evidence about general latency. None were available here.

## Read-only target proof

| Evidence | Before | After | Result |
|---|---|---|---|
| Raw branch token | `HEAD` (detached) | `HEAD` (detached) | exact match |
| HEAD | `b09e5f55df21ed9804f88d4e6f22ecb6edfa3629` | `b09e5f55df21ed9804f88d4e6f22ecb6edfa3629` | exact match |
| Raw porcelain status | empty | empty | exact match |

The harness recorded `target_unchanged: true`. Both selected bundle manifests also recorded identical before/after HEAD values, empty status arrays, `target_unchanged: true`, and `stale: false`.

## Automated tests

| Implementation | Phase | Result |
|---|---|---|
| V1 | Before A/B | 15 passed, 0 failures, 0 errors |
| V2 | Before A/B | 175 passed, 0 failures, 0 errors |
| V2 | Final documentation verification | 175 passed, 0 failures, 0 errors |
| V2 | Post-fix whole-branch verification | 201 passed, 0 failures, 0 errors |
| Plugin MVP | Final whole-branch verification | 247 passed, 0 failures, 0 errors |

## Acceptance table

| Criterion | Status | Evidence |
|---|---|---|
| Frozen input is exact | PASS | SHA-256, `3,709,194` bytes, and `1,557` lines independently matched |
| Both implementations pass before the original comparison | PASS | V1 `15/15`; V2 `175/175` |
| Post-fix V2 suite remains green | PASS | V2 `201/201` |
| Final Plugin MVP suite remains green | PASS | Plugin MVP `247/247` |
| Objective recovery improves from 0/3 to 3/3 | PASS | Final checkpoint rubric scores |
| V1 asks for the goal and V2 does not | PASS | Generated checkpoint instructions |
| V2 identifies `V2 規格` as approved | PASS | V2 confirmation state |
| V2 median scanner time is below one second | PASS | Original `0.190960` seconds; post-fix `0.221369` seconds |
| V2 median overhead is below 100% | PASS | Original `39.889%`; post-fix `49.153%` |
| Target branch, HEAD, and raw status remain exact | PASS | Independent Git checks and harness/manifests |
| Fresh isolated receivers reproduce the semantic difference | UNVERIFIED | Harness reason: `error: one or more receiver runs failed` |
| Receiver timing, tokens, and tool calls establish general latency | UNVERIFIED | No measurements were available; even one observation per version could not establish causality |
| One fixed A/B proves general model-latency causality | UNVERIFIED | The design does not isolate all platform and service variables |

## Limitations

- This is one approved snapshot, one repository state, and three scanner repetitions per version. It demonstrates the measured handoff difference for this fixture, not universal semantic accuracy.
- Scanner timings include local process and filesystem effects. The V1 cold-run outlier shows why the specified median, rather than a single timing, is reported.
- The objective score is a fixed three-element rubric, not an open-ended quality evaluation.
- Writing a checkpoint does not remove or compact platform conversation context.
- The comparison can support a handoff-recovery claim but cannot attribute general model inference latency to conversation context or prove a broad latency reduction.
- The available environment did not authorize transmission of the derived private handoff bundle to the external receiver service. The scanner evidence remains usable, but receiver comprehension and receiver-side cost remain unverified.

## Local Plugin acceptance

The local marketplace and installed Plugin were exercised through the official CLI and two fresh Codex tasks. Direct visibility in the desktop Plugins UI after restart was not observed because the available safety boundary did not permit controlling Codex itself; that UI-specific result remains unverified. The installed Plugin was nevertheless recognized and executed functionally in both fresh tasks.

- Marketplace source added: yes
- Plugin installed and enabled by the official CLI: yes
- Plugin visible in the desktop Plugins UI after restart: unverified
- Plugin recognized and executed in two fresh Codex tasks: yes
- Create flow produced exactly five files: yes
- Confirmed objective and next step preserved: yes
- Target repository unchanged: yes
- Resume recorded-state comparison succeeded before mutation: yes; it returned exit `0` with `verification: state-match`, meaning the recorded project state matched for the still-trusted local bundle, not that its provenance or contents were authenticated
- Resume stopped after an authorized synthetic mutation: yes; it returned exit `3` with `verification: stale` and did not rerun or read the stale handoff
- Raw transcript or session JSONL requested or read in beginner flow: no
- Private absolute paths or transcript text shown to the beginner: no; only a home-relative location was shown
- Additional fail-closed observation: an unwritable default output in a projectless sandbox returned exit `2`; retrying with a safe writable output outside the target repository succeeded

## Public GitHub acceptance

The initial public release commit `80cd3ca43217f320401e435c80b1449309def7d2` was published to the public `main` branch at `https://github.com/jokercm991102-svg/context-relay`. A fresh isolated `CODEX_HOME` contained no configured marketplaces before the test. The documented commands then added `jokercm991102-svg/context-relay` at `main` and installed `context-relay@context-relay` as version `0.1.0`, enabled.

The installed GitHub-sourced Plugin passed the Plugin validator. Its installed beginner wrapper created exactly the five documented handoff files from an explicitly confirmed objective and next step. The target branch, HEAD, and raw porcelain status were unchanged. Resuming from that still-trusted local bundle returned exit `0` with `verification: state-match`. This verifies public-source installation and the local create/resume path for this release candidate; it does not authenticate shared bundle provenance or prove a general reduction in model latency.
