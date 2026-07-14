# Context Relay Codex Plugin MVP Design / Context Relay Codex Plugin MVP 设计

Date: 2026-07-14

Languages: [English](#english) · [简体中文](#简体中文)

The English and Simplified Chinese sections describe the same approved design and have equal authority. If a future edit changes product behavior, both sections must be updated in the same commit.

英文版与简体中文版描述同一份已批准设计，具有同等效力。今后如有修改会影响产品行为，必须在同一次提交中同步更新两个版本。

---

## English

### Summary

Context Relay will become a minimal Codex plugin for people who are using Codex to build or modify a software project without a fixed specification. The user may start with only a rough idea, learn what they want by seeing intermediate results, and repeatedly change the goal. When the task becomes long or confusing, the user can ask Context Relay to identify the current objective, confirm it, and create a local handoff bundle for a fresh Codex task.

The plugin wraps the already-tested Context Relay CLI instead of replacing it. The Skill provides the conversational interface; the CLI provides deterministic Git snapshots, staleness detection, privacy controls, and the five-file handoff bundle.

### Product Positioning

#### Public name

**Context Relay — Help Codex remember what you are actually building**

Simplified Chinese title:

**Context Relay — 让 Codex 记住你现在真正想做什么**

#### One-sentence description

For people who build with Codex by trying, reviewing, and changing their minds, Context Relay turns a long, evolving task into a confirmed local project handoff whose recorded project state can be compared before resuming.

#### Tested claim

The public documentation may claim that, for the preserved fixed-input test, Context Relay V2 recovered all three objective elements while V1 recovered none, and that the target repository remained unchanged. It must not claim that the plugin makes model inference faster. Receiver-side comprehension and general model-latency improvement remain unverified.

#### Product priorities and validation boundary

The existing V2 evidence is sufficient to proceed directly to Plugin packaging: the core handoff method has already demonstrated objective recovery and unchanged target state in the preserved fixed-input test. The MVP does not require a separate Skill-only release or a statistically rigorous convenience study before Plugin implementation.

Implementation and release decisions use this priority order:

1. the handoff preserves the user's confirmed current objective and enables a state-checked continuation from a trusted local bundle;
2. the workflow helps the target beginner user avoid reconstructing a long task manually;
3. privacy, local-only behavior, fail-closed checks, and accurate public claims remain compliant with the documented rules;
4. installation and interaction are reasonably simple for the target user.

Convenience is still tested through the beginner usability check, but minor friction does not block the MVP when the workflow is effective, safe, documented, and usable without programming knowledge. Effectiveness and compliance must not be weakened to reduce a click, prompt, restart, or installation step.

### Target User

The primary user:

- uses the Codex desktop app to build or modify a local Git software project;
- may have little or no programming experience;
- begins with an incomplete product idea rather than a final specification;
- changes requirements after seeing each result;
- has a long task with several corrections, approvals, and clarifying questions;
- wants to continue in a fresh task without restating the whole history;
- can copy one installation command once, but should not need terminal commands during normal use.

The MVP is not presented as a general writing, research, or all-AI-assistants product. Those uses have not been validated.

### MVP Scope

The MVP is a repository-root Codex plugin containing:

1. a plugin manifest at `.codex-plugin/plugin.json`;
2. one reusable Skill at `skills/context-relay/SKILL.md`;
3. a deterministic beginner wrapper at `skills/context-relay/scripts/run_context_relay.py`;
4. the existing `context-relay` launcher and `context_relay` Python package;
5. a repository marketplace entry at `.agents/plugins/marketplace.json`;
6. plain-language English and Simplified Chinese installation and usage documentation;
7. deterministic tests for the plugin manifest, Skill contract, wrapper behavior, and existing CLI regression suite.

The plugin version begins at `0.1.0`. It includes no connector, MCP server, hook, background automation, account system, or graphical interface.

### Supported Environment

- ChatGPT/Codex desktop plugin surface;
- local macOS and Linux projects;
- Python 3.9 or newer;
- Git 2.36 or newer;
- a Git repository that Codex can read;
- local-only execution with no network request from Context Relay.

Windows support is outside the MVP because the existing atomic publication implementation intentionally fails closed outside its verified Darwin and Linux primitives.

### User Experience

#### Invocation

The user can invoke the Skill explicitly with `$context-relay` or natural language such as:

- “Help me prepare a clean handoff.”
- “This task is getting long. What are we actually doing now?”
- “帮我整理当前目标，准备换一个新任务。”
- “这个对话太长了，帮我做交接检查。”

The Skill description may support implicit matching, but the product must not promise continuous or automatic background monitoring. The reliable MVP entry point is user invocation.

#### Create-handoff flow

1. **Orient** — Confirm the current project root and explain that the scan is local and read-only.
2. **Draft** — From the current visible task, the latest user request, and safe project documents, draft one concise current objective plus up to five next steps.
3. **Confirm** — Show the proposed objective. If it is ambiguous, ask one short clarification question. Never treat “好的” as approval and never let bare “核准” authorize an unidentified target.
4. **Scan** — After explicit confirmation, run the bundled beginner wrapper with the project path, the confirmed objective, and up to five confirmed next steps. The wrapper calls the existing CLI and writes to `~/.context-relay/runs/<project-hash>/` by default, which is outside the target repository. An advanced user may explicitly choose another safe output directory.
5. **Report** — Translate the result into plain language: what the current objective is, whether the target remained unchanged, where the handoff bundle is, whether the recorded project state matches, and whether the bundle remains user-trusted.
6. **Handoff** — Tell the user how to start a new Codex task and ask it to read the four handoff-facing files before acting.

The default flow does not locate or read a Codex session JSONL file. If the user knowingly supplies a specific session path and opts into text analysis, the existing `--session --include-text` flow remains available as an advanced mode.

#### Resume-handoff flow

When the user asks to continue from an existing bundle, the Skill reads only:

- `HANDOFF.md`;
- `CHECKPOINT.md`;
- `report.md`;
- `manifest.json`.

It must not inspect the original conversation. Use only a bundle the user created locally and trusts. Verification checks recorded project state and manifest/bundle shape; it does not authenticate the bundle creator or handoff-file content.

If a bundle was shared, is untrusted, or could have been modified, stop before verification and request a fresh trusted handoff. A local mutation after verification is outside this MVP's protection. For a still-trusted bundle, the Skill invokes the bundled deterministic verification command, which compares the current branch, HEAD, complete status, worktree fingerprint, document fingerprints, project identity, and recorded staleness state against `manifest.json`. If verification fails, it stops and asks for a fresh handoff instead of guessing.

### Architecture

#### Skill layer

The Skill is responsible for:

- matching beginner-friendly trigger phrases;
- explaining each step without assuming Git or terminal knowledge;
- drafting and confirming the semantic objective;
- choosing create-handoff or resume-handoff mode;
- invoking only the bundled deterministic command;
- converting exit codes and manifest state into plain language;
- refusing to continue from stale or incomplete evidence.

The Skill must use imperative instructions, explicit inputs and outputs, and a narrow description so Codex activates it only for context/handoff work.

The bundled beginner wrapper is responsible only for locating the installed plugin root, choosing the deterministic default output directory, and passing explicit arguments to the existing CLI create or verify command. It does not reimplement scanning, redaction, confirmation, or staleness rules.

#### CLI layer

The existing CLI remains responsible for:

- safe Git and worktree snapshots;
- bounded document and optional session reading;
- semantic confirmation rules;
- preserving one confirmed objective and up to five confirmed next steps;
- privacy redaction;
- atomic five-file publication;
- before/after target comparison;
- deterministic resume verification against the recorded manifest;
- exit codes `0`, `2`, and `3`.

The Skill must not duplicate these rules in ad hoc shell logic.

#### Plugin layer

The repository root is the plugin root so the installed plugin contains the Skill and the tested Python implementation together. The manifest advertises the Skill and beginner-facing metadata. The marketplace file supports local development immediately and Git-backed installation after the actual public repository URL is known.

Public GitHub publication is not complete until the marketplace entry contains the real repository URL and the installation command is tested from a clean environment.

### Output and Data Flow

```text
User asks for a handoff
        |
        v
Skill drafts one current objective
        |
        v
User explicitly confirms or corrects it
        |
        v
Bundled CLI scans project before and after
        |
        v
Exactly five local handoff files are published atomically
        |
        v
Skill explains the state-comparison result and how to resume from the still-trusted bundle
```

The five files remain:

- `assessment.json` for structured local evidence;
- `report.md` for risks and limitations;
- `CHECKPOINT.md` for the confirmed objective and next steps;
- `HANDOFF.md` for fresh-task verification instructions;
- `manifest.json` for before/after fingerprints and staleness evidence, not creator or handoff-content authentication.

### Privacy and Safety

- Context Relay itself makes no network request.
- The default beginner flow uses a user-confirmed objective and does not read the raw Codex transcript.
- Optional session text analysis requires an explicit path and opt-in.
- Raw transcript content is never copied wholesale into the bundle.
- The Skill warns the user to review the bundle before sharing it.
- Sharing does not authenticate a bundle; review before sharing protects privacy, not authenticity.
- Resume requires a local bundle the user trusts. Shared, untrusted, or possibly modified bundles are rejected before verification.
- Verification checks recorded project state and manifest/bundle shape; it does not authenticate the bundle creator or handoff-file content.
- A local mutation after verification is outside this MVP's protection.
- The scan never edits the target repository.
- A changed branch, HEAD, status, worktree topology, or document fingerprint makes the bundle stale.
- A stale result is described as unsafe to continue, not as a successful handoff.
- The plugin must not claim automatic context-window measurement or guaranteed speed improvement.

### Plain-Language Error Handling

The Skill maps technical failures to a short explanation and one safe next action:

- Git is too old: explain that Git 2.36 or newer is required and stop.
- The folder is not a Git project: ask the user to open the intended Codex project.
- The objective is unclear: ask one clarification question before scanning.
- The target changed during scanning: explain that nothing was handed off and offer a fresh scan.
- Output is unsafe or unwritable: choose a safe output location or ask the user for one; do not weaken path checks.
- The CLI is missing or not executable: report a plugin installation problem; do not recreate the scan manually.
- The bundle is incomplete: remove owned partial output and report failure.

Technical tracebacks and private absolute paths are not shown in beginner-facing responses.

### Documentation Design

The public README is rewritten for non-programmers and organized in this order:

1. the problem in everyday language;
2. who the tool is for;
3. what has and has not been proven;
4. one-command plugin installation;
5. the first phrase to type in Codex;
6. a screenshot-free text walkthrough;
7. privacy and local-only behavior;
8. advanced CLI usage for developers;
9. limitations and troubleshooting;
10. complete Simplified Chinese instructions after the concise English guide.

The README must not lead with implementation details, JSONL paths, exit codes, or benchmark tables. Those remain in advanced and validation sections.

### Distribution

During development, the plugin is installed from the local repository marketplace and tested in the Codex desktop app. For public distribution:

1. create or select the public GitHub repository;
2. replace the marketplace source with the actual Git URL and `main` ref;
3. test the marketplace-add command with the repository's final GitHub shorthand from a clean environment;
4. restart the desktop app and verify the plugin appears;
5. install it, invoke the Skill, and complete one real create/resume handoff;
6. publish only after the repository privacy guard is clean.

License selection, the publishing GitHub account, and the final repository name are publication decisions. They do not change the MVP runtime design and require the user's explicit choice before public release.

### Testing Strategy

#### Static package tests

- Validate `.codex-plugin/plugin.json` and marketplace JSON shape.
- Verify every manifest path exists inside the plugin root.
- Verify the Skill has required metadata, narrow triggers, create/resume modes, privacy rules, and no unresolved placeholders.
- Verify public install copy never contains private machine paths, session filenames, credentials, or validation output artifacts.

#### Skill contract tests

- A long-task trigger selects Context Relay.
- A normal coding request does not match the Skill.
- An ambiguous objective requires clarification.
- A confirmed objective and up to five confirmed next steps reach the bundled CLI without session text flags.
- “好的” remains acknowledgement rather than authorization.
- A stale scan stops the handoff.
- Resume mode reads only the four permitted handoff-facing files.

#### Integration tests

- Install from a local marketplace copy.
- Run create-handoff against a temporary Git repository.
- Verify exactly five files and unchanged target state.
- Run resume-handoff against a valid bundle.
- Mutate the target and verify resume fails closed.
- Preserve the existing 201-test CLI suite.

#### Beginner usability check

A test participant who does not write code should be able to:

1. copy one installation command;
2. restart Codex;
3. type one natural-language request;
4. correct or confirm the proposed objective;
5. receive a plain-language result;
6. start a fresh task from the generated bundle;
7. complete the flow without manually finding a session JSONL file.

### Acceptance Criteria

The MVP is complete when:

- the plugin installs from a local marketplace with no manual file copying;
- the Skill and bundled CLI are installed together;
- create-handoff and resume-handoff both pass deterministic tests;
- the default flow never asks a beginner for a session JSONL path;
- no target repository edit occurs during a scan;
- stale or ambiguous state always fails closed;
- the full CLI regression suite remains green;
- the beginner README is complete in English and Simplified Chinese;
- an actual GitHub URL can replace only the distribution source without changing runtime code;
- public claims match the preserved validation evidence and retain the `UNVERIFIED` receiver limitation.

### Non-Goals

The MVP does not include:

- continuous monitoring of every Codex turn;
- automatic context-window or token-budget measurement;
- automatic creation of a new Codex task;
- background reminders or scheduled automations;
- hooks that run on every tool call;
- a GUI, menu-bar app, or browser dashboard;
- cloud storage, accounts, telemetry, or analytics;
- Windows support;
- automatic publication of a user's project or handoff bundle to GitHub;
- proof that context length is the sole cause of model latency.
- cryptographic authentication, signatures, or handoff-file integrity guarantees.

These may be evaluated only after the manual Plugin workflow demonstrates repeat usage by real users.

### Official Codex References

- [Build skills](https://learn.chatgpt.com/docs/build-skills)
- [Build plugins](https://learn.chatgpt.com/docs/build-plugins)

---

## 简体中文

### 概要

Context Relay 将成为一个精简的 Codex 插件，服务于那些没有固定规格、正在使用 Codex 创建或修改软件项目的人。用户可能一开始只有一个粗略想法，在看到阶段性成果后才逐渐明确需求，并反复改变目标。当任务变得很长或令人困惑时，用户可以请 Context Relay 识别当前目标、让用户确认，并为新的 Codex 任务创建一份本地交接包。

插件会包装已经通过测试的 Context Relay CLI，而不是取代它。Skill 提供对话界面；CLI 提供确定性的 Git 快照、过期检测、隐私控制和包含五个文件的交接包。

### 产品定位

#### 公开名称

**Context Relay — Help Codex remember what you are actually building**

简体中文标题：

**Context Relay — 让 Codex 记住你现在真正想做什么**

#### 一句话说明

适用于那些通过尝试、检查结果和改变想法来与 Codex 一起创建项目的人。Context Relay 可以把漫长且不断变化的任务，转换为一份经过用户确认的本地项目交接资料，并在恢复前比较已记录的项目状态。

#### 已验证的主张

公开文档可以说明：在保留的固定输入测试中，Context Relay V2 找回了目标中的三个要素，而 V1 一个也没有找回，并且目标仓库保持不变。文档不得声称插件能让模型推理变快。接收方理解效果以及模型延迟是否普遍改善，目前仍未得到验证。

#### 产品优先级与验证边界

现有 V2 证据已经足够支持直接进入 Plugin 包装阶段：核心交接方法已在保留的固定输入测试中证明可以找回目标，并保持目标仓库不变。开始实现 Plugin 之前，MVP 不要求另外发布一个只有 Skill 的版本，也不要求先完成具有统计严谨性的便利性研究。

实现和发布决策按照以下优先顺序进行：

1. 交接能够保留用户明确确认的当前目标，并支持从可信本地交接包继续已检查状态的工作；
2. 工作流程能够帮助目标初学者避免手工重建漫长任务；
3. 隐私、仅限本地运行、安全停止检查以及准确的公开说明，继续符合文档规则；
4. 安装和交互对目标用户而言足够简单。

便利性仍会通过初学者可用性检查进行测试，但当工作流程有效、安全、文档完整，并且不需要编程知识即可使用时，少量操作摩擦不会阻止 MVP 发布。不得为了减少一次点击、一句提示、一次重启或一个安装步骤，而削弱有效性或合规性。

### 目标用户

主要用户：

- 使用 Codex 桌面应用创建或修改本地 Git 软件项目；
- 可能几乎没有或完全没有编程经验；
- 从不完整的产品想法开始，而不是从最终规格开始；
- 看到每次结果后会改变需求；
- 一个任务中包含多次修正、批准和澄清；
- 希望换到新任务继续工作，而不必重新讲述全部历史；
- 可以复制一次安装命令，但正常使用时不应该再需要终端命令。

MVP 不会被宣传为通用写作、研究或适用于所有 AI 助手的产品，因为这些用途尚未经过验证。

### MVP 范围

MVP 是一个以仓库根目录为插件根目录的 Codex 插件，包含：

1. 位于 `.codex-plugin/plugin.json` 的插件清单；
2. 位于 `skills/context-relay/SKILL.md` 的一个可复用 Skill；
3. 位于 `skills/context-relay/scripts/run_context_relay.py`、行为确定且适合初学者的包装程序；
4. 现有的 `context-relay` 启动器和 `context_relay` Python 包；
5. 位于 `.agents/plugins/marketplace.json` 的仓库 marketplace 条目；
6. 使用通俗英文和简体中文编写的安装与使用文档；
7. 针对插件清单、Skill 契约、包装程序行为以及现有 CLI 回归套件的确定性测试。

插件版本从 `0.1.0` 开始。它不包含 connector、MCP server、hook、后台自动化、账户系统或图形界面。

### 支持环境

- ChatGPT/Codex 桌面插件环境；
- 本地 macOS 和 Linux 项目；
- Python 3.9 或更高版本；
- Git 2.36 或更高版本；
- Codex 可以读取的 Git 仓库；
- Context Relay 仅在本地运行，不发起网络请求。

Windows 支持不属于 MVP 范围，因为现有的原子发布实现会在经过验证的 Darwin 和 Linux 原语之外主动停止，以保证安全。

### 用户体验

#### 调用方式

用户可以通过 `$context-relay` 明确调用 Skill，也可以使用类似以下的自然语言：

- “Help me prepare a clean handoff.”
- “This task is getting long. What are we actually doing now?”
- “帮我整理当前目标，准备换一个新任务。”
- “这个对话太长了，帮我做交接检查。”

Skill 描述可以支持隐式匹配，但产品不得承诺持续或自动后台监控。MVP 可靠的入口是由用户主动调用。

#### 创建交接流程

1. **确认位置**——确认当前项目根目录，并解释扫描只在本地进行且为只读。
2. **起草目标**——根据当前可见任务、用户最近的要求和安全的项目文档，起草一个简洁的当前目标，以及最多五个下一步。
3. **用户确认**——展示拟定目标。如果目标有歧义，只问一个简短的澄清问题。绝不能把“好的”视为批准，也不能让没有指明对象的单独“批准”授权任何操作。
4. **扫描**——用户明确确认后，使用项目路径、已确认目标和最多五个已确认的下一步，运行插件自带的初学者包装程序。包装程序调用现有 CLI，默认写入目标仓库之外的 `~/.context-relay/runs/<project-hash>/`。高级用户可以明确选择另一个安全的输出目录。
5. **报告结果**——用通俗语言说明当前目标、目标项目是否保持不变、交接包所在位置、当前已记录的项目状态是否相符，以及交接包是否仍受用户信任。
6. **完成交接**——告诉用户如何新建 Codex 任务，并要求新任务在行动前读取四个面向交接的文件。

默认流程不会查找或读取 Codex 会话 JSONL 文件。如果用户主动提供特定会话路径并明确同意文本分析，现有的 `--session --include-text` 流程仍可作为高级模式使用。

#### 恢复交接流程

用户要求从现有交接包继续时，Skill 只读取：

- `HANDOFF.md`；
- `CHECKPOINT.md`；
- `report.md`；
- `manifest.json`。

它不得检查原始对话。只使用用户在本地创建并且信任的交接包。验证会检查已记录的项目状态以及 manifest/交接包结构；它不会认证交接包创建者或交接文件内容。

如果交接包来自分享、不受信任或可能已被修改，请在验证前停止，并要求创建一份新的可信交接。验证后的本地修改不在本 MVP 的保护范围内。对于仍然可信的交接包，Skill 会调用插件自带的确定性验证命令，把当前分支、HEAD、完整状态、worktree 指纹、文档指纹、项目身份和已记录的过期状态，与 `manifest.json` 进行比较。如果验证失败，它会停止并要求创建新的交接，而不是猜测。

### 架构

#### Skill 层

Skill 负责：

- 匹配适合初学者的触发语句；
- 在不假设用户了解 Git 或终端的前提下解释每一步；
- 起草并确认语义目标；
- 选择创建交接或恢复交接模式；
- 只调用插件自带的确定性命令；
- 把退出码和 manifest 状态转换为通俗语言；
- 拒绝从已经过期或证据不完整的状态继续。

Skill 必须使用祈使式说明、明确的输入输出和范围狭窄的描述，使 Codex 只在处理上下文或交接时激活它。

插件自带的初学者包装程序只负责定位已安装的插件根目录、选择确定性的默认输出目录，并把明确参数传给现有 CLI 的创建或验证命令。它不会重新实现扫描、脱敏、确认或过期判断规则。

#### CLI 层

现有 CLI 继续负责：

- 安全的 Git 和 worktree 快照；
- 有界的文档读取，以及可选的会话读取；
- 语义确认规则；
- 保存一个已确认目标和最多五个已确认的下一步；
- 隐私脱敏；
- 五个文件的原子发布；
- 目标扫描前后比较；
- 根据已记录的 manifest 进行确定性的恢复验证；
- 退出码 `0`、`2` 和 `3`。

Skill 不得使用临时拼凑的 shell 逻辑重复这些规则。

#### Plugin 层

仓库根目录就是插件根目录，因此安装后的插件会同时包含 Skill 和经过测试的 Python 实现。插件清单会声明 Skill 和面向初学者的元数据。marketplace 文件可以立即支持本地开发，并在实际公开仓库 URL 确定后支持基于 Git 的安装。

在 marketplace 条目包含真实仓库 URL，并且安装命令已在干净环境中测试之前，不能认为 GitHub 公开发布已经完成。

### 输出与数据流

```text
用户请求交接
      |
      v
Skill 起草一个当前目标
      |
      v
用户明确确认或修正目标
      |
      v
插件自带的 CLI 在处理前后扫描项目
      |
      v
以原子方式发布且仅发布五个本地交接文件
      |
      v
Skill 解释状态比较结果，以及如何从仍受信任的交接包恢复工作
```

五个文件保持不变：

- `assessment.json`：结构化的本地证据；
- `report.md`：风险和限制；
- `CHECKPOINT.md`：经过确认的目标和下一步；
- `HANDOFF.md`：供新任务使用的验证说明；
- `manifest.json`：处理前后指纹和过期证据，不用于认证创建者或交接文件内容。

### 隐私与安全

- Context Relay 本身不发起网络请求。
- 默认初学者流程使用经过用户确认的目标，不读取原始 Codex 对话记录。
- 可选的会话文本分析需要用户提供明确路径并主动同意。
- 原始对话内容绝不会被整段复制到交接包中。
- Skill 会提醒用户在分享前检查交接包。
- 分享不会认证交接包；分享前检查是为了保护隐私，不是为了证明真实性。
- 恢复工作要求使用用户信任的本地交接包。来自分享、不受信任或可能已被修改的交接包会在验证前被拒绝。
- 验证会检查已记录的项目状态以及 manifest/交接包结构；它不会认证交接包创建者或交接文件内容。
- 验证后的本地修改不在本 MVP 的保护范围内。
- 扫描绝不编辑目标仓库。
- 分支、HEAD、状态、worktree 拓扑或文档指纹发生变化时，交接包会被判定为过期。
- 过期结果会被说明为“不适合继续”，而不是成功交接。
- 插件不得声称可以自动测量上下文窗口，或保证改善速度。

### 通俗错误处理

Skill 会把技术故障转换成简短说明和一个安全的下一步：

- Git 太旧：说明需要 Git 2.36 或更高版本，然后停止。
- 文件夹不是 Git 项目：请用户打开预期的 Codex 项目。
- 目标不清楚：扫描前只问一个澄清问题。
- 扫描期间目标发生变化：说明没有完成交接，并建议重新扫描。
- 输出位置不安全或无法写入：选择安全输出位置或请用户指定；不得削弱路径检查。
- CLI 缺失或不可执行：报告插件安装问题；不得手工重建扫描逻辑。
- 交接包不完整：清理本工具拥有的部分输出，然后报告失败。

面向初学者的回复中不显示技术堆栈信息和私有绝对路径。

### 文档设计

公开 README 会为非程序员重新编写，并按以下顺序组织：

1. 用日常语言说明问题；
2. 说明工具适合谁；
3. 说明已经证明和尚未证明的内容；
4. 一条命令安装插件；
5. 告诉用户第一次在 Codex 中输入什么；
6. 不依赖截图的文字操作示例；
7. 隐私和仅限本地运行的说明；
8. 面向开发者的高级 CLI 用法；
9. 限制和故障排除；
10. 在精简英文指南之后提供完整的简体中文说明。

README 不得以实现细节、JSONL 路径、退出码或基准测试表格开头。这些内容保留在高级用法和验证章节中。

### 分发

开发期间，插件从本地仓库 marketplace 安装，并在 Codex 桌面应用中测试。公开分发流程如下：

1. 创建或选择公开 GitHub 仓库；
2. 把 marketplace 来源替换为真实 Git URL 和 `main` ref；
3. 在干净环境中，使用最终 GitHub 简写测试 marketplace-add 命令；
4. 重新启动桌面应用并确认插件出现；
5. 安装插件、调用 Skill，并完成一次真实的创建交接和恢复交接；
6. 只有仓库隐私检查通过后才发布。

许可证选择、发布所用的 GitHub 账户和最终仓库名称属于发布决策。它们不改变 MVP 运行时设计，但在公开发布前必须由用户明确选择。

### 测试策略

#### 静态包测试

- 验证 `.codex-plugin/plugin.json` 和 marketplace JSON 的结构。
- 验证插件清单中的每个路径都存在于插件根目录内。
- 验证 Skill 包含必要元数据、狭窄触发条件、创建/恢复模式、隐私规则，并且没有未解决的占位符。
- 验证公开安装文案不包含私人设备路径、会话文件名、凭据或验证输出产物。

#### Skill 契约测试

- 长任务相关触发语句会选择 Context Relay。
- 普通编程请求不会匹配此 Skill。
- 有歧义的目标必须先澄清。
- 经过确认的目标和最多五个已确认的下一步会传给插件自带的 CLI，并且不带会话文本标志。
- “好的”仍然只是回应，而不是授权。
- 过期扫描会停止交接。
- 恢复模式只读取四个获准的交接文件。

#### 集成测试

- 从本地 marketplace 副本安装。
- 针对临时 Git 仓库运行创建交接。
- 验证只生成五个文件，并且目标状态保持不变。
- 针对有效交接包运行恢复交接。
- 修改目标，然后验证恢复操作会安全停止。
- 保留现有的 201 项 CLI 测试套件。

#### 初学者可用性检查

不编写代码的测试参与者应该能够：

1. 复制一条安装命令；
2. 重新启动 Codex；
3. 输入一句自然语言请求；
4. 修正或确认拟定目标；
5. 得到通俗语言结果；
6. 使用生成的交接包开始新任务；
7. 在不手动寻找会话 JSONL 文件的情况下完成流程。

### 验收条件

满足以下条件时，MVP 才算完成：

- 插件可从本地 marketplace 安装，不需要手动复制文件；
- Skill 和插件自带的 CLI 会一起安装；
- 创建交接和恢复交接都通过确定性测试；
- 默认流程绝不会要求初学者提供会话 JSONL 路径；
- 扫描期间不会编辑目标仓库；
- 过期或有歧义的状态总会安全停止；
- 完整 CLI 回归测试套件继续通过；
- 面向初学者的 README 提供完整英文版和简体中文版；
- 替换为真实 GitHub URL 时，只需更改分发来源，不需要修改运行时代码；
- 公开主张与保留的验证证据一致，并保留接收方效果 `UNVERIFIED` 的限制说明。

### 非目标

MVP 不包含：

- 持续监控每一轮 Codex 对话；
- 自动测量上下文窗口或 token 预算；
- 自动创建新的 Codex 任务；
- 后台提醒或定时自动化；
- 每次工具调用时执行的 hook；
- GUI、菜单栏应用或浏览器仪表板；
- 云端存储、账户、遥测或分析；
- Windows 支持；
- 自动把用户项目或交接包发布到 GitHub；
- 证明上下文长度是模型延迟的唯一原因。
- 加密认证、签名或交接文件完整性保证。

只有手动 Plugin 工作流程在真实用户中证明具有重复使用价值后，才会评估这些功能。

### Codex 官方参考资料

- [Build skills](https://learn.chatgpt.com/docs/build-skills)
- [Build plugins](https://learn.chatgpt.com/docs/build-plugins)
