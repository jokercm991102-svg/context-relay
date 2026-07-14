---
name: context-relay
description: Use when a Codex software-project task has become long, requirements changed repeatedly, the current objective is unclear, or the user asks to create or resume a Context Relay handoff; also matches Chinese requests such as “这个任务变得很长” or “帮我整理当前目标并准备交接”. Not for ordinary coding requests, general summaries, or automatic background monitoring.
---

# Context Relay

Use this Skill only for a project handoff, either through an explicit `$context-relay` request or a matching long-task handoff request. Keep Context Relay local with no network request. Do not edit the target repository.

## Choose one mode

- Use **create** when the user wants to clarify the current goal and prepare a fresh task.
- Use **resume** when the user supplies an existing Context Relay bundle and wants to continue from it.

## Create

1. Confirm the intended Git project root. Explain that the scan is local and read-only.
2. Draft exactly one concise current objective. Do not ask the user for a session JSONL, raw transcript, or permission to include either. Draft the objective from the visible current task, latest user request, and safe project documents instead. Draft no more than five next steps.
3. Show the heading `Proposed Context Relay handoff`, followed by `Current objective` and `Next steps`.
4. Ask the user to confirm or correct that identified handoff. Treat “好的” as acknowledgement, not authorization; a bare unidentified approval is also not authorization. Do not run until the user explicitly confirms the shown Context Relay objective and steps.
5. Resolve `scripts/run_context_relay.py` relative to this `SKILL.md`. Use only that resolved wrapper; do not invoke the core CLI directly. Run its `create` command with the project, confirmed objective, and each of the no more than five confirmed next steps. Do not pass `--session` or `--include-text` in the beginner flow.
6. On exit `0`, rely on the wrapper's sanitized output. The wrapper sanitizes successful output: it prints the bundle as a home-relative `~` location and never exposes `Path.home()`. Inspect only the generated `manifest.json`, `CHECKPOINT.md`, `HANDOFF.md`, and `report.md`. Explain the confirmed objective, whether the target stayed unchanged, the home-relative bundle location, and how to start a fresh Codex task.
7. On exit `2`, explain the single safe correction. On exit `3`, state that the result is stale and do not hand it off. Never show a traceback or private absolute path.

## Resume

1. Confirm the intended project and bundle. Require a bundle the user created locally and trusts. If the bundle was shared, is untrusted, or could have been modified, stop before verification and ask for a fresh trusted Context Relay handoff. Do not inspect the original conversation.
2. Explain the boundary: Verification checks recorded project state and manifest/bundle shape. It does not authenticate the bundle creator or handoff-file content.
3. Resolve `scripts/run_context_relay.py` relative to this `SKILL.md` and run its `resume` command before reading bundle contents.
4. If verification is unsafe or stale, stop. Ask for a fresh Context Relay handoff. Once verification reports stale, do not re-run verification on that bundle and do not continue from it.
5. If verification reports a state match, read only `HANDOFF.md`, `CHECKPOINT.md`, `report.md`, and `manifest.json`. Do not read assessment.json.
6. Restate the confirmed objective and first safe action before editing the target project.

## Boundaries

- Do not claim that Context Relay measures the context window or makes model inference faster.
- Do not locate a session JSONL file automatically.
- Do not copy raw transcript text into the bundle.
- Do not bypass output-path, staleness, privacy, or confirmation checks.
- Do not claim verification protects against a local mutation after verification.
- Tell the user to review a bundle before sharing it.
