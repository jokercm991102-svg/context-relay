# Context Relay

Help Codex remember what you are actually building.

Context Relay is for people who start with a rough software idea, learn from each result, and change the goal while working with Codex. It turns the confirmed current objective and next steps into a local handoff whose recorded project state can be compared in a fresh task.

## Three-step quick start

1. Install the Context Relay Plugin and restart the ChatGPT desktop app.
2. In Codex, invoke `$context-relay` and say: “This task is getting long. Confirm what we are building and prepare a handoff.”
3. Review the proposed objective and next steps. Explicitly confirm them, then use the generated handoff in a fresh task.

You do not need to find a session JSONL file, understand Git fingerprints, or read the formal product specification.

## What is proven

In the preserved fixed-input comparison, V2 recovered all three objective elements while V1 recovered none, and the target repository remained unchanged. This does not prove general receiver comprehension; receiver comprehension remains unverified. Context Relay does not make model inference faster as a proven claim.

## Privacy and safety

Context Relay is local, read-only, and makes no network request. The beginner flow uses the objective you explicitly confirm and does not read the raw Codex transcript.

Use only a bundle you created locally or otherwise trust. Verification checks the recorded project state and the manifest/bundle shape. It does not authenticate who created the bundle, and it does not authenticate the contents of the handoff files. If a bundle is shared, untrusted, or could have been modified, stop and create a fresh trusted handoff. Changes after verification and before use are outside this MVP's protection.

Review every bundle before sharing it. Review protects privacy, not authenticity: sharing a bundle does not make it authentic.

## Install from GitHub

Open Terminal, paste these two commands one at a time, and press Return after each:

```bash
codex plugin marketplace add jokercm991102-svg/context-relay --ref main
codex plugin add context-relay@context-relay
```

Restart the ChatGPT desktop app, open a new Codex task, and invoke `$context-relay`. No terminal command is needed during normal use.

## Beginner walkthrough

Ask Codex: “This task is getting long. Confirm what we are building and prepare a handoff.” Context Relay shows one proposed objective and no more than five next steps. Correct anything that is wrong. Reply with an explicit confirmation such as “I confirm the proposed Context Relay handoff.” A reply such as “okay” or “好的” is only acknowledgement and does not start the scan.

After a successful scan, Context Relay states that the target stayed unchanged and gives a home-relative bundle location. Start a fresh Codex task, provide that bundle, and invoke `$context-relay` to resume. Context Relay verifies the recorded project state before reading the handoff. If the branch, commit, status, worktrees, or tracked project-document evidence changed, it stops and asks for a fresh handoff.

## Limits and troubleshooting

Context Relay supports local macOS and Linux Git projects with Python 3.9 or newer and Git 2.36 or newer. It does not support Windows in this MVP, monitor every Codex turn in the background, create a new task automatically, measure the context window, or guarantee faster model responses.

- Result `0`: the handoff was created without detected target drift, or the recorded project state matches.
- Result `2`: the input, installation, project, bundle, or output location is invalid; correct that item and try again.
- Result `3`: the project changed or the bundle is stale; create a fresh handoff.

If the Skill is missing, confirm the Plugin is installed and enabled, restart the desktop app, and try `$context-relay` in a new task. If Git is too old, update Git before scanning. Do not bypass a stale or unsafe-path warning.

## 简体中文

让 Codex 记住你现在真正想做什么。

Context Relay 适合只有粗略软件想法、会根据每次结果继续修改目标的人。它把用户明确确认的当前目标和下一步，转换成一份保存在本地、可在新任务中比较已记录项目状态的交接资料。

### 三步快速开始

1. 安装 Context Relay Plugin，然后重新启动 ChatGPT 桌面应用。
2. 在 Codex 中调用 `$context-relay`，并输入：“这个任务变得很长，请确认我们现在要做什么并准备交接。”
3. 检查拟定目标和下一步。明确确认后，在新任务中使用生成的交接资料。

你不需要寻找会话 JSONL 文件，不需要理解 Git 指纹，也不需要阅读正式产品规格。

### 已经证明的内容

