# Context Relay Visible Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Context Relay announce every Create and Resume step up front, visibly identify human confirmation gates, and switch to an action-specific running state after confirmation.

**Architecture:** Keep the deterministic Python wrapper and core handoff engine unchanged. Implement the feature in the Skill's conversational contract, lock the contract with repository tests, mirror the behavior in the bilingual README, and bump the public Plugin manifest to `0.1.2`.

**Tech Stack:** Markdown Skill instructions, Python 3 `unittest`, JSON Plugin manifest, Codex Plugin validator.

## Global Constraints

- Create exposes 4 user-visible steps; steps 1 and 2 require confirmation.
- Resume exposes 3 user-visible steps; step 1 requires confirmation.
- Use only the states **Confirmation required**, **Running**, **Complete**, and **Stopped**, localized to the user's language.
- Do not tell the user to leave, wait elsewhere, or remain on the screen.
- Do not provide a duration estimate or imply asynchronous background monitoring.
- Preserve all existing explicit-confirmation, local-only, privacy, staleness, and read-only target rules.
- Do not change the Python wrapper or core scanner unless an existing regression test proves a compatibility defect.
- Public release version is `0.1.2`; `.agents/plugins/marketplace.json` remains a local-source marketplace and does not gain an unsupported version field.
- English and Simplified Chinese public documentation must remain behaviorally equivalent.

---

## File Structure

- `tests/test_plugin_package.py` — deterministic package, Skill-contract, documentation-parity, and manifest-version assertions.
- `skills/context-relay/SKILL.md` — normative Create and Resume conversational behavior.
- `README.md` — beginner-facing English and Simplified Chinese explanation and examples.
- `.codex-plugin/plugin.json` — public Plugin version and existing interface metadata.
- `.agents/plugins/marketplace.json` — remains unchanged; its local source resolves the version from the Plugin manifest.

---

### Task 1: Lock and implement the visible Skill workflow

**Files:**
- Modify: `tests/test_plugin_package.py`
- Modify: `skills/context-relay/SKILL.md`

**Interfaces:**
- Consumes: the existing Create and Resume safety contract and wrapper invocation rules.
- Produces: a stable text contract for visible step counts, confirmation gates, state transitions, and stopped behavior.

- [ ] **Step 1: Add the failing Skill-contract test**

Add this method to `PluginPackageTests` immediately after `test_skill_contract_is_narrow_safe_and_bilingual`:

```python
    def test_skill_announces_visible_confirmation_workflows(self):
        skill = (ROOT / "skills/context-relay/SKILL.md").read_text(
            encoding="utf-8"
        )
        required = (
            "Create has 4 visible steps and 2 confirmation gates.",
            "Resume has 3 visible steps and 1 confirmation gate.",
            "Confirmation required",
            "Running",
            "Complete",
            "Stopped",
            "Scanning will not start before confirmation.",
            "The handoff bundle will not be created before explicit confirmation.",
            "Confirmation complete",
            "Creating the local handoff bundle now.",
            "Verification will not start before confirmation.",
            "Verifying the project and handoff bundle state now.",
            "Context Relay is not continuing execution.",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.casefold(), skill.casefold())

        forbidden = (
            "you can leave",
            "leave the screen",
            "wait elsewhere",
            "remain on the screen",
        )
        for phrase in forbidden:
            with self.subTest(forbidden=phrase):
                self.assertNotIn(phrase.casefold(), skill.casefold())
```

- [ ] **Step 2: Run the new test and verify the expected failure**

Run:

```bash
python3 -m unittest tests.test_plugin_package.PluginPackageTests.test_skill_announces_visible_confirmation_workflows -v
```

Expected: `FAIL`; the first missing phrase is `Create has 4 visible steps and 2 confirmation gates.`

- [ ] **Step 3: Add the minimal visible-status contract to the Skill**

Insert this section after `## Choose one mode` and its two mode bullets:

```markdown
## Show visible workflow status

Respond in the user's language. Before asking for the first confirmation, show the complete selected workflow, label every confirmation and automatic step, and identify the current step.

- Create has 4 visible steps and 2 confirmation gates.
  1. **Confirmation required** — confirm the target project.
  2. **Confirmation required** — confirm the current objective and next steps.
  3. **Automatic** — create the local handoff bundle.
  4. **Automatic result** — show the result and explain how to continue in a fresh task.
- Resume has 3 visible steps and 1 confirmation gate.
  1. **Confirmation required** — confirm the project and trusted local bundle.
  2. **Automatic** — verify the project and bundle state.
  3. **Automatic result** — read the handoff and identify the first safe action.

Use only these workflow states, localized to the user's language: **Confirmation required**, **Running**, **Complete**, and **Stopped**. Corrections may add conversation turns, but they do not add confirmation gates. Keep the current state at **Confirmation required** until that gate is explicitly confirmed.

When waiting, state which operation has not started. When confirmation is complete, show **Confirmation complete — Running** and name the operation that is starting. Do not call that operation generic implementation. Do not tell the user to leave, wait elsewhere, or remain on the screen. Do not imply background monitoring or promise a duration.
```

