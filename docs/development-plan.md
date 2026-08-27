# 开发计划：从只读 Demo 到可工作的 Coding Harness

> 版本：v1.0（2026-08-27）
> 依据：`docs/gap-quantitative-assessment.md`（量化差距）与 `docs/architecture-assessment.md`（架构评估）
> 目标读者：项目作者（单人开发）
> 本文档回答三个问题：**做什么、按什么顺序做、做完怎么验收**

---

## 0. 目标与原则

### 0.1 总体目标

把当前"只读侦察器"（生产就绪度 ~12%，SWE-bench 解出率 ~0%）建设为**可工作的本地 coding harness**（生产就绪度 ~40%），并保留通往生产级的扩展路径。

**一句话目标（Phase 0 结束时应达到）**：

> 用户提交一个真实 bug → Agent 搜索仓库 → 读取代码 → 修改文件 → 运行测试 → 根据失败迭代 → 输出最终 diff 与验证结果。**全程无人干预，5 个自建任务中至少通过 3 个。**

### 0.2 核心原则（来自两份评估文档的共同结论）

1. **先闭环，后扩展**：优先打通"发现→修改→验证→迭代"，禁止先做横向功能（多 Provider、更多 Schema 类型）。
2. **每项设施用真实任务验证**：不建设"尚未被执行链路使用"的平台设施。
3. **模型分层使用（开发省成本、验收不掺水）**：开发调试用微小模型/Mock（成本 ≈ $0），**验收与评测必须用强模型**（DeepSeek API 档）——否则无法区分"harness 的 bug"和"模型的不足"，详见 §6.1。
4. **不做过度设计**：模块化单体即可，不拆服务；状态机、预算等语义用显式代码而非框架。
5. **复用已有资产**：`tools/base.py`（Schema 推导、异常隔离）、`Workspace.resolve()`（路径策略）、`ModelClient` Protocol——不重写。

### 0.3 现状基线（实测，2026-08-27）

| 项 | 现状 |
|---|---|
| 代码 | 1,687 行 src + 264 行测试，16 个测试全通过 |
| 工具 | 3 个只读（list_files / search_text / read_file）+ 1 个空壳（patching） |
| 循环 | 线性循环，max_turns=8，无状态/预算/取消 |
| 已知缺陷 | `except A, B:` ×3（3.13- 不兼容）、"Mode;" 拼写、patching 空壳、`max_fils`/`authropic_schema` 笔误 |

---

## 1. 总体路线图

```
Phase 0  打通闭环（2–4 周）   →  可工作的本地 harness（40%）
Phase 1  可靠化   （4–6 周）   →  长任务不爆上下文、可恢复（60%）
Phase 2  安全化   （6–10 周）  →  可执行不可信任务（75%）
Phase 3  可运营   （6–10 周）  →  多模型、可观测、可评测（85%+）
```

每个 Phase 结束有**可演示的验收标准**（见各节末尾），全部完成后合计约 **4–7 个月**（1 人全职；兼职按比例延长）。

---

## 2. Phase 0：打通闭环（第 1–4 周）★ 最高优先级

### 2.1 目标

补上差距最大的"修改与验证"两段：实现补丁编辑、命令执行、测试运行、Git 查看，并建立 1 个 E2E 修复任务作为验收。

### 2.2 任务清单（按依赖顺序）

**M0.0 模型分层与 Mock 测试基础设施（1 天）★ 先做，省全部前期模型成本**

开发期 90% 的测试**不需要真实模型**——用确定性的假模型响应即可零成本验证 harness 逻辑：

- [ ] 实现 `MockModelClient`（实现现有 `ModelClient` Protocol）：
  - 场景 A：脚本化响应——给定输入消息序列，按预设脚本返回固定的 tool_calls/文本（用于测试工具执行链路）
  - 场景 B：回放模式——记录一次真实模型会话的请求/响应，之后可离线重放（用于回归测试，成本 $0）
  - 场景 C：故障注入——返回非法 JSON、无 choices、超时、重复 tool_call id（用于测试错误处理）
