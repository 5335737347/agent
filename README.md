# Coding Harness Demo

一个使用 Python 实现的最小 coding agent harness Demo。

项目包含模型调用、tool calling 循环、工具注册与 JSON Schema 生成，以及受工作区
路径约束的文件读取工具。它适合用于学习 coding agent 的基本执行机制，或作为后续
扩展更多编码工具的原型。

## 功能

- OpenAI Chat Completions 兼容模型接入
- 多轮对话和工具调用循环
- 同步及异步工具执行
- 根据函数签名、类型标注和 docstring 自动生成工具 Schema
- 工具参数解析、异常隔离和结果归一化
- 工作区相对路径检查
- 带行号及行数限制的 UTF-8 文件读取

当前只注册了 `read_file` 工具，因此尚不支持搜索、修改代码或运行测试。

## 环境要求

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- Ollama 或其他 OpenAI-compatible API 服务

## 运行

默认配置连接本地 Ollama：

```bash
uv run agent
```

可以通过环境变量覆盖模型配置：

```bash
MODEL_BASE_URL=http://localhost:11434/v1
MODEL_API_KEY=ollama
MODEL_NAME=deepcoder:14b
```

也可以将这些配置写入项目根目录的 `.env` 文件。

交互命令：

- `/clear`：清空当前对话
- `/exit`：退出程序

## 测试

```bash
uv run python -m unittest discover -s test -v
```

## 项目结构

```text
src/
├── agent/
│   ├── loop.py          # Agent 循环和消息模型
│   ├── main.py          # CLI 入口及依赖装配
│   └── model.py         # OpenAI-compatible 模型适配
├── sandbox/
│   └── workspace.py     # 工作区路径约束
└── tools/
    ├── base.py          # 工具抽象、Schema 和 Registry
    └── file_tools.py    # 文件读取工具
test/                    # 单元测试
docs/                    # 设计及评估文档
```

## 工作原理

```text
用户输入 → Agent → 模型 → 工具调用 → 工具结果 → 模型 → 最终回答
```

Agent 会持续处理模型返回的工具调用，直到模型给出最终文本，或达到最大模型轮次。
工具错误会作为结果返回模型处理，而不会直接终止整个 Agent 循环。

## 文档

- [架构与深度评估](docs/architecture-assessment.md)

## 当前边界

`Workspace` 只负责限制工具访问的路径，不是操作系统级安全沙箱。当前项目适合作为
教学 Demo 和开发原型，不应直接用于不可信输入或生产环境中的自动代码执行。
