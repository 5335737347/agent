# Coding Harness Demo：架构与深度评估

## 评估结论

这是一个结构清楚、核心抽象良好的 coding harness 原型，但目前更准确的定位是
“支持文件读取的 tool-calling agent Demo”，还没有形成真正可完成编码任务的
harness。

- 作为教学 Demo：完成度较高
- 作为可扩展原型：基础良好
- 作为真实 coding agent：能力尚不完整
- 作为生产系统：暂不适用

评估时项目的 24 项单元测试全部通过。

## 当前架构

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
- `src/agent/loop.py`：Agent 循环、消息类型和最大轮次控制。
- `src/agent/model.py`：OpenAI Chat Completions 兼容协议适配。
- `src/tools/base.py`：工具定义、Schema 推导、注册、调用和结果归一化。
- `src/tools/file_tools.py`：工作区文件读取工具。
- `src/sandbox/workspace.py`：工作区路径解析和越界检查。

## 设计优点

### 工具抽象完整

`@tool` 可以从 Python 函数签名、类型标注和 docstring 自动生成 JSON Schema，
覆盖 `Annotated`、`Literal`、Union、Enum、TypedDict、dataclass、集合类型、日期、
UUID 和 Path 等。对一个小型 Demo 而言，这部分已经具备较好的独立复用价值。

### 工具失败不会击穿 Agent 循环

未知工具、非法 JSON、参数绑定错误及工具内部异常都会被规范化为失败的
`ToolResult`，再反馈给模型，使模型可以理解并修正工具调用。

### 同时支持同步和异步工具

工具既可以由同步 harness 调用，也可以在异步 Agent 循环中调用。返回值和异常会
经过统一归一化。

### Agent 循环简洁

当前循环支持完整消息历史、一次响应中的多个工具调用、工具错误回填、最大模型轮次
限制，以及空字符串回答与缺失回答的区分。核心状态机规模较小，容易理解和扩展。

### 路径约束方向正确

`Workspace` 拒绝绝对路径，并通过路径规范化和 `relative_to()` 阻止普通的
`../` 及符号链接逃逸。这比简单的字符串前缀检查可靠。

### 核心测试稳定

现有测试覆盖工具 Schema、参数与异常处理、同步和异步工具、Registry、Agent 多轮
循环及基本路径逃逸。测试快速，适合后续持续回归。

## 核心不足

### 尚未形成 coding 闭环

当前只向模型注册了 `read_file`。模型不能列举目录、搜索代码、修改文件、查看
Git diff 或运行测试。因此用户必须提供准确文件路径，模型发现问题后也不能直接
修复。

建议优先增加：

- `list_files`
- `search_text`
- `apply_patch` 或受约束的写入工具
- 受控的 `exec_command`
- `git_diff`

写入和命令工具必须配套权限策略，不能只依赖 system prompt。

### Workspace 不是安全沙箱

当前实现是路径约束器，不是操作系统沙箱。它不提供进程、系统调用、网络、CPU、
内存及文件权限隔离，也没有写入审批或命令白名单。

如果以后加入 shell 工具，只使用 `Workspace.resolve()` 并不足以保障安全。路径检查
与文件操作之间理论上也存在 TOCTOU 风险。生产环境应使用 OS 或容器级隔离，并
引入独立权限策略。

### 同步工具会阻塞事件循环

`Tool.arun()` 会直接调用同步函数。读取小文件时影响有限，但搜索、Git 操作和耗时
计算会阻塞整个异步事件循环，影响并发、超时和取消。同步工具可通过
`asyncio.to_thread()` 执行，命令工具应使用异步 subprocess。

### 多工具调用串行执行

同一模型响应里的多个工具调用目前逐个执行。多个独立读取或搜索任务可以并发，但
写文件、运行命令等副作用工具不能默认并行。

建议为工具增加 `read_only`、`parallel_safe`、`requires_approval`、`timeout`
和 `max_output` 等元数据。

