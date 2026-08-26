# Coding Harness 架构与生产化差距评估

## 1. 结论

当前项目已经具备 coding harness 的核心雏形：模型适配、Agent 循环、工具注册、
参数 Schema、异常隔离和工作区路径约束。整体结构清楚，尤其工具抽象具有较好的
复用价值。

但它目前仍是一个“支持读取文件的 tool-calling agent Demo”，而不是生产级
coding harness。主要差距不在模型调用本身，而在以下五个方面：

1. 缺少发现、修改和验证代码的完整工具链。
2. 缺少操作系统级执行隔离及权限治理。
3. 缺少预算、取消、恢复和持久化等运行控制。
4. 缺少日志、执行轨迹、指标和审计能力。
5. 缺少端到端任务、安全测试和 Agent eval。

成熟度判断：

- 教学 Demo：完成度较高
- 可扩展原型：基础良好
- 真实 coding agent：能力不完整
- 生产系统：暂不适用

评估时项目的 24 项单元测试全部通过。

## 2. 当前执行架构

```text
CLI 用户输入
    ↓
Agent.arun()
    ↓
OpenAICompatibleClient.complete()
    ↓
模型返回文本或 tool_calls
    ↓
ToolRegistry 解析参数并执行工具
    ↓
ToolMessage 回填模型上下文
    ↓
继续请求模型，直到产生最终文本或达到 max_turns
```

主要模块职责：

- `src/agent/main.py`：交互式 CLI，以及模型、工具和 Agent 的装配。
- `src/agent/loop.py`：消息模型、Agent 循环和最大轮次控制。
- `src/agent/model.py`：OpenAI Chat Completions 兼容协议适配。
- `src/tools/base.py`：工具定义、Schema 推导、注册、调用和结果归一化。
- `src/tools/file_tools.py`：工作区文件读取工具。
- `src/sandbox/workspace.py`：工作区路径解析和越界检查。

## 3. 与生产级 Coding Harness 的差距

| 维度 | 当前实现 | 生产级要求 | 差距 |
|---|---|---|---|
| Agent loop | 多轮工具调用和最大轮次 | 状态机、预算、取消、恢复 | 中 |
| Coding 工具 | 只有 `read_file` | 发现、搜索、编辑、命令、Git、测试 | 极大 |
| 模型适配 | 单一 Chat Completions 适配 | 多 Provider、流式、重试、降级 | 大 |
| 执行隔离 | 工作区路径检查 | OS/容器隔离和资源限制 | 极大 |
| 权限治理 | 无明确权限模型 | Policy、审批、风险分级和审计 | 极大 |
| 上下文 | 完整历史持续累积 | token 预算、压缩、外置结果 | 大 |
| 持久化 | 仅进程内状态 | Session、checkpoint、崩溃恢复 | 大 |
| 可观测性 | CLI 错误文本 | 日志、trace、metrics、审计 | 极大 |
| 可靠性 | 最大轮次限制 | 超时、取消、重试、熔断 | 大 |
| 工具协议 | Schema 和结果抽象较好 | 类型校验、版本和能力元数据 | 中 |
| 测试 | 组件级单元测试 | 集成、E2E、安全测试和 eval | 大 |
| 工程运营 | 尚未建立 | 配置、部署、监控、成本和容量治理 | 极大 |

## 4. 可保留的设计基础

### 4.1 Agent 边界清楚

模型接口、消息类型、工具注册表和执行循环已经分离。生产化时无需推翻当前结构，
但应把简单循环提升为显式运行状态机。

### 4.2 工具系统较为成熟

`@tool` 可以根据函数签名、类型标注和 docstring 生成 JSON Schema，覆盖：

- `Annotated`、`Literal`、Union 和 Optional
- Enum、TypedDict 和 dataclass
- list、tuple、set 和 Mapping
- datetime、date、time、UUID 和 Path
- 默认参数、必填参数和参数说明

工具系统同时处理参数解析、同步与异步调用、异常隔离和结果归一化。这是当前最接近
生产级、也最值得继续复用的部分。

### 4.3 工具故障被隔离

未知工具、非法 JSON、参数绑定错误和工具内部异常会转换为失败的 `ToolResult`，
再交给模型处理，不会直接击穿整个 Agent 循环。

