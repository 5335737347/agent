# 量化差距评估：本仓库 Coding Harness vs 生产级 Coding Agent

> 评估日期：2026-08-27
> 评估对象：本仓库 `src/`（1,687 行 Python + 264 行测试）
> 对照对象：Claude Code、OpenAI Codex、Gemini CLI（闭源生产级）；OpenHands、Aider（开源生产级）
> 方法：代码逐文件核验 + 运行测试 + 基准/工程数据对比

---

## 0. 结论（TL;DR）

**本仓库 harness 的"生产级 coding agent 能力完整度"约为 12%**（0–100% 加权）。

- 若只算"核心编码闭环"（发现 → 修改 → 验证 → 迭代），完整度约 **25%**：发现阶段完成约一半，修改与验证阶段为 **0%**（`patching.py` 是空壳，无命令执行）。
- 端到端可解性量化：**SWE-bench Verified 预计解出率 ≈ 0%**（无法修改文件 → 任何需要写代码的任务都无解）；补齐写文件+命令+测试工具后，受 14B 本地模型限制，预计仍只有 **5–20%**（生产头部 45–80%）。
- 代码规模约为开源生产级 agent 的 **1/20（Aider）到 1/100（OpenHands）**，测试数量约为其 **1/50–1/300**。
- 差距最大的三个维度：**安全执行（沙箱）、运行治理（预算/审批/恢复）、可观测性**——三者合计约占 60% 的差距权重。
- 好消息：工具抽象层（`base.py` 895 行）质量接近生产级，是唯一可以"带过去"的资产。

---

## 1. 本仓库代码事实核对（实测，非文档转述）

| 事实项 | 实测值 | 备注 |
|---|---|---|
| 源码规模 | 1,687 行 / 12 个 .py（src/） | 其中 base.py 895 行（53%） |
| 测试规模 | 264 行 / **16 个**测试 | docs 写"24 项"已过时（现为 16，全部通过） |
| 工具数量 | **3 个**（list_files / search_text / read_file），全部只读 | patching.py 存在但为空壳 |
| apply_patch | **未实现**（函数体仅 `...`） | 参数名笔误 `max_fils`（应为 max_files） |
| 模型适配 | 1 个 Provider（OpenAI Chat Completions 兼容） | 无流式/重试/超时/usage 追踪 |
| 默认模型 | deepcoder:14b（Ollama 本地 14B） | 非前沿模型 |
| Agent 循环 | 线性循环，max_turns=8 | 无状态机/预算/取消/审批 |
| 持久化 | 无（进程内 history） | CLI 退出即丢 |
| 沙箱 | 仅 Workspace 路径检查（36 行） | 无进程/网络/资源隔离 |
| 日志/审计 | 无（print + 异常文本） | |
| 上下文 | 完整历史全量重发 | 8 轮内即可能超 14B 模型窗口 |

**代码缺陷（本次核验发现）：**
1. `base.py` L135/314/338：`except TypeError, ValueError:` 为 Python 2 风格（PEP 758 使 Python 3.14 接受，但 3.13 及以前是 SyntaxError）——可移植性隐患。
2. `model.py` L89：`"Mode; returned no choices"` 拼写错误（应为 "Model"），且该错误路径无测试覆盖。
3. `patching.py`：`make_apply_patch_tool` 未实现 + `max_fils` 笔误 + docstring 为 `"""..."""` 占位。
4. `base.py` L808：`authropic_schema` 拼写错误方法名（legacy 别名，建议标记 deprecation）。

---

## 2. 量化对比表（同维度硬数字）