- [ ] 模型选择配置化：`MODEL_NAME=mock` 时启用 Mock，其余走现有 `OpenAICompatibleClient`（不改动现有装配）
- [ ] 测试约定：**工具与循环的单元/集成测试一律用 Mock**；真实模型只出现在 E2E 验收（M0.6）
- [ ] 用一个真实用例验证：`test_loop_with_mock.py`——Mock 驱动 Agent 完成"读文件→编辑→读回"三步，断言消息序列与工具参数

理由：您的 `ModelClient` Protocol 已为此预留扩展点，Mock 接入成本约 1 天，但能把 Phase 0 开发期的模型调用量压到接近零（开发期每轮调试省 $0.001–0.01，更重要的是**确定性**：mock 失败 = harness 的 bug，不存在"模型抽风"的归因歧义）。

**M0.1 修复已知缺陷（0.5 天）**
- [ ] `base.py` 三处 `except TypeError, ValueError:` → `except (TypeError, ValueError):`
- [ ] `model.py` "Mode;" → "Model;"，并为无 choices 分支补测试
- [ ] 删除 `authropic_schema` 或标记 `@deprecated`；统一 `ToolRegister`/`ToolRegistry` 命名
- [ ] `patching.py`：删除空壳（本阶段直接重写，见 M0.2）

**M0.2 编辑工具：`str_replace_editor`（2–3 天）★ 核心**

参考 Claude Code 的编辑工具设计（模型友好度高于手写 unified diff）：

```
src/tools/filesystem/editing.py
├── edit_file(path, old_string, new_string)
│     - old_string 必须唯一匹配（重复时报错并要求模型加上下文）
│     - 编辑后 read-back 校验（UTF-8、行号边界）
├── create_file(path, content)       # 拒绝覆盖已存在文件
├── delete_file(path)                # 需确认参数 allow_force=False 默认
└── 全部走 Workspace.resolve() + 变更前后 SHA-256 记录
```

设计要点：
- **不做全文件 write_file**（生产经验：全量写容易覆盖并发修改；`str_replace` 语义天然可审查、可审计、可回滚）。
- 文件系统变更事件先于实际写操作做**存在性检查**（缓解 TOCTOU，完整修复在 Phase 2）。
- 工具结果返回：编辑行号区间、新旧片段、剩余行数——帮助模型规划下一步。

**M0.3 命令执行工具：`run_command`（3–4 天）★ 核心**

```
src/tools/shell/runner.py
├── run_command(command, cwd=".", timeout=60, max_output_bytes=100_000,
│               env_allowlist=..., run_in_background=False)
├── job_kill / job_list / job_output（后台任务三件套，复用同一执行器）
└── 输出截断 + 退出码 + stderr 分离
```

设计要点：
- **cwd 强制限制在工作区内**（`Workspace.resolve()` 复用）；命令本身不做 shell 注入防护——这是工具语义，模型负责正确调用，安全边界在 Phase 2 的沙箱。
- 超时与输出上限是**硬默认值**（不可省略），防模型失控。
- 默认**不**注入用户环境变量（白名单：PATH、HOME、LANG、DSH 自有变量）。
- 后台任务与前台命令共用执行器，为 Phase 1 的 jobs 治理打基础。

**M0.4 Git 与验证工具（2–3 天）**

```
src/tools/git/status.py      → git_status(路径过滤、简短格式)
src/tools/git/diff.py        → git_diff(文件范围、--stat 摘要)
src/tools/git/commit.py      → git_commit(message)【默认禁用，Phase 2 审批后启用】
src/tools/test/run_tests.py  → run_tests(command, timeout=300)  # 复用 run_command
```