### 4.4 模型接口初步解耦

`ModelClient` Protocol 使核心循环不直接依赖 OpenAI SDK，为后续增加其他 Provider
适配器保留了扩展点。

### 4.5 已建立基本路径边界

`Workspace` 通过路径规范化和 `relative_to()` 防止普通的绝对路径、`../` 和
符号链接逃逸。它不能替代安全沙箱，但作为文件工具的路径策略是合理的。

## 5. 分领域差距分析

### 5.1 缺少真实编码闭环

生产级 harness 至少需要完成以下流程：

```text
理解任务
  → 发现仓库结构
  → 搜索并阅读相关代码
  → 制定修改方案
  → 应用最小补丁
  → 运行测试和检查
  → 根据失败继续迭代
  → 检查最终 diff
  → 输出可验证报告
```

当前系统只能读取一个已知路径，无法完成其余步骤。建议补齐三组工具。

仓库发现工具：

- 列举目录和文件
- glob 查找
- 文本及正则搜索
- 分段读取文件
- 识别语言、构建系统和测试框架

修改工具：

- `apply_patch`
- 创建文件
- 受控删除或移动
- 格式化
- 修改前后哈希校验

补丁式修改比任意 `write_file` 更适合生产系统，因为它更容易审查、限制范围、检测
并发冲突、记录审计和回滚。

验证工具：

- 测试、构建、lint 和类型检查
- Git status 和 diff
- 受控项目脚本

命令执行是 coding harness 最关键、也最危险的能力，必须与沙箱和权限系统同步
建设。

### 5.2 Agent 循环缺少生产运行语义

当前循环是线性的“模型 → 工具 → 模型”。生产运行时应使用显式状态：

```text
CREATED
  ↓
RUNNING_MODEL
  ↓
WAITING_APPROVAL ──→ CANCELLED
  ↓
RUNNING_TOOLS
  ↓
VALIDATING
  ↓
COMPLETED / FAILED / BUDGET_EXCEEDED
```

需要补充：

- `run_id`、轮次 ID 和调用 ID
- token、时间、成本和工具调用预算
- 用户取消
- 审批暂停与恢复
- checkpoint
- 崩溃后恢复
- 明确的停止原因
- 部分工具失败后的处理策略

预算耗尽、用户取消和审批拒绝属于正常运行结果，不宜全部表示为异常。建议让
`AgentResult` 返回结构化状态、停止原因、用量、耗时和工具轨迹。

### 5.3 Workspace 不等于安全沙箱

当前 `Workspace` 只回答“路径是否位于工作区”，不能限制：

- 进程访问工作区外的文件
- 命令读取环境变量和凭据
- 网络数据外传
- 无限 CPU、内存或进程创建
- 后台进程残留
- 修改 `.git` 或其他敏感文件
- 脚本、编译器和子进程绕过路径检查

生产级执行链路应为：

```text
Agent
  ↓
Policy Engine
  ↓
Approval Gate
  ↓
Sandbox Executor
  ├── 文件系统挂载策略
  ├── 网络策略
  ├── 环境变量白名单
  ├── CPU/内存/进程限制
  ├── 超时和取消
  └── 输出限制
```

隔离能力可以分层实现：

1. 本地受信任模式：工作区约束加命令审批。
2. 容器模式：独立容器，只挂载目标仓库。
3. 强隔离模式：microVM、临时身份、临时凭据和受控网络代理。

`Workspace` 可以继续作为路径策略存在，但不应承担整个 sandbox 的安全语义。路径
检查与实际操作之间还需考虑 TOCTOU 风险。

### 5.4 缺少权限和审批系统

生产系统必须根据操作副作用进行风险分级。建议为工具增加能力元数据：

```python
ToolPolicy(
    effect="read",            # read/write/execute/network
    parallel_safe=True,
    approval="never",         # never/on_risk/always
    timeout_seconds=30,
    max_output_bytes=100_000,
)
```

典型默认策略：

| 操作 | 默认策略 |
|---|---|
| 读取和搜索普通源码 | 自动允许 |
| 修改工作区源码 | 按运行模式允许或审批 |
| 删除文件 | 必须审批 |
| 修改 `.git` 或密钥文件 | 拒绝 |
| 运行项目测试 | 在沙箱内受限允许 |
| 安装依赖或访问网络 | 审批并限制范围 |
| 发布、推送和部署 | 必须审批 |

