# RepoPilot Week 1 Plan

## 0. 项目定位

项目名称：**RepoPilot: Context-Efficient Repository-Level Coding Agent**

目标不是实现一个“能调用 shell 的 Coding Agent Demo”，而是研究：

> 在固定模型与有限上下文预算下，能否通过 repository-aware context engineering，使 Coding Agent 更准确地定位相关代码、减少无关上下文与工具调用，并利用测试失败动态更新上下文，从而提升 repository-level software repair 效率。

RepoPilot 与已有 Travel Planner 项目应形成明显区分：

```text
Travel Planner
→ Agent reliability
→ evaluator / verifier
→ structured repair
→ SFT / post-training

RepoPilot
→ Coding Agent
→ repository understanding
→ context engineering
→ tool use
→ autonomous debugging
→ SWE-bench evaluation
```

---

## 1. Week 1 总目标

第一周不追求复杂系统。

核心目标：

> 从零实现一个不依赖 LangGraph 等 Agent 框架的 Minimal Repository Coding Agent，并逐步加入 repository retrieval、symbol-aware context 和 failure-driven context update，最终形成第一组 controlled experiment。

第一周结束时至少应该具备：

```text
Issue
  ↓
Repository understanding
  ↓
Context retrieval
  ↓
Code inspection
  ↓
Patch generation
  ↓
Run tests
  ↓
Failure analysis
  ↓
Context update
  ↓
Retry
  ↓
Final diff / result
```

并可以比较：

```text
B0 — Naive Agent
B1 — Static Retrieval Agent
B2 — Dynamic Context Agent
```

---

## 2. 第一周不做什么

严格控制 scope。本周暂不实现：Multi-Agent、Planner/Coder/Reviewer 多智能体、MCP、长期 Memory DB、向量数据库、复杂 embedding RAG、SFT、DPO、GRPO、RL、GUI、VS Code Plugin、正式大规模 SWE-bench。

优先：**可运行 > 可测试 > 可记录 > 可比较 > 可扩展。**

---

## 3. 技术栈

```text
Python 3.11+
Pydantic
OpenAI-compatible API
subprocess
ripgrep
Git
pytest
tree-sitter / Python AST
JSONL
Docker（SWE-bench 阶段再重点使用）
```

Agent Loop 自己实现，不使用 LangGraph 作为核心 Agent runtime。

---

## 4. 模型 Backend 抽象

从 Day 1 就避免绑定单一 API。

```python
class ModelBackend:
    def chat(self, messages, tools=None):
        ...
```

至少预留：ModelScopeBackend、DeepSeekBackend、OpenAICompatibleBackend、LocalVLLMBackend。第一周实际只需要实现当前能用的 backend。

目标：后续从魔搭免费 API 切换 DeepSeek API / Local vLLM 时，不修改 Agent 主逻辑。

---

## 5. RuntimeContext

启动时自动检测：OS、shell、cwd、path style、Python version、available commands、Git availability、ripgrep availability，并显式注入 system prompt。不能让模型自己猜 shell。

---

# Day 1 — Minimal Agent Loop

目标：完成最基础 `LLM → tool call → execute → observation → LLM`。

建议模块：

```text
repopilot/
├── agent/
│   ├── loop.py
│   ├── state.py
│   └── prompts.py
├── models/
│   ├── base.py
│   └── modelscope.py
├── runtime/
│   └── environment.py
└── tools/
```

最低 AgentState：issue、messages、step_count、files_seen、files_modified、test_history、token_usage。

Day 1 tools：`list_files`、`search_code`、`read_file`、`git_diff`。不要先开放任意 shell。

准备一个 10–20 文件以内、带 pytest、人工注入 1–2 个 bug 的 toy repo。

Done Definition：Agent loop 可连续运行；Tool call 正常；模型知道真实 RuntimeContext；能搜索/读取仓库；Trajectory 可以保存。

---

# Day 2 — Patch / Test / Retry

目标：形成完整闭环 `Issue → Inspect → Patch → Test → Failure → Retry`。

新增 tools：`apply_patch`、`run_tests`。尽量使用 unified diff / structured patch，不默认 whole-file rewrite。