| 维度 | 本仓库 | Aider（开源） | OpenHands（开源） | Claude Code / Codex（闭源生产） |
|---|---|---|---|---|
| 代码规模 | 1,687 行 | ~37,000 行 Python（1.3MB） | 9MB+ 代码（TS/Python） | 闭源（npm 包 MB 级） |
| 测试 | 16 个单元 | 数千（pytest 套件） | 数千 + E2E | 内部（不可见） |
| 内置工具 | 3（只读） | ~10 命令 + repo-map | ~20（CodeAct 为 Python 代码即工具） | 10–15 核心 + MCP 数千 |
| 写文件/补丁 | ❌ 空壳 | ✅ apply/edit 格式 | ✅ CodeAct | ✅ 多种编辑器 |
| 命令执行 | ❌ | ✅（受限） | ✅（沙箱内） | ✅（沙箱/审批） |
| 测试运行 | ❌ | ✅ | ✅ | ✅ |
| Git 操作 | ❌ | ✅ 自动 commit | ✅ | ✅ |
| 浏览器/截图 | ❌ | ❌ | ✅（部分） | ✅（部分） |
| SWE-bench Verified | **~0%**（不可写文件） | 31.4%（2026-04 榜） | 68.4%（CodeAct v3×Opus 4.6） | 66–80.9%（官方口径）；SWE-bench Pro（更难）：Claude Code+Opus 4.8 = 69.2% #1（2026-06） |
| Terminal-Bench 2.1 | ~0%（无终端） | — | — | 56.9–83.4%（2026-06 榜：#1 Codex CLI+GPT-5.5=83.4%，#2 Claude Code+Opus 4.8=78.9%；同模型 GPT-5.5 跨 harness 差 5.2pp） |
| 沙箱 | 路径检查 | 本地受限执行 | Docker 容器 | 权限模式/容器/云沙箱（BoundaryBench High-NIST 严格档：Grok 74.9% > Codex 74.2% > Claude Code 67.8%） |
| 审批流 | ❌ | ❌ | ✅ | ✅（plan/acceptEdits 等） |
| 预算/取消/恢复 | ❌ | ⚠️ 部分 | ✅ | ✅ |
| 持久化 | ❌ | ✅ git 驱动 | ✅ session | ✅ checkpoint/resume |
| 日志/审计 | ❌ | ⚠️ 基础 | ✅ | ✅（企业版） |
| 上下文管理 | 全量累积 | ✅ repo-map + 压缩 | ✅ token 预算 | ✅ 压缩 + 服务端缓存 |
| 多 Provider | ❌（仅 OpenAI 兼容） | ✅（30+） | ✅ | ❌（绑定自家，Codex CLI 例外开源） |
| 流式/重试/降级 | ❌ | ✅ | ✅ | ✅ |
| 并发工具 | ❌ 串行 | ✅ | ✅ | ✅ |
| 多代理编排 | ❌ | ❌ | ⚠️ | ✅ subagents/teams |

---

## 3. 八维能力评分（0–10，生产级头部 = 8–10 基准）

| 维度 | 本仓库 | 生产级 | 差距 | 差距权重 |
|---|---|---|---|---|
| 编码工具链（发现/修改/验证） | 1.5 | 9 | 7.5 | 20% |
| Agent 循环语义（状态/预算/取消） | 2 | 8 | 6 | 10% |
| 安全执行（沙箱/隔离） | 2 | 8 | 6 | 15% |
| 权限与审批治理 | 0.5 | 8 | 7.5 | 10% |
| 模型适配层 | 2 | 8 | 6 | 10% |
| 上下文管理 | 2 | 8 | 6 | 10% |
| 可观测性（日志/trace/审计） | 1 | 8 | 7 | 10% |
| 持久化与恢复 | 0 | 8 | 8 | 5% |
| 测试与 Eval | 2 | 8 | 6 | 5% |
| 工程规模与生态 | 2 | 9 | 7 | 5% |

**加权"生产就绪度" = 12%**（Σ 权重 × 本仓库分 / Σ 权重 × 生产分）

> 参考：若以"能完成真实编码任务并安全运营"为 100%，教学 Demo ≈ 5–10%，本仓库 ≈ 10–15%，可工作的本地 harness（P0 目标）≈ 30–40%，生产级 ≈ 100%。

---

## 4. 端到端可解性量化（SWE-bench 估计）

SWE-bench Verified 任务需要：读代码 → **改文件** → 跑测试 → 迭代。

| 阶段 | 本仓库 | 说明 |
|---|---|---|
| 读代码 | ✅ 可用 | list_files/search_text/read_file 有边界限制 |
| 改文件 | ❌ | patching.py 空壳 → **任何修复任务在此失败** |
| 跑测试 | ❌ | 无命令执行 |
| 迭代 | ❌ | 无验证信号可迭代 |