Add these sentences without deleting or weakening any existing safety instruction:

- At the start of Create step 1: `Show Current: Step 1 of 4 — Confirmation required. Scanning will not start before confirmation.`
- Before the existing Create confirmation request in step 4: `Show Current: Step 2 of 4 — Confirmation required. The handoff bundle will not be created before explicit confirmation.`
- Before the wrapper invocation in Create step 5: `Show Confirmation complete — Running. Creating the local handoff bundle now.`
- At the start of successful Create reporting in step 6: `Show Step 4 of 4 — Complete.`
- At the start of Resume step 1: `Show Current: Step 1 of 3 — Confirmation required. Verification will not start before confirmation.`
- Before verification in Resume step 3: `Show Confirmation complete — Running. Verifying the project and handoff bundle state now.`
- Before the successful Resume restatement in step 6: `Show Step 3 of 3 — Complete.`
- On every unsafe, invalid, or stale stop path: `Show Stopped. Context Relay is not continuing execution.`

- [ ] **Step 4: Run the focused Skill tests**

Run:

```bash
python3 -m unittest \
  tests.test_plugin_package.PluginPackageTests.test_skill_announces_visible_confirmation_workflows \
  tests.test_plugin_package.PluginPackageTests.test_skill_contract_locks_normative_safety_semantics \
  tests.test_plugin_package.PluginPackageTests.test_skill_closes_fresh_agent_privacy_and_stale_loopholes \
  -v
```

Expected: 3 tests pass and the command ends with `OK`.

- [ ] **Step 5: Commit the Skill contract**

```bash
git add tests/test_plugin_package.py skills/context-relay/SKILL.md
git commit -m "feat: show Context Relay confirmation progress"
```

---

### Task 2: Document the visible workflow in both public languages

