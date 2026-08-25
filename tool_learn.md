# tool_learn — `src/tools/base.py` 详解

> 学习文档：编码 harness 的工具抽象层与注册表。
> 对应文件：`src/tools/base.py`（约 860 行，Python 3.14 项目，无第三方依赖，纯标准库实现）。

---

## 目录

1. [模块定位](#1-模块定位)
2. [设计哲学](#2-设计哲学)
3. [整体架构](#3-整体架构)
4. [类型别名与常量](#4-类型别名与常量)
5. [ToolResult — 结果封装](#5-toolresult--结果封装)
6. [类型翻译器 _type_to_schema（核心）](#6-类型翻译器-type_to_schema核心)
7. [辅助函数](#7-辅助函数)
8. [Tool 类 — 工具本体](#8-tool-类--工具本体)
9. [tool() 工厂 / 装饰器](#9-tool-工厂--装饰器)
10. [ToolRegister — 注册与分发](#10-toolregister--注册与分发)
11. [完整数据流](#11-完整数据流)
12. [设计要点总结](#12-设计要点总结)
13. [注意事项与坑](#13-注意事项与坑)

---

## 1. 模块定位

这个模块解决的是 **AI 编码 harness**（让大模型自主调用工具执行任务的框架）中最基础的问题：

> 如何把一个普通的 Python 函数，变成大模型（LLM）能够理解、能够调用、调用结果能被解析的「工具」？

大模型（如 OpenAI、Anthropic）无法直接调用 Python 函数。它们通过「工具调用」（tool use / function calling）机制工作：

1. **模型侧**：宿主把每个工具描述成 JSON Schema 发给模型 → 模型根据描述生成一次调用（工具名 + JSON 格式的参数）。
2. **宿主侧**：解析模型生成的参数 → 执行真正的 Python 函数 → 把结果（文本）返回给模型继续推理。

因此模块有三大职责：

| 职责 | 对应组件 |
|---|---|
| **翻译**：函数签名/注解 → JSON Schema | `_type_to_schema()`、`tool()` |
| **执行**：参数校验、同步/异步调用、异常隔离 | `Tool.run()` / `Tool.arun()` |
| **管理**：注册、查重、分发、按 provider 导出 | `ToolRegister` |

---

## 2. 设计哲学

全文贯穿一条核心原则：

> **错误绝不外抛。** 所有失败路径（工具不存在、JSON 解析失败、参数绑定失败、函数抛异常）都归一化为 `ToolResult(is_error=True)` 返回。

为什么？因为 agent 主循环是「模型 ↔ 工具」的对话循环，任何异常中断都会让循环崩溃。把错误变成一条文本消息（如 `ERROR: Unknown tool 'foo'`）返回给模型，模型看到错误可以**自己修正**（换个参数、换个工具重试）。这是 agent 框架的经典错误处理范式。

第二原则：

> **同步/异步双通道。** `run()` 供同步代码调用，`arun()` 供异步 agent 循环调用，两者行为一致、错误语义一致。

第三原则：

> **与 provider 解耦。** 内部只维护一份统一的 JSON Schema，导出时才转换为 OpenAI / Anthropic 各自的格式。新增 provider 只需加一个 `to_xxx_schema()` 方法。

---

## 3. 整体架构

```
┌────────────────────────────────────────────────────┐
│                   使用方代码                        │
│   @tool 装饰器 / ToolRegister.register()           │
└──────────────────────┬─────────────────────────────┘
                       │
┌──────────────────────▼─────────────────────────────┐
│  tool() 工厂                                       │
│  ├─ inspect.signature → 参数清单                    │
│  ├─ _resolved_type_hints → 类型注解                 │
│  ├─ _parse_docstring → 描述 + 参数描述               │
│  └─ _type_to_schema → 每个参数的 JSON Schema         │
└──────────────────────┬─────────────────────────────┘
                       │ 产出 Tool 对象
┌──────────────────────▼─────────────────────────────┐
│  Tool                                               │
│  ├─ to_openai_schema() / to_anthropic_schema()      │
│  ├─ run()  → 同步执行（异常隔离 → ToolResult）        │
│  └─ arun() → 异步执行（异常隔离 → ToolResult）        │
└──────────────────────┬─────────────────────────────┘
                       │ 注册进
┌──────────────────────▼─────────────────────────────┐
│  ToolRegister（别名 ToolRegistry）                  │
│  ├─ openai_schema() / anthropic_schema()            │
│  └─ run() / arun() 按名分发                          │
└──────────────────────┬─────────────────────────────┘
                       │
┌──────────────────────▼─────────────────────────────┐
│           LLM provider（OpenAI / Anthropic API）    │
└────────────────────────────────────────────────────┘
```

---

## 4. 类型别名与常量

```python
JSONSchema = dict[str, Any]                    # 任何 JSON Schema 片段
ToolCallable = Callable[..., Any]              # 可被包装成工具的可调用对象
ToolArguments = str | Mapping[str, Any] | None # provider 传来的参数：JSON 文本 / dict / 空
```

四个正则 / 集合常量：

```python
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# 工具名限制：1-64 位，仅字母/数字/下划线/连字符
# 这是 OpenAI/Anthropic API 的共同限制（Anthropic 连 64 位上限都是一致的）

_PARAM_RE = re.compile(
    r"^(?P<name>[A-Za-z_]\w*)(?:\s*\([^)]*\))?\s*:\s*(?P<description>.*)$"
)
# Google 风格 docstring 参数行：`name (type): description` 或 `name: description`
# 用 (?P<name>) 命名分组，后续用 m["name"] 取值

_SPHINX_PARAM_RE = re.compile(
    r"^:param\s+(?P<name>[A-Za-z_]\w*)\s*:\s*(?P<description>.*)$"
)
# Sphinx 风格：`:param name: description`

_ARG_SECTIONS = {"args:", "arguments:", "parameters:"}
# 进入参数段的标记行（Google 风格）

_END_SECTIONS = {"attributes:", "examples:", "notes:", "raises:", "returns:", ...}
# 遇到这些段即停止解析 —— description 只保留参数段之前的内容
```

---

## 5. ToolResult — 结果封装

```python
@dataclass(frozen=True, slots=True)
class ToolResult:
    content: str
    is_error: bool = False
```

| 装饰器 | 作用 |
|---|---|
| `@dataclass` | 自动生成 `__init__`、`__repr__`、`__eq__` |
| `frozen=True` | 不可变（agent 循环中到处传递，防意外修改） |
| `slots=True` | 无 `__dict__`，省内存、访问更快 |

```python
@classmethod
def ok(cls, content: str) -> ToolResult: ...
@classmethod
def error(cls, message: str) -> ToolResult: ...  # is_error=True

def __str__(self) -> str:
    return self.content   # 直接可拼进模型消息文本
```

**为什么需要这个包装而不是直接返回字符串？**
因为 agent 循环需要区分「工具正常返回了 'not found'」和「工具报错了」——两者对模型的意义完全不同。`is_error=True` 的结果模型会看到 `ERROR: ...` 前缀（由 `_error_result` 生成），从而意识到自己调用错了。

---

## 6. 类型翻译器 `_type_to_schema`（核心）

### 6.1 作用

把 Python 类型注解翻译成 JSON Schema 片段。这是全模块最复杂的函数，约 150 行，需要逐个处理 Python 类型系统的每一种形态。

### 6.2 基础映射表

```python
_TYPE_MAP = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    list: {"type": "array", "items": {}},
    ...
}
```

注意查表时用 `copy.deepcopy`：

```python
if annotation in _TYPE_MAP:
    return copy.deepcopy(_TYPE_MAP[annotation])
```

**为什么 deepcopy？** 因为后续逻辑可能向 schema 里注入 `description` / `default` 键，如果直接返回共享字典，一次调用就会污染全局表，导致下一次翻译带上别人的描述。

### 6.3 处理分支一览（按代码顺序）

| 分支 | 触发条件 | 产物 | 备注 |
|---|---|---|---|
| 空/Any | `inspect.Parameter.empty` 或 `Any` | `{}` | 空 schema = 允许任意值 |
| None | `None` / `type(None)` | `{"type": "null"}` | |
| 字符串注解 | `isinstance(annotation, str)` | `{}` | 前向引用字符串无法解析，放弃 |
| 递归保护 | `annotation in seen` | `{}` | 自引用类型防死循环 |
| `Annotated` | `origin is Annotated` | 基础类型 schema + 元数据 | 字符串元数据→`description`；Mapping 元数据→直接合并 |
| `Required`/`NotRequired` | origin 匹配 | 解包内层 | TypedDict 限定符 |
| `Literal` | origin 匹配 | `{"enum": [...]}` | 值类型唯一时附带 `"type"` |
| `Union` / `\|` | origin 匹配 | `{"anyOf": [...]}` | **choices 去重** |
| `TypeVar` | `isinstance(annotation, TypeVar)` | constraints → anyOf；bound → 其 schema；否则 `{}` | |
| `NewType` | 有 `__supertype__` | 递归翻译底层类型 | NewType 不可见、透明解包 |
| 基础类型 | 在 `_TYPE_MAP` | deepcopy 的映射 | |
| `list`/`Sequence`/`set` | origin 匹配 | `array` + items | set 系列加 `uniqueItems: True` |
| `tuple` | origin 匹配 | 定长 → `prefixItems`；`tuple[T, ...]` → 变长 | prefixItems 是 JSON Schema 2020-12 语法 |
| `dict`/`Mapping` | origin 匹配 | `object` + `additionalProperties` | 键类型不翻译——JSON 键永远是字符串 |
| 日期时间 | `datetime`/`date`/`time` | `string` + format | |
| `UUID` | 精确匹配 | `string` + `"uuid"` | |
| `Path` | `issubclass(PurePath)` | `string` + `"path"` | 兼容 `pathlib.Path` |
| `Enum` | `issubclass(Enum)` | `enum` 值列表 + 值类型 | 与 Literal 同款逻辑 |
| `TypedDict` | `is_typeddict()` | object + required 列表 | 见 6.4 |
| `dataclass` | `is_dataclass()` | object + required + default | 见 6.5 |
| 其他 | 兜底 | `{}` | 无法翻译的类型就放行（宁松勿严） |

### 6.4 递归保护机制

```python
def _type_to_schema(annotation, seen: frozenset[Any] | None = None):
    ...
    seen = seen or frozenset()
    try:
        if annotation in seen:
            return {}
    except TypeError:
        pass          # 有些注解不可哈希，跳过检查
    ...
    next_seen = seen | {annotation}   # 只有 TypedDict/dataclass 分支传下去
```

只有**结构类型**（TypedDict、dataclass）才可能递归引用自身（如树的节点含子节点列表）。`seen` 是一个不可变的 `frozenset`，沿递归链路向下传递——一旦某个类型已经在祖先路径上出现过，就返回 `{}` 剪枝，避免无限递归。基础类型分支不传 `seen`，因为它们的递归深度固定为 1。

### 6.5 递归类型：TypedDict

```python
if is_typeddict(annotation):
    hints = _resolved_type_hints(annotation)
    required_keys = set(getattr(annotation, "__required_keys__", ()))
    optional_keys = set(getattr(annotation, "__optional_keys__", ()))
    # 扫描每个字段，根据 Required/NotRequired 限定符修正必需性
    # __total__=False 的 TypedDict：默认所有键可选
```

要点：
- `__required_keys__` / `__optional_keys__` 是 TypedDict 的运行时元数据（Python 3.9+）
- `Required[T]` / `NotRequired[T]` 限定符会**覆盖** `total` 语义
- 输出 `additionalProperties: False` —— 严格模式，模型传多余字段会被 provider 校验拒绝

### 6.6 递归类型：dataclass

```python
if inspect.isclass(annotation) and is_dataclass(annotation):
    for item in fields(annotation):
        schema = _type_to_schema(hints.get(item.name, item.type), next_seen)
        if item.default is MISSING and item.default_factory is MISSING:
            required.append(item.name)                 # 无默认值 → 必填
        elif item.default is not MISSING and _is_json_value(item.default):
            schema["default"] = item.default           # 默认值可序列化 → 注入 default
```

细节：
- `MISSING` 是 `dataclasses` 提供的哨兵值，用于区分「没有默认值」和「默认值是 None」——`None` 是合法默认值，不能靠 `is None` 判断
- 默认值必须**能 JSON 序列化**才注入 `default`（不可序列化的默认值如 `Path`、函数，只能丢弃，否则发出去的 schema 是无效 JSON）

### 6.7 一个完整的翻译示例

```python
from typing import Literal, Annotated
from dataclasses import dataclass

class Model(Enum):
    GPT4 = "gpt-4"
    SONNET = "sonnet"

@dataclass
class Filter:
    field: str
    limit: int = 10
    direction: Literal["asc", "desc"] = "asc"

def query(
    table: str,
    model: Model,
    filters: list[Filter],
    page: int = 1,
    options: dict[str, str] | None = None,
    sort: Annotated[str, "排序字段"] = "id",
) -> None: ...
```

翻译结果（简化）：

```json
{
  "type": "object",
  "properties": {
    "table":      {"type": "string"},
    "model":      {"type": "string", "enum": ["gpt-4", "sonnet"]},
    "filters":    {"type": "array", "items": {
                    "type": "object", "additionalProperties": false,
                    "properties": {"field": {"type": "string"},
                                   "limit": {"type": "integer", "default": 10},
                                   "direction": {"enum": ["asc", "desc"], "default": "asc"}},
                    "required": ["field"]}},
    "page":       {"type": "integer", "default": 1},
    "options":    {"anyOf": [{"type": "object", "additionalProperties": {"type": "string"}},
                             {"type": "null"}]},
    "sort":       {"type": "string", "description": "排序字段", "default": "id"}
  },
  "required": ["table", "model", "filters"],
  "additionalProperties": false
}
```

注意 `X | None` 翻译成了 `anyOf: [X, null]` —— 这是 JSON Schema 表达「可空」的标准方式。

---

## 7. 辅助函数

### 7.1 `_resolved_type_hints` — 解析前向引用

```python
try:
    return get_type_hints(target, include_extras=True)
except NameError, TypeError:
    return dict(getattr(target, "__annotations__", {}))
```

- `get_type_hints` 会把字符串形式的注解（前向引用 `"Filter"`）解析成真实类型
- 解析失败（如类型在闭包作用域、循环导入）时回退到原始字符串注解
- `include_extras=True` 很重要：否则 `Annotated` 元数据会被剥掉
- ⚠️ `except NameError, TypeError:` 是 **PEP 758 语法**（Python 3.14+ 允许不带括号的 except），等价于 `except (NameError, TypeError):`。在 3.14 之前的 Python 里这是语法错误

### 7.2 `_is_json_value` — 可序列化性检测

```python
try:
    json.dumps(value)
except TypeError, ValueError:
    return False
return True
```

用于判断「函数默认值能不能写进 JSON Schema 的 `default`」。

### 7.3 `_format_result` — 返回值 → 模型文本

```python
if isinstance(value, str):
    return value                                  # 字符串原样（不带引号）
if isinstance(value, bytes):
    return value.decode("utf-8", errors="replace") # 二进制解码（replace 容错）
try:
    return json.dumps(value, ensure_ascii=False)   # 其他结构 → 紧凑 JSON
except TypeError, ValueError:
    return str(value)                              # 不可序列化 → str() 兜底
```

注意顺序：字符串优先原样返回，而不是 `json.dumps`（否则会带引号）。`ensure_ascii=False` 保证中文不被转成 `\uXXXX`。

### 7.4 `_error_result` — 异常 → 失败结果

```python
detail = str(error)
suffix = f": {detail}" if detail else ""
return ToolResult.error(f"ERROR: {type(error).__name__}{suffix}")
```

产出如 `ERROR: FileNotFoundError: [Errno 2] No such file...`。带上异常类型名，模型能根据类型名判断错误性质。

### 7.5 `_parse_docstring` — 文档字符串解析

**作用**：从函数 docstring 中提取 ① 工具描述（发给模型的第一印象）② 每个参数的描述（注入 schema 的 `description`）。

**状态机**：`in_arguments` 标志 + `current_parameter` 追踪当前参数。

流程（逐行）：

```
:param x: desc          ← Sphinx 风格，直接记录，跳过其余检查
args: / arguments: / parameters:
                        ← 进入参数段（Google 风格）
name (type): desc       ← 参数行，记录当前参数名和描述
  continuation text     ← 缩进续行，拼接到上一个参数的描述
returns: / raises: / ...← 结束段，break 停止解析
（参数段之外）非 : 开头的行 → description_lines（工具描述）
```

**关键设计**：
- Google 风格 `name (type): desc` 用正则 `(?:\s*\([^)]*\))?` 吃掉可选的 `(type)` 部分
- 参数段内**空行不重置** `current_parameter`？—— 不，空行 `stripped` 为空，`if stripped and current_parameter` 为假，什么都不做。也就是说空行后接的续行仍会拼到上一个参数
- 遇到 `_END_SECTIONS`（Returns/Examples/Raises...）直接 `break`：这些段后的内容是给人类看的，不适合发给模型

### 7.6 `_validate_tool_name` — 名称校验

```python
if not _TOOL_NAME_RE.fullmatch(name):
    raise ValueError("Tool name must be 1-64 characters containing only letters, numbers, underscores, or hyphens")
```

注意用 `fullmatch`（整体匹配）而非 `match`（前缀匹配）。这是**唯一**会在注册期抛异常的地方——非法名称发出去会被 provider API 拒绝，早抛比晚抛好。

---

## 8. Tool 类 — 工具本体

### 8.1 结构

```python
@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: JSONSchema
    func: ToolCallable

    def __post_init__(self):
        _validate_tool_name(self.name)     # 名称合法性
        if not callable(self.func): raise TypeError(...)
        if not isinstance(self.parameters, dict): raise TypeError(...)
```

注意这里没有 `frozen=True`（Tool 本身不需要不可变），`__post_init__` 做构造时校验——**fail fast**。

### 8.2 Provider 格式导出

```python
def to_openai_schema(self):
    return {
        "type": "function",
        "function": {
            "name": self.name,
            "description": self.description,
            "parameters": copy.deepcopy(self.parameters),  # 深拷贝防篡改
        },
    }

def to_anthropic_schema(self):
    return {
        "name": self.name,
        "description": self.description,
        "input_schema": copy.deepcopy(self.parameters),
    }
```

两份 schema 再次 deepcopy——调用方拿到导出结果后随便改，不影响内部状态。

### 8.3 `run()` — 同步调用（决策树）

```
                    run(**kwargs)
                        │
                        ▼
            ┌─ _argument_error(kwargs) ──┐
            │  (signature.bind 预校验)    │
            └──────────┬─────────────────┘
                       │ 绑定失败 → ERROR: invalid arguments
                       ▼
        func 是协程函数 ?
        ┌────┴────┐
       是│        │否
        ▼        ▼
  有运行中的事件循环?    value = func(**kwargs)
  ┌────┴────┐          │
 是│        │否        │ value 是 awaitable?
  │        ▼          ┌┴─────────┐
  │   asyncio.run(   是│         │否
  │   self.arun())    ▼         ▼
  │             有运行中的事件循环?   _normalize_result(value)
  │             ┌───┴────┐
  │            是│       │否
  │             ▼       ▼
  │    报错要求用 arun()  asyncio.run(self._await_result(value))
  │    (协程会被 close())   │
  │                        ▼
  └────────→ 最终都归一化为 ToolResult
```

核心逻辑分两层：

**第一层：拒绝嵌套事件循环。** `asyncio.run()` 在「已有运行中的循环」时会抛 `RuntimeError: asyncio.run() cannot be called from a running event loop`，所以代码先探测：

```python
try:
    asyncio.get_running_loop()
except RuntimeError:          # 没有运行中的循环
    return asyncio.run(self.arun(**kwargs))
return ToolResult.error("...must be called with arun()...")
```

**第二层：返回 awaitable 的普通函数同样处理**——同步函数也可以 `return some_async_fn()`，`inspect.isawaitable(value)` 探测到就照第一层逻辑走；协程对象还要 `value.close()` 防止「coroutine was never awaited」警告。

**第三层：异常隔离。**

```python
try:
    value = self.func(**kwargs)
except Exception as error:  # noqa: BLE001
    return _error_result(error)   # 绝不外抛
```

### 8.4 `arun()` — 异步调用

```python
async def arun(self, **kwargs):
    argument_error = self._argument_error(kwargs)
    if argument_error is not None:
        return argument_error
    try:
        if inspect.iscoroutinefunction(self.func):
            value = await self.func(**kwargs)
        else:
            value = self.func(**kwargs)
            if inspect.isawaitable(value):
                value = await value
    except Exception as error:
        return _error_result(error)
    return self._normalize_result(value)
```

比 `run()` 简单：异步上下文里不需要处理「没有循环」的情况，协程/普通函数/返回 awaitable 的普通函数统一 `await`。

### 8.5 `_argument_error` — 参数预校验

```python
try:
    inspect.signature(self.func).bind(**kwargs)
except (TypeError, ValueError) as error:
    return ToolResult.error(f"ERROR: invalid arguments for '{self.name}': {error}")
return None
```

用 `signature.bind()` 模拟一次参数绑定：**缺必填参数、传了不存在的参数名、位置/关键字混用错误**都会在这里暴露——在真正执行前就拦截，错误信息更准确（说明是「无效参数」而非函数内部报错）。

### 8.6 `_normalize_result` — 结果归一化

```python
@staticmethod
def _normalize_result(value: Any) -> ToolResult:
    if isinstance(value, ToolResult):
        return value                    # 工具自己返回 ToolResult → 直通
    return ToolResult.ok(_format_result(value))
```

允许工具函数直接返回 `ToolResult`（这样可以构造带 `is_error=True` 的自定义失败），否则自动格式化。

---

## 9. tool() 工厂 / 装饰器

### 9.1 两种形态（`@overload` 声明）

```python
@overload
def tool(func: ToolCallable, *, name=None, description=None) -> Tool: ...

@overload
def tool(func: None = None, *, name=None, description=None) -> Callable[[ToolCallable], Tool]: ...
```

用法一：直接转换
```python
read_tool = tool(read_file)
```

用法二：配置化装饰
```python
@tool(name="read", description="读取文件内容")
def read_file(path: str) -> str: ...
```

### 9.2 装饰器内部流程

```python
def decorator(target):
    signature = inspect.signature(target)
    type_hints = _resolved_type_hints(target)
    doc_description, parameter_descriptions = _parse_docstring(target)

    for parameter_name, parameter in signature.parameters.items():
        # ① 拒绝位置参数
        if parameter.kind in (POSITIONAL_ONLY, VAR_POSITIONAL):
            raise TypeError("model tool calls only support keyword arguments")
        # ② **kwargs → additionalProperties
        if parameter.kind is VAR_KEYWORD:
            additional_properties = _type_to_schema(annotation) or True
            continue
        # ③ 类型翻译 + docstring 描述注入
        schema = _type_to_schema(annotation)
        schema["description"] = ...（来自 docstring）
        # ④ 必填/默认值
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
        elif _is_json_value(parameter.default):
            schema["default"] = parameter.default
```

设计决策：

- **拒绝 `POSITIONAL_ONLY` 和 `*args`**：模型工具调用只发关键字参数（JSON 对象），位置参数语义无法传达。`**kwargs` 却可以接受（映射成 `additionalProperties`）——模型传任意额外键都能接住
- `additionalProperties` 的默认值是 `False`（严格），有 `**kwargs` 才放宽
- 工具描述优先级：显式 `description=` 参数 > docstring > 兜底 `"Call {name}"`
- 注意：`required` 只在「无默认值」时加入——有默认值的参数模型可以省略

### 9.3 生成的 parameters 结构

```json
{
  "type": "object",
  "properties": { ... },
  "required": ["...必填参数..."],
  "additionalProperties": false
}
```

---

## 10. ToolRegister — 注册与分发

### 10.1 基础 API

| 方法 | 行为 |
|---|---|
| `register(candidate)` | 接受 `Tool` 或普通函数（自动 `tool()` 转换）；**重名抛 `ValueError`**；返回注册的 Tool |
| `unregister(name)` | `dict.pop`，不存在抛 `KeyError` |
| `get(name)` | 查找，返回 `Tool \| None` |
| `__contains__` | `in` 运算符支持 |
| `__len__` | 工具数量 |
| `openai_schema()` / `anthropic_schema()` | 全量导出，**保持注册顺序**（`dict` 有序） |

### 10.2 历史兼容点

```python
def authropic_schema(self):     # 注意拼写：authropic
    return self.anthropic_schema()
```

这是历史遗留的**错拼方法名**，保留它只是为了不破坏老调用方（一旦删除，依赖 `authropic_schema` 的旧代码直接 AttributeError）。文件末尾还有：

```python
ToolRegistry = ToolRegister
```

两个名字指向同一个类——新代码用 `ToolRegistry`（惯例名），老代码用 `ToolRegister` 也不坏。**这类「错拼保留 + 别名」是库代码里常见的兼容性策略：兼容成本极低（一行转发），破坏成本极高（用户代码崩溃）。**

### 10.3 分发 — `run()` / `arun()`

```python
def run(self, name, arguments=None):
    registered = self.tools.get(name)
    if registered is None:
        return ToolResult.error(f"ERROR: Unknown tool '{name}'")
    kwargs, error = self._parse_arguments(arguments)
    if error is not None:
        return error
    return registered.run(**kwargs)
```

`arun()` 是同样的流程 + `await registered.arun(**kwargs)`。

**参数格式**：provider 传来的参数可能是
- `None` → 无参数调用
- JSON 字符串 `'{"path": "/tmp/a.txt"}'` → `json.loads` 解码
- Mapping（dict）→ 直接使用

### 10.4 `_parse_arguments` 的完整错误清单

| 情况 | 返回 |
|---|---|
| `None` / 空字符串 | `({}, None)` 正常调用 |
| 非法 JSON | `ERROR: invalid JSON arguments: <msg> at line <L> column <C>` |
| JSON 解码出数组/数字 | `ERROR: arguments must decode to a JSON object` |
| 键不是字符串 | `ERROR: argument names must be strings` |
| 类型不对（非 str/Mapping/None） | `ERROR: arguments must be a JSON string, object, or None` |

错误信息带着行列号（`error.lineno` / `error.colno`），方便排查 provider 生成了什么畸形 JSON。

---

## 11. 完整数据流

以「注册一个文件读取工具，模型调用它」为例走一遍：

```
① 注册
   @tool
   def read_file(path: str, limit: int = 100) -> str: ...
   registry.register(read_file)
      │ tool() 解析签名 → 生成 Tool{name="read_file",
      │   parameters={"type":"object","properties":{path,limit},
      │                "required":["path"]}}
      ▼
② 发给模型
   registry.openai_schema()
      ▼  [{ "type":"function", "function":{ "name":"read_file",
            "parameters":{...} }}]
   → POST /v1/chat/completions (OpenAI)

③ 模型返回工具调用
   { "tool_calls": [{ "function": { "name": "read_file",
       "arguments": "{\"path\":\"/tmp/a.txt\",\"limit\":50}" } }] }

④ 宿主分发
   registry.run("read_file", '{"path":"/tmp/a.txt","limit":50}')
      │ _parse_arguments → {"path": "/tmp/a.txt", "limit": 50}
      │ registered.run(**kwargs)
      │   ├─ _argument_error: signature.bind 校验通过
      │   ├─ func(**kwargs) → "文件内容..."
      │   └─ _normalize_result → ToolResult.ok("文件内容...")
      ▼
⑤ 返回给模型继续推理
   "tool_results": [{ "tool_call_id": "...", "content": "文件内容..." }]
```

**模型写错参数时**：
```
registry.run("read_file", '{"pah": "/tmp/a.txt"}')
  → _argument_error 捕获 bind 失败
  → ToolResult.error("ERROR: invalid arguments for 'read_file': ...")
  → 模型看到错误 → 自我修正 → 重试
```

---

## 12. 设计要点总结

1. **错误即消息**：全部失败路径归一化为 `ToolResult(is_error=True)`，agent 循环零 try/except。这是本模块最重要的模式。
2. **同步/异步双通道**：`run()` 无循环时 `asyncio.run` 兜底、有循环时报错引导 `arun()`——把「嵌套事件循环」这个 Python 异步的经典陷阱挡在模块边界。
3. **单一 schema 源**：内部一套 JSON Schema，`to_openai_schema` / `to_anthropic_schema` 双格式导出，新增 provider 只需加方法。
4. **深拷贝三连**：查表、导出都 deepcopy，内部状态不可被外部篡改，schema 共享安全。
5. **fail fast**：工具名非法、参数形态非法（位置参数）在**注册期**就抛异常；运行期的错误才走 ToolResult。
6. **宁松勿严的兜底**：无法翻译的类型返回 `{}`（允许任意值）而不是报错——工具可用性优先于类型严谨性。
7. **兼容性策略**：`authropic_schema` 错拼保留 + `ToolRegistry` 别名，一行转发换取老代码不破坏。
8. **防御性解析**：`_parse_arguments` 对 provider 传来的任何畸形输入（非法 JSON、非对象、非字符串键）都有明确的错误路径。

---

## 13. 注意事项与坑

### 13.1 PEP 758 语法（Python 3.14+）

```python
except NameError, TypeError:    # 3.14+ 合法，等价于 (NameError, TypeError)
```

这是 PEP 758（允许 except 不带括号）的语法。**项目 `.python-version` 是 3.14**，所以合法。但如果这份代码被移植到 3.13 及以下，会直接 `SyntaxError`——移植时需改回 `except (NameError, TypeError):`。

### 13.2 `_parse_docstring` 的 NameError 陷阱（粘贴版有、源码已修正）

早期版本该函数首行曾出现过 `inToolResult:_arguments = False` 的笔误（应为 `in_arguments = False`）。这个笔误**能通过编译**（函数内带注解的赋值不求值注解，只是多定义了一个无关变量 `inToolResult`），但 `in_arguments` 从未初始化 → 当 docstring 首行不是 `Args:` 段时，循环第一轮 `if in_arguments:` 直接 `NameError`。教训：**带注解的赋值语句是个隐蔽的笔误温床**，好在源码 `base.py:353` 已是正确的 `in_arguments = False`。

### 13.3 几个容易忽略的行为细节

- `Tool.run()` 检测到运行中的事件循环后，对协程返回值调用 `value.close()`——防止产生 "coroutine was never awaited" 的 RuntimeWarning 污染日志。
- `_normalize_result` 允许工具直接返回 `ToolResult`（直通不包装），这是自定义失败信息的逃生门。
- `_is_json_value` 会做一次真实的 `json.dumps`——每次注册工具时对每个默认值都执行一次序列化测试，注册大量工具时这部分成本存在（可忽略，但值得知道）。
- 注册顺序即导出顺序（`dict` 保序），provider 看到工具的先后顺序会影响模型的工具选择倾向，顺序有语义。
- 工具函数返回值如果是 `str`，原样返回不带引号；如果是结构，紧凑 JSON（无缩进）——模型收到的就是紧凑文本。

### 13.4 扩展点

想加深理解，可以尝试：

1. 新增 provider（如 Google Gemini 的 `functionDeclarations` 格式）→ 在 `Tool` 上加 `to_gemini_schema()`
2. 给 `tool()` 加 `concurrency` 参数（控制并发调用上限）→ 思考它应该存在 `Tool` 还是 `ToolRegistry`
3. 用 `repr` 或 `str` 工具测试 `_type_to_schema` 对各种类型注解的输出
4. 思考为什么 `Tool` 没有 `frozen=True` 而 `ToolResult` 有