设计要点：
- `git_diff` 是 Phase 0 的**审计输出**：每轮工具调用后，模型可自查 diff，避免无效修改循环。
- `run_tests` 不做"自动发现测试命令"（不猜），由模型从 README/文档推断——这是生产 agent 的通用做法。

**M0.5 Agent 循环增强（2 天）**

- [ ] `AgentResult` 扩展：`stop_reason`（COMPLETED / MAX_TURNS / TOOL_ERROR / CANCELLED）、`turn_count`、`tool_call_count`
- [ ] 工具结果截断：`ToolMessage.content` 超过上限时保留头尾 + "…(截断 N 字符)"（防 14B 模型上下文爆掉）
- [ ] 循环内注入轻量状态提示：每轮系统侧追加"已修改文件: [x,y,z]；测试状态: 2 失败"（模型无记忆时的廉价工作记忆）

**M0.6 E2E 验收任务（3 天）★ 验收核心**

```
tasks/                          # 自建任务集（不进 src，作为测试资产）
├── bug-001-divide-by-zero/     # 每个任务 = 迷你仓库 + README 描述 + 失败测试
├── bug-002-off-by-one/
├── bug-003-encoding/
├── bug-004-import-order/
└── bug-005-null-handling/
test/e2e/test_fix_bug.py        # 驱动 Agent 修复 + 断言测试通过 + 断言无无关修改
```

每个任务包含：`repo/`（含 1 个 bug + 失败测试）、`README.md`（自然语言任务描述）、`verify.sh`（判定脚本）。
建议用 **Python 标准库项目**（不装依赖），保证可复现。

### 2.3 Phase 0 验收标准（全部满足才算完成）

1. ✅ 5 个 E2E 任务中 ≥3 个：Agent 独立完成修复，`verify.sh` 通过，且 diff 只包含必要改动（**验收模型 = L3 强模型**，§6.1）。
2. ✅ `git diff` 显示每个成功任务 ≤5 个文件的修改。
3. ✅ 所有工具都有超时/输出上限，失控命令（`yes`、死循环）不会挂死 CLI。
4. ✅ 16 个旧测试 + 新增测试全绿；`except A, B:` 类缺陷清零。
5. ✅ 工具与循环测试全部走 Mock（L0），无真实模型依赖；`MODEL_NAME=mock` 一键切换验证。
5. ✅ 完成一次真实仓库演练：对本项目仓库跑一个真实 issue（如"修复 gap 文档里的错误链接"）。

### 2.4 里程碑后检查点

Phase 0 完成后运行一次自评（对照 gap 文档第 3 节）：
- 工具链：1.5 → 6.5；安全执行：2 → 2（未动）；运行治理：2 → 3
- **生产就绪度预计 12% → ~40%**

---

## 3. Phase 1：可靠化（第 5–10 周）

### 3.1 目标

让 Agent 能跑长任务而不爆上下文、不被单次失败击穿、崩溃后可恢复。

### 3.2 任务清单

**M1.1 显式 Run 状态机（1 周）**

```
CREATED → RUNNING_MODEL ⇄ WAITING_APPROVAL(预留) → RUNNING_TOOLS → VALIDATING
        → COMPLETED / FAILED / BUDGET_EXCEEDED / CANCELLED
```
- 状态转换用枚举 + 显式异常，禁止字符串散落。
- `AgentResult` 携带结构化停止原因（替代现在的 `AgentLoopError` 文本）。

**M1.2 预算系统（1 周）**
- token 预算：每轮从响应 `usage` 累加（model.py 现在丢弃了 usage，需补上）。
- 时间预算：总墙钟时间上限；单工具超时已有（Phase 0）。
- 成本预算：`tokens × 模型单价`（配置化）。
- 预算耗尽 = 正常停止（BUDGET_EXCEEDED），不是异常。