**Files:**
- Modify: `tests/test_plugin_package.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: the exact visible Create and Resume step counts from Task 1.
- Produces: beginner documentation that sets correct expectations before installation and first use.

- [ ] **Step 1: Add the failing bilingual README test**

Add this method to `PluginPackageTests` after the Skill workflow test:

```python
    def test_readme_explains_visible_confirmation_workflow(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        required = (
            "Create has 4 steps; steps 1 and 2 require your confirmation.",
            "Resume has 3 steps; step 1 requires your confirmation.",
            "Confirmation required",
            "Confirmation complete — Running",
            "Create 共 4 个步骤，第 1、2 步需要你确认。",
            "Resume 共 3 个步骤，第 1 步需要你确认。",
            "需要确认",
            "确认部分已完成｜执行中",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.casefold(), readme.casefold())
```

- [ ] **Step 2: Run the README test and verify the expected failure**

Run:

```bash
python3 -m unittest tests.test_plugin_package.PluginPackageTests.test_readme_explains_visible_confirmation_workflow -v
```

Expected: `FAIL` because the English Create step-count sentence is absent.

- [ ] **Step 3: Add the English beginner explanation**

Insert this section after `## Three-step quick start` and before `## What is proven`:

```markdown
## Visible workflow status

Context Relay shows the complete workflow before it asks you to confirm anything.

- Create has 4 steps; steps 1 and 2 require your confirmation. It then creates the local handoff and shows the result automatically.
- Resume has 3 steps; step 1 requires your confirmation. It then verifies the project state and shows the safe continuation automatically.

When you see **Confirmation required**, Context Relay is waiting for you and has not started the named operation. After you confirm, it changes to **Confirmation complete — Running** and states the operation that has started. A failed safety or staleness check is labeled **Stopped**.
```

- [ ] **Step 4: Add the equivalent Simplified Chinese explanation**

Insert this section after `### 三步快速开始` and before `### 已经证明的内容`:

```markdown
### 可见流程状态

Context Relay 会在要求确认前显示完整流程。

- Create 共 4 个步骤，第 1、2 步需要你确认。之后会自动创建本地交接资料并显示结果。
- Resume 共 3 个步骤，第 1 步需要你确认。之后会自动验证项目状态并显示安全的继续方式。

看到 **需要确认** 时，代表 Context Relay 正在等待你，所说明的操作尚未开始。确认后，状态会变为 **确认部分已完成｜执行中**，并说明已经开始的操作。安全或过期检查失败时会显示 **已停止**。
```

- [ ] **Step 5: Run documentation and preserved-claim tests**

Run:

```bash
python3 -m unittest \
  tests.test_plugin_package.PluginPackageTests.test_readme_explains_visible_confirmation_workflow \
  tests.test_plugin_package.PluginPackageTests.test_readme_leads_with_beginner_quick_start_and_keeps_claims_bounded \
  -v
```

Expected: 2 tests pass and the command ends with `OK`.

- [ ] **Step 6: Commit the bilingual documentation**

```bash
git add tests/test_plugin_package.py README.md
git commit -m "docs: explain visible confirmation workflow"
```

---

### Task 3: Bump and validate the `0.1.2` package contract

**Files:**
- Modify: `tests/test_plugin_package.py`
- Modify: `.codex-plugin/plugin.json`
- Verify unchanged: `.agents/plugins/marketplace.json`

**Interfaces:**
- Consumes: the finished Skill and README behavior from Tasks 1 and 2.
- Produces: a strict-semver public Plugin manifest for version `0.1.2`.

- [ ] **Step 1: Change the manifest test first**

In `test_manifest_has_valid_stable_contract`, replace:

```python
                "version": "0.1.1",
```

with:

```python
                "version": "0.1.2",
```

- [ ] **Step 2: Run the manifest test and verify the expected failure**

Run:

```bash
python3 -m unittest tests.test_plugin_package.PluginPackageTests.test_manifest_has_valid_stable_contract -v
```

Expected: `FAIL` showing actual version `0.1.1` and expected version `0.1.2`.

- [ ] **Step 3: Update only the public manifest version**

In `.codex-plugin/plugin.json`, replace:

```json
  "version": "0.1.1",
```

with:

```json
  "version": "0.1.2",
```

Do not add a version field to `.agents/plugins/marketplace.json`; the local marketplace entry points to `./`, and Codex reads the version from `.codex-plugin/plugin.json`.

- [ ] **Step 4: Run the package contract tests**

Run:

```bash
python3 -m unittest tests.test_plugin_package -v
```

Expected: every `PluginPackageTests` test passes and the command ends with `OK`.

- [ ] **Step 5: Commit the release version**

```bash
git add tests/test_plugin_package.py .codex-plugin/plugin.json
git commit -m "chore: bump Context Relay to 0.1.2"
```

---

### Task 4: Run complete regression and Plugin-package validation

**Files:**
- Verify: all tracked files
- No production file should change in this task.

**Interfaces:**
- Consumes: the completed `0.1.2` repository state.
- Produces: test and validator evidence suitable for the final handoff.

- [ ] **Step 1: Run the complete repository regression suite**

Run:

```bash
python3 -m unittest discover -s tests -v
```

Expected: all tests pass and the command ends with `OK`.

- [ ] **Step 2: Prepare the validator dependency in a temporary directory**

Run:

```bash
python3 -m pip install --target /private/tmp/context-relay-validator-deps PyYAML==6.0.2
```

Expected: `Successfully installed PyYAML-6.0.2`. This writes only to `/private/tmp`; do not add the dependency to the Plugin repository.

- [ ] **Step 3: Validate the Skill frontmatter**

Run:

```bash
PYTHONPATH=/private/tmp/context-relay-validator-deps \
python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" \
skills/context-relay
```

Expected: `Skill is valid!`

- [ ] **Step 4: Validate the complete Plugin package**

Run:

```bash
PYTHONPATH=/private/tmp/context-relay-validator-deps \
python3 "$HOME/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py" .
```

Expected: output begins with `Plugin validation passed:` and names this repository root.

- [ ] **Step 5: Verify a clean diff and repository state**

Run:

```bash
git diff --check
git status --short --branch
```

Expected: `git diff --check` prints nothing; status shows `main` ahead only by the intended design, plan, feature, documentation, and version commits, with no untracked or modified files.

- [ ] **Step 6: Inspect for release blockers**

Run:

```bash
rg -n "T[B]D|T[O]DO|\[T[O]DO:|0\.1\.1" \
  .codex-plugin/plugin.json \
  .agents/plugins/marketplace.json \
  skills/context-relay/SKILL.md \
  README.md
```

Expected: no matches. Historical validation and design documents are intentionally outside this release-blocker scan.

---

## New-task Acceptance After Installation

The updated Skill is loaded only in a new Codex task after reinstall. Do not claim live conversational acceptance from the current task. After the code plan is complete and the user authorizes installation or publication:

1. validate the exact installed `0.1.2` source;
2. reinstall through the confirmed local or GitHub marketplace without hand-editing marketplace configuration;
3. restart Codex if requested by the installation surface;
4. open a new task and invoke `$context-relay` once in Create mode and once in Resume mode;
5. verify that the opening message lists all steps and human confirmation gates before any confirmation request;
6. verify that confirmation changes the state from **需要确认** to **确认部分已完成｜执行中**;
7. verify that no message tells the user to leave or implies background monitoring.

Publication, GitHub push, and changes to the user's active installation remain separate external actions and require their corresponding authorization.