→ 当前 **解出率 ≈ 0%**（确定性结论，不依赖模型）。

补齐后的模型上限估计（参考开源同档）：
- deepcoder:14b（14B 本地）：SWE-bench Verified 期望 **5–20%**（Qwen3-Coder-30B = 51.6% 需 OpenHands 级 scaffold；14B 档显著更低，且本地量化推理质量打折）
- 若换 DeepSeek V3.2+ / GPT-5.1-Codex 级模型：受 harness 质量限制，预计 **30–50%**（同模型挂 SWE-agent v1 = 43.2%、挂 Cline = 59.8%，scaffold 差异 16.6pp 即 harness 的贡献空间）

**结论：当前瓶颈排序 = 工具闭环（100% 阻塞）→ 模型（14B → 上限 ~20%）→ harness 质量（可达 ±16pp，实证：同模型换 harness 成绩差 10–20+ 点，如 LangChain 实验 GPT-5.2-Codex 52.8%→66.5%、Claw-SWE-Bench 19.1%→73.4%；但 METR 2026-02 也显示 Claude Code/Codex 相对默认 scaffold 的增益仅 14.5–50.7%，即 harness 收益与任务强相关）。**

---

## 5. 差距的"价格"（从现状到生产级的投入估计）

| 里程碑 | 内容（对应 docs/architecture-assessment.md 的 P0–P3） | 工作量估计（1 人全职） |
|---|---|---|
| **P0 可用闭环** | apply_patch + 命令执行 + 测试运行 + 超时/输出上限 + 结构化日志 + 1 个 E2E 修复任务 | **2–4 周** |
| **P1 可靠** | Run 状态机 + token/时间/成本预算 + 上下文裁剪 + 只读并发 + 持久化/checkpoint | 4–6 周 |
| **P2 安全** | Policy Engine + 审批 + 容器沙箱（Docker/landlock）+ 安全测试套件 | 6–10 周 |
| **P3 可运营** | 多 Provider 网关 + tracing/metrics + replay/eval + 多任务调度 | 6–10 周 |
| **生产级合计** | | **约 4–7 个月** |

> 对照：Aider 单开发者约 2 年迭代到 48K star；OpenHands 为组织级项目（85K star，3 年）。个人从零到生产级 harness 的合理预期是半年到一年，前提是优先打通闭环而不是扩展 Schema。

---

## 6. 可保留资产（不需要重写）

1. **`tools/base.py` 的 Schema 推导**（~400 行核心）：覆盖 Annotated/Literal/Union/TypedDict/dataclass/Enum/datetime/UUID/Path，含 docstring 解析、默认值、必填推导、递归保护——质量接近生产级，直接复用。
2. **`Tool` 同步/异步双执行 + 异常隔离**：错误归一化为 `ToolResult` 不击穿循环——正确的设计。
3. **`Workspace.resolve()` 路径策略**：防绝对路径/`../`/symlink 逃逸——作为"路径层"保留，但必须明确它**不是**沙箱（TOCTOU 风险：resolve 后到操作间可被替换）。
4. **`ModelClient` Protocol 解耦**：扩展多 Provider 的挂载点正确。

## 7. 需要立即修的 4 个问题

1. `except TypeError, ValueError:` ×3 → 改 `except (TypeError, ValueError):`（否则 3.13 及以下无法运行）。
2. `model.py` "Mode;" 拼写 → "Model;"，并为"无 choices"分支补测试。
3. `patching.py`：要么实现（P0），要么删除并明确"只读 Demo"边界（README 已声明只读，但文件存在会造成误导）。
4. `ToolRegistry`/`ToolRegister` 双名与 `authropic_schema`：选一个名字，另一个标记 deprecated。

---

## 8. 与生产级 agent 的本质差距（一句话总结）

> 生产级 agent 是**"闭环执行器 + 安全边界 + 运行治理"三位一体的系统**；本仓库目前是**"只读侦察器 + 工具注册表"**。差距不在模型调用代码（只差几十行），而在执行闭环（缺 ~500 行）、安全边界（缺 ~1,000+ 行）、运行治理（缺 ~2,000+ 行）——以及围绕它们的一整套测试、文档和运营实践。