**M1.3 上下文管理（1.5 周）★ 长任务关键**
- token 计量器：维护"当前上下文占用"估计。
- 三级分层（对应架构评估 5.6 节）：
  - 对话上下文（保留）：系统提示 + 最近 N 轮 + 用户指令。
  - 工作记忆（保留）：M0.5 的状态提示结构化（`TaskState`：已改文件/测试结果/当前假设）。
  - 外部工件（外置）：大文件内容、命令输出 → 截断 + "内容已存至 .dsh/artifacts/xxx.txt，可用 read_file 读取"。
- 压缩策略 v1（不做 LLM 摘要，太贵）：旧轮工具结果替换为一行摘要（模型无关剪枝）。
- 触发阈值：达到窗口 70% 开始裁剪。

**M1.4 持久化与恢复（1.5 周）**
- SQLite（标准库 `sqlite3`）存储：sessions / messages / tool_calls / run_state。
- `--resume <session_id>` 从崩溃点恢复；副作用工具（edit/create）记录**执行前**状态，恢复时能区分"未执行/已执行"。
- CLI 增加 `/save`、`/resume`、`/status` 命令。
- JSONL 追加日志同步启用（Phase 1 的可观测性底线，见 M1.5）。

**M1.5 结构化日志（0.5 周）**
- `logging` + JSON Lines 格式：`session_id / run_id / turn_id / tool_call_id` 关联字段。
- 脱敏：API key、env 全量值默认不落盘。
- 无第三方依赖（先不引 OpenTelemetry）。

**M1.6 只读工具并发（0.5 周）**
- 同一响应中多个只读工具调用并行执行（`asyncio.gather`）；写入工具保持串行。
- 工具增加元数据：`effect: read|write|execute`（为 Phase 2 Policy 铺路）。

### 3.3 Phase 1 验收标准

1. ✅ 一个 30+ 轮的长任务（如"跨 10 个文件的重构"）不爆上下文、正常完成。
2. ✅ 任务中途 `Ctrl-C` 后 `/resume` 能继续（已完成的编辑不重复执行）。
3. ✅ 预算耗尽的任务返回 `BUDGET_EXCEEDED` 结构化结果，CLI 可读。
4. ✅ 日志可通过 `session_id` 完整还原一次运行（工具轨迹可回放）。
5. ✅ 5 个 E2E 任务在"每任务 100k token 预算"下全部通过（预算不再是隐形天花板）。

---

## 4. Phase 2：安全化（第 11–20 周）

### 4.1 目标

从"仅路径检查"升级为"策略 + 审批 + 进程隔离"，使 harness 可执行不可信任务。

### 4.2 任务清单

**M2.1 Policy Engine（2 周）**

```python
ToolPolicy(
    effect="read",           # read / write / execute / network
    approval="never",        # never / on_risk / always
    timeout_seconds=30,
    max_output_bytes=100_000,
    sensitive_patterns=[".git/*", "*.env", "*.pem"],
)
```
- 默认策略表（来自架构评估 5.4 节）：读/搜自动允许；修改按模式；删除必须审批；`.git`/密钥拒绝；测试沙箱内允许；网络审批。
- 策略**由执行层强制**，模型永远无法绕过。

**M2.2 审批 Gate（1.5 周）**
- 高风险操作 → 暂停 Run → CLI/Web 询问（复用 `ask_user_question` 语义）。
- 审批结果持久化（Phase 1 的 SQLite），支持"本次会话记住"。

**M2.3 进程沙箱（3–5 周）★ 工作量最大**
- 分档实施（不一步到位）：
  - L1（1 周）：`resource.setrlimit`（CPU/内存/进程数）+ 超时 + 输出上限 + 环境白名单。纯标准库，立即生效。
  - L2（2 周）：Linux 用 **Landlock**（Python 无官方绑定，用 `ctypes` 调 `landlock_create_ruleset`，或封装 `bubblewrap`）限制文件系统访问；非 Linux 降级 L1。
  - L3（可选，2 周）：Docker 容器（`docker run --rm --network none -v workspace:/ws`），跨平台一致。