Patch safety：目标文件存在、patch 能 apply、修改限制在 workspace 内、diff 可查看。

TestResult 至少包含：success、exit_code、stdout、stderr、failed_tests。

开始记录：steps、LLM calls、input/output tokens、tool calls、files read/modified、changed LOC、test retries、latency。

Done Definition：至少完成 3 个 toy bug cases，能定位 bug、修改、pytest、失败后自动重试、最终通过。

---

# Day 3 — Naive Baseline + Static Repository Retrieval

冻结 B0：允许 repo tree、search/read、patch、test，但没有主动 repository retrieval、context ranking、symbol graph、failure-driven update。

B1 新增 issue-conditioned lexical retrieval：identifier extraction、keyword match、filename match、ripgrep frequency、test file matching。

记录 retrieved files/symbols/snippets、retrieval score、token cost，并计算最终 modified file 是否出现在 initial retrieval top-k，形成 retrieval hit rate。

Done Definition：B0/B1 都可运行，并使用 same model / issue / step budget / temperature。

---

# Day 4 — Symbol-Aware Repository Understanding

加入 symbol-aware retrieval。Python repo 第一版优先用 Python AST，后续再扩展 tree-sitter。

提取：class、function、method、import、call/reference、file、test。

构建轻量 Repository Graph：File contains Class/Method，Function calls Function，File imports File。无需图数据库，可用 dict/set/NetworkX。

第一版 context score：

```text
score = α * lexical + β * symbol + γ * graph
```

权重手工设置，不做参数学习。

Done Definition：给定 issue，可以输出 Top-k files、Top-k symbols、Why selected、Token cost，并明显优于单纯关键词搜索。

---

# Day 5 — Token-Budget Context Manager

核心问题：在固定 token budget 下选择信息价值最高的代码上下文。

第一版至少支持 8K / 12K / 16K / 24K。

ContextItem：source、file、symbol、content、relevance_score、token_cost。

Packing 第一版用 greedy，按 `score/token_cost` 排序，直到总 token cost 达到预算上限。

Context 建议分层：Task、Repository Map、Relevant Symbols、Relevant Code、Current Diff、Recent Test Failure。

记录 context budget、actual context tokens、selected files/symbols、dropped candidates。

Done Definition：相同 issue 下可比较 Full/Naive Context vs Budget-aware Context，并清楚显示 context token 差异。

---

# Day 6 — Failure-Driven Context Update

流程：

```text
Initial issue
↓
Initial retrieval
↓
Patch
↓
pytest failure
↓
Extract failure evidence
↓
Update retrieval ranking
↓
Inject new context
↓
Retry
```

Failure evidence：failed test names、stack trace files、line numbers、symbols、exception type、assertion message。

Dynamic ranking 可先用：

```text
new_score = original_score
          + λ1 * stack_trace_hit
          + λ2 * failed_test_reference
          + λ3 * recently_modified_dependency
```

不无限累积 context，允许新增高价值上下文、删除低价值上下文，保持固定 token budget。

维护结构化 Agent Memory：goal、confirmed_facts、suspected_files、modified_files、failed_hypotheses、test_failures、next_actions。

Done Definition：至少找到一个初始 retrieval 不足的 case，展示第一次 patch 失败 → failure evidence → context ranking 更新 → 拉入新文件 → 第二次成功，并保存完整 trajectory。

---

# Day 7 — Controlled Experiment V1

本日不再加功能，做第一轮实验。

方法：

- B0 — Naive Agent：Minimal tools + linear history + model-driven search
- B1 — Static Context：lexical + symbol-aware retrieval + token-budget context
- B2 — Dynamic Context：B1 + failure-driven context update + structured context state

第一轮 5–10 cases，可用 toy repo + 少量真实 repo issue；暂不要求正式 SWE-bench。

必须统一 same model、temperature、task、max steps、max output、execution environment。

核心指标：

| Metric | B0 | B1 | B2 |
|---|---:|---:|---:|
| Task Success | | | |
| Input Tokens | | | |
| Output Tokens | | | |
| LLM Calls | | | |
| Tool Calls | | | |
| Files Read | | | |
| Files Modified | | | |
| Changed LOC | | | |
| Test Retries | | | |
| Latency | | | |