### 缺少资源预算和停止机制

当前只有 `max_turns`，尚未提供：

- 单次模型及工具调用超时
- 总运行时间和最大工具调用数
- token/context 预算
- 工具输出大小限制
- 用户取消向子进程的传播

真实 coding agent 很容易因大文件、无限搜索或模型反复纠错耗尽上下文。

### 会话历史无限增长

CLI 会持续保存并发送完整历史消息。所有旧工具输出都会进入后续模型请求，大型文件
可能很快占满上下文。后续需要 token 估算、历史裁剪、旧轮次摘要和大型结果外置。

### 模型适配层测试不足

`OpenAICompatibleClient` 尚无独立测试，而外部协议是最容易发生兼容问题的位置。
建议覆盖消息转换、空参数、多工具调用、空 choices、API 异常，以及不同
OpenAI-compatible 服务的兼容差异。

模型无 choices 时的 `Mode; returned no choices` 也存在拼写问题，说明该错误路径
尚未受到测试保护。

### 错误语义没有完整传入 Provider

内部 `ToolMessage` 包含 `is_error`，转换为 Chat Completions 消息时只发送文本，
模型主要依赖 `ERROR:` 前缀识别失败。后续可统一使用结构化结果：

```json
{
  "ok": false,
  "error": {
    "type": "RuntimeError",
    "message": "boom"
  }
}
```

### 工具参数没有运行时类型验证

JSON Schema 会提示模型生成正确参数，但执行阶段只通过 Python 函数签名检查参数
名称和数量，不会强制检查标注类型。可以使用 JSON Schema 或 Pydantic 做运行时
校验；涉及路径、命令和权限的工具必须自行验证输入。

### 项目包装仍处于 Demo 阶段

Python 3.14 的要求会缩小可运行环境。如果没有不可替代的 3.14 依赖，可以评估
支持 Python 3.12 或 3.13。项目还应补充配置校验、lint、类型检查、覆盖率和 CI。

## 测试缺口

建议优先增加：

- 模型适配层消息转换和错误路径
- `read_file` 截断、空文件、非法 UTF-8、目录和符号链接行为
- `max_lines <= 0` 的构造校验
- 工具超时、取消和超大输出
- 多工具调用中部分成功、部分失败
- 恶意或超大 JSON 参数
- 非法历史消息序列及 provider 异常结构
- CLI EOF、清理和异常恢复
- lint、类型检查、覆盖率和 CI

## 推荐演进路线

### 第一阶段：打通最小编码闭环

1. 增加文件发现、文本搜索、补丁写入和测试执行工具。
2. 给写入和命令操作增加明确权限边界。
3. 增加工具超时及输出上限。
4. 完善配置校验和模型适配层测试。

### 第二阶段：提高可靠性

1. 增加工具副作用、并行安全及审批元数据。
2. 并发执行独立的只读工具。
3. 增加上下文裁剪和 token 预算。
4. 输出结构化事件、耗时、用量、工具轨迹和停止原因。

### 第三阶段：形成可扩展平台

1. 增加更多 provider 适配器。
2. 支持 session/state 持久化。
3. 引入 OS 级命令沙箱和 policy engine。
4. 增加 tracing、replay 和离线 eval。
5. 建立面向真实代码仓库的端到端任务集。

## 最终判断

项目的 Agent 循环和工具包装基础是健康的，工具 Schema 部分尤其完整。当前主要
问题不是基础代码质量，而是实际 coding 能力和安全模型尚未跟上“coding harness”
的定位。

下一阶段应优先打通：

```text
发现代码 → 阅读与搜索 → 生成补丁 → 运行验证 → 检查 diff → 汇报结果
```

在完成这个闭环前，项目适合作为 tool-calling agent 的教学 Demo 和后续 harness
的原型，不适合用于不可信输入或生产环境中的自动代码执行。