审批必须由执行层强制实施，不能让模型自行判断是否安全。

### 5.5 模型适配层仍是最小实现

生产级模型网关还需要处理：

- 流式输出和请求取消
- 超时、指数退避和 rate limit
- 可重试与不可重试错误分类
- Provider failover 和模型降级
- token usage、成本和 context window
- 不完整或非标准 tool call
- Provider API 版本差异
- 幂等性和请求关联 ID

内部协议应统一为 `ModelRequest`、`ModelResponse`、`ContentBlock`、`ToolCallBlock`、
`Usage`、`FinishReason` 和 `ProviderError`，由各 Provider 适配器完成转换。

当前 `OpenAICompatibleClient` 还缺少独立测试；无 choices 时的
`Mode; returned no choices` 也存在拼写错误，说明错误路径尚未被测试保护。

### 5.6 上下文管理过于简单

当前每轮都会重新发送完整历史，工具结果和文件内容会持续累积。真实任务很容易因此
耗尽上下文。

生产系统应区分：

- **对话上下文**：用户要求、系统规则和近期关键交互。
- **工作记忆**：任务状态、已知事实、修改文件和测试状态。
- **外部工件**：完整文件、命令日志、大型 diff 和测试报告。

需要增加 token 预算、历史压缩、工具结果截断、大输出外置、内容去重和相关片段
选择。核心原则是：**会话历史不等于 Agent 状态。**

### 5.7 缺少日志和可观测性

当前主要通过 CLI 最终输出和异常文本排查问题，无法系统还原一次 Agent 运行。
生产级系统至少需要三类记录。

运行日志用于开发和现场排障，记录：

- 模型和工具调用的开始、结束及耗时
- 状态转换、重试和错误分类
- 当前轮次、预算和上下文规模
- 超时、取消和 Provider 异常

执行轨迹使用稳定事件描述完整生命周期，例如：

```text
run_created
model_request_started
model_response_received
tool_call_requested
approval_requested
tool_started
tool_finished
checkpoint_saved
run_completed
```

审计日志用于回答谁发起任务、执行了什么命令、修改了哪些文件、是否访问网络、谁
批准高风险操作，以及最终产生了哪些副作用。

所有记录应通过以下字段串联：

```text
session_id
run_id
turn_id
model_request_id
tool_call_id
sandbox_id
```

日志必须设置脱敏和长度限制，不能默认记录 API key、完整环境变量、用户凭据、私有
代码全文、完整 prompt 或无限制命令输出。未来可以把运行 ID 映射为
OpenTelemetry trace，把模型和工具调用映射为 span。

建议同时采集任务成功率、平均模型轮次、工具失败率、token 和成本、测试通过率、
审批等待时间及沙箱启动耗时等指标。

### 5.8 缺少持久化和恢复

当前状态只存在于进程内，CLI 退出后全部丢失。生产系统至少需要持久化：

- Session 和 Run
- Message、ToolCall 和 ToolResult
- Approval 和 Checkpoint
- Artifact 和 Usage
- 最终状态及停止原因

持久化用于长任务调度、审批等待、崩溃恢复、问题重放、审计和 eval。尤其在工具
产生副作用后，恢复逻辑必须知道调用尚未执行、正在执行还是已经成功，避免重复写入
或重复发布。因此副作用工具还需要幂等键或明确的恢复策略。

### 5.9 工具并发和调度能力不足

同一响应中的工具目前串行执行。生产系统应允许独立只读操作并发，同时保证写入和
命令操作有序执行，并限制单个 Run 及全局并发。

工具定义需要说明：

- 是否只读
- 是否有副作用
- 是否可取消
- 是否可重试
- 是否幂等
- 是否可并行
- 会操作哪些资源

同步工具当前会直接运行在异步事件循环中。耗时同步操作应通过线程池执行，命令工具
应使用异步 subprocess。

### 5.10 错误模型过于文本化

当前用 `ERROR: ...` 向模型表达失败，适合 Demo，但控制层还需要机器可读错误码：