另外记录 initial retrieval hit rate、peak context tokens、context compression ratio。

---

## 6. Trajectory Logging

从 Day 1 统一使用 JSONL。每个 step 至少记录 task_id、step、model、action、input/output tokens、files_in_context、tool latency、LLM latency。任务结束记录 success、total_steps、total tokens、files read/modified、changed LOC、test retries。

---

## 7. mini-SWE-agent 的位置

第一周只阅读、理解、记录设计，不把它硬接进 RepoPilot。后续把它作为 external baseline，而不是 RepoPilot 的代码基础。

最终实验结构：

```text
B0 Naive Agent
B1 Static RepoPilot
B2 Dynamic RepoPilot
External: mini-SWE-agent
```

尽量统一 same model、same SWE-bench cases、same budget。

---

## 8. Week 1 建议仓库结构

```text
RepoPilot/
├── repopilot/
│   ├── agent/
│   │   ├── loop.py
│   │   ├── state.py
│   │   └── prompts.py
│   ├── models/
│   │   ├── base.py
│   │   ├── modelscope.py
│   │   └── openai_compatible.py
│   ├── tools/
│   │   ├── files.py
│   │   ├── search.py
│   │   ├── patch.py
│   │   ├── tests.py
│   │   └── git.py
│   ├── context/
│   │   ├── lexical.py
│   │   ├── symbols.py
│   │   ├── graph.py
│   │   ├── ranker.py
│   │   ├── budget.py
│   │   └── dynamic.py
│   ├── runtime/
│   │   ├── environment.py
│   │   └── executor.py
│   └── evaluation/
│       ├── metrics.py
│       └── trajectory.py
├── experiments/
│   ├── naive.yaml
│   ├── static_context.yaml
│   └── dynamic_context.yaml
├── tests/
├── toy_repos/
├── reports/
├── README.md
└── WEEK1_PLAN.md
```

不要为了严格遵守结构阻碍开发，可以按实际情况调整。

---

## 9. Week 1 最终交付物

### Code
Minimal Agent Loop、ModelBackend、RuntimeContext、list/search/read、patch、test、git diff、lexical retrieval、symbol retrieval、repo graph、token-budget context manager、failure-driven update。

### Demo
至少一个完整 trajectory：Issue → retrieval → patch → failure → context update → retry → PASS。

### Experiment
至少 5–10 cases × B0/B1/B2，得到第一张结果表。

### Documentation
README v0、architecture diagram draft、Week 1 experiment report。

---

## 10. Week 1 成功标准

Engineering：Agent Loop 稳定、Tools 稳定、Patch/Test/Retry 可用、模型 backend 可切换、Runtime aware。

Algorithm：Static Context Engine 可运行、Dynamic Context Update 可运行、固定 token budget。

Evaluation：B0/B1/B2 可公平比较、trajectory 可记录、metrics 可自动汇总。

只要这些完成，RepoPilot 就从 Coding Agent Demo 进入可做实验的 Agent Research Prototype 阶段。

---

## 11. 第二周入口

```text
SWE-bench integration
↓
5 cases
↓
10–20 cases
↓
mini-SWE-agent external baseline
↓
RepoPilot ablation
↓
failure analysis
```

不要第一周提前扩张 scope。

---

## 12. 核心研究假设

> Compared with a naive repository-level coding agent, structured repository retrieval and failure-driven dynamic context management can improve software-repair effectiveness and/or reduce inference cost under a fixed model and context budget.

不要因为后面看到新的 Coding Agent 技术就频繁改变主问题。

---

## 13. 第一周优先级

```text
P0:
Agent Loop
Tools
Patch/Test/Retry
Trajectory

P1:
Static Retrieval
Symbol Graph
Context Budget

P2:
Failure-driven Context Update

P3:
漂亮架构
复杂 Prompt
额外 Tools
```

P0/P1 没完成之前，不做 P3。

---

## 14. Week 1 一句话目标

> **7 天内，将 RepoPilot 从零推进到一个可在固定模型和上下文预算下，对 repository-level bug repair 进行 B0/B1/B2 controlled experiment 的完整研究原型。**