- 网络策略：L2 阶段"默认禁网"，白名单式放开（模型 API 调用在 harness 进程内，不受影响）。

**M2.4 安全测试套件（2 周）**
- 路径逃逸（`../`、symlink、`/proc` 穿透、TOCTOU 竞态）
- 命令注入（`;`、`$( )`、`|`、`${IFS}`、换行拼接）
- 凭据泄漏（env 全量注入 → 白名单后断言不出现）
- 资源耗尽（fork bomb、无限输出、大文件读取）
- 网络外传（沙箱内 curl 外部地址 → 断言失败）
- 每类攻击 ≥1 个测试用例，全部 fail-closed。

**M2.5 副作用审计（1 周）**
- 每次写/执行记录：操作、路径、哈希、审批者、会话上下文。
- 审计日志只追加、不可被 Agent 修改。

### 4.3 Phase 2 验收标准

1. ✅ 安全测试套件全绿（含 TOCTOU 竞态用例）。
2. ✅ 沙箱内 Agent 无法读取工作区外文件、无法外传数据、无法耗尽资源。
3. ✅ 删除/网络/安装依赖必须审批，审批拒绝 = 正常停止原因。
4. ✅ 审计日志可回答：谁/何时/执行了什么/改了哪些文件/谁批准的。
5. ✅ 5 个 E2E 任务在 L1 沙箱下仍全部通过（安全不牺牲功能）。

---

## 5. Phase 3：可运营（第 21–30 周）

### 5.1 目标

多模型、可观测、可评测——达到"可被他人使用和评估"的程度。

### 5.2 任务清单

- **M3.1 多 Provider 网关（2 周）**：Anthropic / DeepSeek / OpenAI 适配器；流式输出；重试与退避（可重试/不可重试错误分类）；Provider failover。
- **M3.2 模型切换策略（1 周）**：轻量模型规划 + 重型模型执行（architect/editor 双模型，Aider 模式）；按工具类型路由。
- **M3.3 指标采集（1 周）**：任务成功率、平均轮次、工具失败率、token/成本、测试通过率、审批等待时间；`/metrics` 输出。
- **M3.4 离线 Eval（2 周）**：把 `tasks/` 扩展为可配置评测集（含 SWE-bench 子集尝试）；`eval.py --set mini --model deepseek` 一键跑分；结果入 SQLite 可对比。
- **M3.5 会话管理界面（1–2 周）**：CLI 子命令完善（`agent run --task`、`agent resume`、`agent eval`、`agent audit`），Web UI 可选。
- **M3.6 文档与打包（1 周）**：README 重写（安装/配置/安全模型/限制）、`pyproject` 完善、发布 `pip install` 可用包。

### 5.3 Phase 3 验收标准

1. ✅ 同一 E2E 任务集在 ≥2 个 Provider 上可跑并产出可比分数。
2. ✅ 架构评估 P3 清单全部落地（多 Provider、重试、限流、降级、tracing、metrics、replay、eval、调度）。
3. ✅ 他人按 README 可在 10 分钟内跑通 `tasks/bug-001` 的完整修复流程。

---

## 6. 关键技术选型（建议）

| 决策点 | 建议 | 理由 |
|---|---|---|
| 补丁编辑 | `str_replace` 语义优先；unified diff 留到 Phase 2 | 模型调用成功率最高；生产 agent（Claude Code）同款 |
| 命令执行 | `asyncio.create_subprocess_exec`（非 `shell=True`） | 避免 shell 层二次解析；跨平台 |
| 沙箱 L1 | `resource.setrlimit`（标准库） | 零依赖立即生效 |
| 沙箱 L2 | Landlock（ctypes 封装）或 bubblewrap | Linux 原生、无需容器 daemon |
| 持久化 | SQLite（标准库 `sqlite3`） | 零依赖、可查询、单文件 |
| 日志 | `logging` + JSON Lines | 零依赖、机器可读 |
| 测试 | pytest（从 unittest 迁移）+ `tasks/` E2E 资产 | 参数化/夹具生态 |
| 配置 | `.env` + 简单 dataclass（不引 pydantic） | 保持轻量 |
| 模型策略 | **三层分层**（见 §6.1）：开发=Mock/微模型（$0），集成=中档，验收=强模型 | 省成本 + 消除归因歧义；API 成本约为订阅制 1/10–1/30 |