```text
InvalidArguments
PermissionDenied
ApprovalRejected
Timeout
Cancelled
ResourceExceeded
SandboxViolation
ProviderUnavailable
RateLimited
TransientToolFailure
PermanentToolFailure
```

不同错误应对应不同策略：参数错误交给模型修正，限流自动退避，权限问题请求审批，
沙箱违规立即终止并审计，取消则不应被包装为普通工具错误。

工具参数目前也只进行 Python 签名绑定，没有根据类型标注执行运行时校验。可使用
JSON Schema 或 Pydantic 校验，但路径、命令和权限仍必须由工具自身验证。

### 5.11 测试体系不完整

现有 24 项单元测试是良好起点，但生产级 harness 需要分层测试：

1. **单元测试**：预算、日志、策略、模型转换和错误分类。
2. **契约测试**：不同 Provider、工具和沙箱实现满足统一接口。
3. **集成测试**：模拟模型完成搜索、修改、测试和回答。
4. **端到端测试**：在临时仓库中修复真实 Bug 并验证测试通过。
5. **安全测试**：路径逃逸、命令注入、凭据泄漏、网络外传、输出洪泛和资源耗尽。
6. **Agent eval**：评估任务完成率，而不只是代码是否按设计执行。

Agent eval 应关注：

- 修复成功率和测试通过率
- 无关修改比例
- 平均 token、时间和成本
- 人工介入次数
- 安全违规率

## 6. 推荐目标架构

```text
CLI / API / IDE
       ↓
Session Service
       ↓
Run Orchestrator
       ├── State Machine
       ├── Budget Manager
       ├── Context Manager
       ├── Approval Manager
       └── Event Recorder
       ↓
Model Gateway
       ├── OpenAI Adapter
       ├── Anthropic Adapter
       └── Local Model Adapter
       ↓
Tool Scheduler
       ├── Tool Registry
       ├── Policy Engine
       ├── Argument Validator
       └── Concurrency Controller
       ↓
Sandbox Executor
       ├── Workspace Files
       ├── Process Runner
       ├── Git Operations
       └── Resource Limits

横切能力：
Logging / Tracing / Metrics / Audit / Persistence / Secrets
```

早期不必拆成微服务。模块化单体足以支撑开发，关键是提前明确边界、运行状态和事件
模型，避免把 Provider、权限和执行逻辑继续堆入 Agent 循环。

## 7. 推荐实施路线

### P0：从 Demo 变成可工作的本地 Harness

1. 增加文件发现、搜索、`apply_patch`、受控命令和 Git diff。
2. 实现工具超时、取消和输出上限。
3. 增加基础结构化日志、关联 ID 和敏感信息脱敏。
4. 补齐模型适配层测试。
5. 建立至少一个自动修复 Bug 的端到端任务。

完成 P0 后，项目才真正具备 coding harness 的完整执行能力。

### P1：从可工作变成可靠

1. 引入显式 Run 状态机和结构化停止原因。
2. 增加 token、时间、成本和调用次数预算。
3. 实现上下文裁剪、工作记忆和大型结果外置。
4. 增加工具能力元数据及只读工具并发。
5. 持久化运行状态、执行轨迹和 checkpoint。

### P2：从可靠变成安全

1. 建立 Policy Engine 和审批机制。
2. 引入容器或更强的执行沙箱。
3. 实施环境变量白名单、网络策略和资源配额。
4. 建立副作用审计和安全测试套件。

### P3：从安全变成可运营

1. 支持多 Provider、重试、限流和降级。
2. 建立 tracing、metrics 和成本统计。
3. 支持 replay 和离线 eval。
4. 增加多任务调度、容量控制和版本化策略。

## 8. 优先级判断

当前最接近生产级的是工具抽象，差距最大的是安全执行和运行治理。短期不建议继续
投入大量时间扩展通用 Schema 类型，而应优先打通一条真实闭环：

```text
用户提交 Bug
  → Agent 搜索仓库
  → 读取相关代码
  → 应用补丁
  → 运行测试
  → 根据失败继续修改
  → 输出最终 diff 和验证结果
```

随后围绕这条闭环逐层加入日志、预算、取消、审批、持久化和沙箱。这样每项基础
设施都能通过真实任务验证，避免先构建一套尚未被执行链路使用的平台。