在保留的固定输入比较中，V2 找回了全部三个目标要素，V1 一个也没有找回，并且目标仓库保持不变。这不能证明接收方普遍更容易理解，也不能证明 Context Relay 会让模型推理更快；接收方理解效果仍未验证。

### 隐私与安全

Context Relay 只在本地运行、只读，并且不发起网络请求。初学者流程使用你明确确认的目标，不读取原始 Codex 对话。

只使用你在本地创建或通过其他方式信任的交接包。验证会检查已记录的项目状态以及 manifest/交接包结构；它不会认证交接包的创建者，也不会认证交接文件的内容。如果交接包来自分享、你不信任，或可能已被修改，请停止并创建一份新的可信交接。验证后、使用前发生的修改不在本 MVP 的保护范围内。

分享交接资料前，请检查每一份资料。检查用于保护隐私，不是为了证明真实性：分享不会使交接包变得真实可信。

### 从 GitHub 安装

打开“终端”，依次粘贴下面两条命令，每粘贴一条后按一次回车：

```bash
codex plugin marketplace add jokercm991102-svg/context-relay --ref main
codex plugin add context-relay@context-relay
```

重新启动 ChatGPT 桌面应用，打开一个新的 Codex 任务，然后输入 `$context-relay`。正常使用时不需要再运行终端命令。

### 初学者操作示例

对 Codex 说：“这个任务变得很长，请确认我们现在要做什么并准备交接。”Context Relay 会显示一个拟定目标和最多五个下一步。如有错误，先进行修正；然后明确回复：“我确认这份 Context Relay 交接。”单独回复“好的”只表示收到，不会开始扫描。

扫描成功后，Context Relay 会说明目标项目保持不变，并提供以用户主目录表示的交接位置。打开一个新的 Codex 任务，提供该交接资料，再调用 `$context-relay` 恢复工作。Context Relay 会先验证项目状态；如果分支、commit、状态、worktree 或项目文档证据发生变化，它会停止并要求重新创建交接。

### 限制与故障排除

MVP 支持本地 macOS 和 Linux Git 项目，需要 Python 3.9 或更高版本以及 Git 2.36 或更高版本。它不支持 Windows，不会在后台监控每一轮 Codex 对话，不会自动创建新任务，不会测量上下文窗口，也不保证模型回复更快。

- 结果 `0`：交接创建期间未检测到目标变化，或者已记录的项目状态相符。
- 结果 `2`：输入、安装、项目、交接包或输出位置无效；修正后重试。
- 结果 `3`：项目已经变化或交接包已经过期；请重新创建交接。

如果找不到 Skill，请确认 Plugin 已安装并启用，重新启动桌面应用，然后在新任务中再次输入 `$context-relay`。如果 Git 版本太旧，请先更新 Git。不要绕过过期或路径不安全警告。

## Advanced CLI

For local Plugin development from a repository checkout, run `codex plugin marketplace add .`, then `codex plugin add context-relay@context-relay`, restart the ChatGPT desktop app, and test in a new Codex task.

Create a bundle from already confirmed input:

```bash
./context-relay scan \
  --project "$PWD" \
  --objective "Ship the confirmed Plugin MVP" \
  --next-step "Run the regression suite" \
  --output-dir "$HOME/.context-relay/runs/manual"
```

Advanced users may explicitly opt into local session text analysis with `--session` and `--include-text`. The beginner Skill never uses those flags.

Verify an existing bundle before resuming:

```bash
BUNDLE="$(find "$HOME/.context-relay/runs/manual" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
./context-relay verify \
  --project "$PWD" \
  --bundle "$BUNDLE"
```

Every successful create run publishes exactly `assessment.json`, `report.md`, `CHECKPOINT.md`, `HANDOFF.md`, and `manifest.json`. For `verify`, exit `0` means the recorded project state matches; it does not authenticate bundle provenance or contents. For `scan`, exit `0` means no target drift was detected. Exit `2` means invalid input, and exit `3` means stale or changed state. See the [bilingual Plugin design](docs/superpowers/specs/2026-07-14-context-relay-plugin-mvp-design.md) and [fixed-input validation](docs/validation/2026-07-14-context-relay-v2-fixed-ab.md).