### 6.1 模型分层策略（前期省钱的关键，附成本数字）

> 核心原则：**开发调试的成本应该趋近于零，但验收评测的成本一分不能省。** 微小模型的价值在"快速、免费地跑通流程"，不在"证明 harness 有效"。

| 层 | 模型 | 用途 | 单次成本 | 何时用 |
|---|---|---|---|---|
| **L0 确定性层** | `MockModelClient`（脚本化/回放/故障注入） | 工具链路、循环逻辑、错误处理的单元与集成测试 | **$0**，毫秒级 | 开发期 90% 的测试（M0.0） |
| **L1 微模型层** | deepcoder:14b（现有 Ollama）或更小（Qwen3-Coder-8B/4B） | 手工冒烟测试：跑通"读→改→测"手工流程、CLI 体验、找 harness 的明显问题 | **$0**（本地推理） | 日常交互调试 |
| **L2 中档层** | DeepSeek API 便宜档 / 32B 本地 | 小规模集成验证：1–2 个最简 E2E 任务、跨工具联调 | ~$0.01–0.1/任务 | 每周集成检查 |
| **L3 验收层** | DeepSeek V3.x API（或 GPT/Claude API） | **正式验收与评测**：5 个 E2E 任务、Phase 里程碑验收、后续 eval | ~$0.1–1/任务（DeepSeek 档） | 每个 Phase 结束、里程碑验收 |

**为什么不能用微模型做验收（验证偏差陷阱）：**

1. **归因歧义**：微模型工具调用失败率 30–70%，一旦 E2E 失败，无法判断是 harness 的 bug 还是模型能力不足——调试将变成猜谜。
2. **过度适配**：为容忍微模型的错误输出（错 JSON、old_string 不匹配）而加的容错逻辑，可能拖累强模型（多余的重试、模糊匹配反而引入错误修改）。
3. **假阴性验收**：Phase 0 验收"5 任务 ≥3 通过"用 14B 跑大概率失败（期望 5–20%），会让您误判 harness 未就绪，实际是模型问题。
4. **反向也成立**：Mock 驱动通过 ≠ 真实模型可用。两层都要有：Mock 保"harness 逻辑对"，L3 保"整机端到端通"。

**成本账（Phase 0 全期，对比）**：
- 全用 API 强模型开发调试：约 $50–150（按 500 轮调试 × 0.1–0.3 美元）
- 按本分层：**约 $5–15**（仅验收期使用 L3），省 90%+，且调试体验更好（确定性 + 毫秒级响应）

**落地约束**：
- L0/L1 之间切换 = 改 `MODEL_NAME` 环境变量，零代码改动（M0.0 已保证）
- 每次里程碑验收前，用 L3 跑一次完整 E2E 并**记录分数存档**（供 Phase 3 的 eval 对比基线）
- L1 微模型层发现的问题，先自查 harness（日志/轨迹），确认非 harness 问题后才归因模型——避免把模型当"背锅侠"

**明确不做（本计划范围外）**：IDE 插件、MCP server 生态、多用户/团队、云托管、GUI 化——这些是生产级产品的方向，不是个人 harness 的下一步。

---

