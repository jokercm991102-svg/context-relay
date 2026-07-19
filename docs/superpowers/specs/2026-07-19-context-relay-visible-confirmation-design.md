# Context Relay Visible Confirmation Design / Context Relay 可见确认流程设计

Date: 2026-07-19

Languages: [English](#english) · [简体中文](#简体中文)

The English and Simplified Chinese sections describe the same approved design and have equal authority. A behavior change must update both sections in the same commit.

英文版与简体中文版描述同一份已批准设计，具有同等效力。任何行为修改都必须在同一次提交中同步更新两个版本。

---

## English

### Problem

Context Relay correctly requires explicit confirmation before it creates or resumes a handoff. However, the current Skill does not announce the complete workflow or identify which steps require the user. A user may see Codex respond, assume execution has started, and leave while Codex is actually waiting for confirmation.

### Decision

At the beginning of both Create and Resume, Context Relay must:

1. state the total number of steps;
2. list every step in order;
3. label every step as **Confirmation required**, **Automatic**, or **Automatic result**;
4. state the current step and status;
5. say what will not start until confirmation is received.

The workflow uses four status labels:

- **Confirmation required** — Context Relay is waiting for the user and has not started the protected operation.
- **Running** — all confirmation gates for the operation are complete and Context Relay is performing the named action.
- **Complete** — the operation finished successfully and Context Relay is presenting the result.
- **Stopped** — a safety or validation condition prevented further execution; no background work is continuing.

Do not tell the user to leave, wait elsewhere, or remain on the screen. Report only the workflow state and the action currently required.

### Create Flow

Create has four visible steps and two confirmation gates:

1. **Confirmation required** — confirm the target project.
2. **Confirmation required** — confirm the current objective and next steps.
3. **Automatic** — create the local handoff bundle.
4. **Automatic result** — report the result and explain how to continue in a fresh task.

The opening message must follow this meaning:

> **Context Relay Create has 4 steps. Steps 1 and 2 require your confirmation:**
>
> 1. **Confirmation required:** Confirm the target project
> 2. **Confirmation required:** Confirm the current objective and next steps
> 3. **Automatic:** Create the local handoff bundle
> 4. **Automatic result:** Show the result and explain how to continue in a fresh task
>
> **Current: Step 1 of 4 — Confirmation required**
> Confirm that the project shown below is correct. Scanning will not start before confirmation.

When the objective and next steps are shown, use:

> **Current: Step 2 of 4 — Confirmation required**
> Review the proposed objective and next steps. The handoff bundle will not be created before explicit confirmation.

After explicit confirmation, use:

> **Confirmation complete — Running**
> Creating the local handoff bundle now.

On success, use:

> **Step 4 of 4 — Complete**
> The handoff bundle has been created. Here is the result and how to continue in a fresh task.

### Resume Flow

Resume has three visible steps and one confirmation gate:

1. **Confirmation required** — confirm the target project and a locally created, trusted bundle.
2. **Automatic** — verify the project and bundle state.
3. **Automatic result** — read the permitted handoff files, restate the objective, and identify the first safe action.

The opening message must follow this meaning:

> **Context Relay Resume has 3 steps. Step 1 requires your confirmation:**
>
> 1. **Confirmation required:** Confirm the project and trusted local bundle
> 2. **Automatic:** Verify the project and bundle state
> 3. **Automatic result:** Read the handoff and identify the first safe action
>
> **Current: Step 1 of 3 — Confirmation required**
> Confirm the project and bundle shown below. Verification will not start before confirmation.

After confirmation, use:

> **Confirmation complete — Running**
> Verifying the project and handoff bundle state now.

On success, use:

> **Step 3 of 3 — Complete**
> State verification passed. Here is the confirmed objective and the first safe action.

### Corrections and Stops

Corrections may add conversational turns, but they do not add new confirmation gates. Continue to label the current workflow step as **Confirmation required** until the content at that gate is explicitly confirmed.

If the wrapper returns an unsafe, stale, or invalid result, use:

> **Stopped**
> The project or handoff bundle did not pass the required check. Context Relay is not continuing execution.

The existing fail-closed behavior, explicit-approval rules, privacy boundaries, and exit-code handling remain unchanged.

### Implementation Scope

The change targets version `0.1.2` and is limited to:

- the Create and Resume conversational contract in `skills/context-relay/SKILL.md`;
- matching English and Simplified Chinese examples in `README.md`;
- deterministic contract tests for the announced step counts, confirmation labels, running transition, and stopped state;
- plugin and marketplace version metadata required for the `0.1.2` release.

The Python wrapper and core scanning or verification logic do not change unless tests reveal that a narrow compatibility adjustment is necessary.

### Non-goals

- No background monitoring.
- No asynchronous progress tracker.
- No time estimate or guaranteed duration.
- No reduction or bypass of confirmation gates.
- No change to bundle contents, project-state verification, privacy guarantees, or read-only target behavior.

### Acceptance Criteria

1. A first-time user can see the complete Create or Resume workflow before being asked to confirm anything.
2. Every human confirmation gate is visibly labeled.
3. Waiting messages cannot reasonably be mistaken for active execution.
4. After confirmation, the message names the actual operation that has started instead of using the generic word “implementation.”
5. Create, Resume, correction, success, and stop paths use consistent status labels.
6. Existing safety and regression tests continue to pass.

---

## 简体中文

### 问题

Context Relay 在创建或恢复交接前要求明确确认，这项安全机制是正确的。但是，目前的 Skill 没有在开始时说明完整流程，也没有标明哪些步骤需要用户操作。用户看到 Codex 回复后，可能以为已经开始执行并离开，实际上 Codex 仍在等待确认。

### 决策

Create 与 Resume 开始时，Context Relay 必须：

1. 说明总步骤数；
2. 按顺序列出全部步骤；
3. 把每一步标为 **需要确认**、**自动执行** 或 **自动完成**；
4. 说明当前步骤与状态；
5. 说明收到确认前不会开始哪项操作。

流程统一使用四种状态：

- **需要确认**：Context Relay 正在等待用户，受保护的操作尚未开始。
- **执行中**：该操作需要的确认已经完成，Context Relay 正在执行所说明的工作。
- **已完成**：操作成功结束，Context Relay 正在显示结果。
- **已停止**：安全或验证条件阻止继续执行；目前没有后台工作继续运行。

不要建议用户离开、到其他地方等待或留在画面前。只说明工作状态和当前需要的操作。

### Create 流程

Create 显示四个步骤，其中两个步骤需要确认：

1. **需要确认**：确认目标项目。
2. **需要确认**：确认当前目标与下一步。
3. **自动执行**：创建本地交接资料。
4. **自动完成**：显示结果，并说明如何在新任务中继续。

开场信息必须表达以下内容：

> **Context Relay Create 共 4 个步骤，第 1、2 步需要你确认：**
>
> 1. **需要确认**：确认目标项目
> 2. **需要确认**：确认当前目标与下一步
> 3. **自动执行**：创建本地交接资料
> 4. **自动完成**：显示结果，并说明如何在新任务中继续
>
> **目前：第 1/4 步｜需要确认**
> 请确认以下项目是否正确。确认前不会开始扫描。

显示目标与下一步时使用：

> **目前：第 2/4 步｜需要确认**
> 请检查拟定目标与下一步。明确确认前不会创建交接资料。

明确确认后使用：

> **确认部分已完成｜执行中**
> 现在开始创建本地交接资料。

成功后使用：

> **第 4/4 步｜已完成**
> 交接资料已经创建。以下是结果，以及在新任务中继续的方法。

### Resume 流程

Resume 显示三个步骤，其中一个步骤需要确认：

1. **需要确认**：确认目标项目，以及由用户在本地创建并信任的交接资料。
2. **自动执行**：验证项目与交接资料状态。
3. **自动完成**：读取允许的交接文件、重述目标并指出第一个安全操作。

开场信息必须表达以下内容：

> **Context Relay Resume 共 3 个步骤，第 1 步需要你确认：**
>
> 1. **需要确认**：确认项目与可信的本地交接资料
> 2. **自动执行**：验证项目与交接资料状态
> 3. **自动完成**：读取交接内容并指出第一个安全操作
>
> **目前：第 1/3 步｜需要确认**
> 请确认以下项目与交接资料。确认前不会开始验证。

确认后使用：

> **确认部分已完成｜执行中**
> 现在开始验证项目与交接资料状态。

成功后使用：

> **第 3/3 步｜已完成**
> 状态验证通过。以下是已确认的目标与第一个安全操作。

### 修改与停止

修改内容可能增加对话轮次，但不会增加新的确认关卡。在当前关卡得到明确确认前，状态继续显示为 **需要确认**。

如果封装程序返回不安全、已过期或无效的结果，使用：

> **已停止**
> 项目或交接资料没有通过必要检查。Context Relay 目前没有继续执行。

现有的失败即停止行为、明确核准规则、隐私边界与退出码处理保持不变。

### 实作范围

本次修改目标版本为 `0.1.2`，范围仅包括：

- `skills/context-relay/SKILL.md` 中 Create 与 Resume 的对话规则；
- `README.md` 中对应的英文与简体中文示例；
- 针对步骤数、确认标签、执行状态切换与停止状态的确定性规则测试；
- 发布 `0.1.2` 所需的 Plugin 与 Marketplace 版本资料。

除非测试发现必须进行小范围兼容性调整，否则不会修改 Python 封装程序以及核心扫描或验证逻辑。

### 不在范围内

- 不加入后台监控。
- 不加入异步进度追踪。
- 不提供时间估计或保证执行时间。
- 不减少或绕过确认关卡。
- 不修改交接资料内容、项目状态验证、隐私保证或目标项目只读行为。

### 验收标准

1. 第一次使用的用户在被要求确认前，就能看到完整的 Create 或 Resume 流程。
2. 每个人工确认关卡都有醒目标记。
3. 等待确认的信息不会被合理误解为正在执行。
4. 确认后，信息会说明真正开始的操作，不使用含糊的“开始实作”。
5. Create、Resume、修改、成功和停止路径使用一致的状态标签。
6. 现有安全测试与回归测试继续通过。