## 7. 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|
| 14B 模型无法完成 E2E 任务（Phase 0 验收失败） | 高 | 高 | **已由模型分层策略消除归因歧义**（§6.1）：验收一律用 L3 强模型跑；若 L3 仍失败才是 harness 问题。开发期用 L0/L1 不涉及此风险 |
| `str_replace` 语义导致模型反复失败（old_string 不匹配） | 中 | 中 | 报错信息给出模糊匹配建议（difflib）；允许"读取后编辑"模式 |
| 命令执行导致环境损坏（误删、污染仓库） | 中 | 高 | Phase 0 就要求 E2E 在 git 仓库内跑（可 `git checkout` 还原）；`delete_file` 默认拒绝 |
| 范围蔓延（想先做多 Provider/Web UI） | 高 | 中 | 严格执行 Phase 顺序；每阶段结束才允许评估下一阶段 |
| 沙箱实施拖期（Landlock ctypes） | 中 | 中 | L1 先交付（已有价值）；L2 允许降级 bubblewrap；L3 明确为可选项 |
| 上下文裁剪破坏任务连续性 | 中 | 中 | 裁剪只作用于"外部工件"，对话/工作记忆不动；用 30 轮长任务回归测试守护 |

---

## 8. 每周节奏建议（Phase 0 示例）

| 周 | 目标 | 里程碑 |
|---|---|---|
| 第 1 周 | M0.1 + M0.2 | 缺陷清零；edit_file 可用（手工验证） |
| 第 2 周 | M0.3 + M0.4 | run_command + git diff + run_tests 可用 |
| 第 3 周 | M0.5 + M0.6 前半 | Agent 循环增强；tasks/bug-001~002 建立 |
| 第 4 周 | M0.6 后半 + 验收 | 5 任务 ≥3 通过；真实仓库演练；写 Phase 0 复盘 |

**每周固定动作**：跑全部测试 → 跑一次 E2E 任务 → 更新本文档"进度"小节（见 §9）。

---

## 9. 进度追踪

> 每阶段完成或每周更新时，在此记录。格式：`[x] 完成 / [ ] 未完成 + 阻塞原因`。

### Phase 0（目标：第 1–4 周）
- [ ] M0.1 缺陷修复
- [ ] M0.2 str_replace_editor
- [ ] M0.3 run_command + jobs
- [ ] M0.4 git/验证工具
- [ ] M0.5 循环增强
- [ ] M0.6 E2E 任务集
- **验收**：5 任务 ≥3 通过：____ / 生产就绪度复评：____

### Phase 1（目标：第 5–10 周）
- [ ] M1.1 状态机 / [ ] M1.2 预算 / [ ] M1.3 上下文 / [ ] M1.4 持久化 / [ ] M1.5 日志 / [ ] M1.6 并发
- **验收**：30 轮长任务 + 崩溃恢复 + 预算停止：____

### Phase 2（目标：第 11–20 周）
- [ ] M2.1 Policy / [ ] M2.2 审批 / [ ] M2.3 沙箱 L1-L3 / [ ] M2.4 安全测试 / [ ] M2.5 审计
- **验收**：安全套件全绿 + E2E 不退化：____

### Phase 3（目标：第 21–30 周）
- [ ] M3.1 多 Provider / [ ] M3.2 双模型 / [ ] M3.3 指标 / [ ] M3.4 Eval / [ ] M3.5 会话界面 / [ ] M3.6 打包
- **验收**：双 Provider 可评测 + 他人 10 分钟跑通：____

---

## 10. 下一步（明天就能开始）

1. 修 4 个已知缺陷（M0.1，0.5 天）。
2. 实现 `MockModelClient`（M0.0，约 1 天）——**先做这个**：后续所有工具开发都在 Mock 下零成本测试。
3. 实现 `src/tools/filesystem/editing.py` 的 `edit_file`（M0.2 核心，约 150–200 行 + Mock 测试）。
4. 模型冒烟（L1，可选）：用 `deepcoder:14b` 手工试一次"读文件 → 改文件"，确认模型能正确输出 `str_replace` 参数——注意这只是**冒烟**，不作为验收依据（验收用 L3）。
