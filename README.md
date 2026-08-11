# ResearchEvolve

> A research harness for AI-driven mathematical discovery.
>
> 面向机器可评测数学研究的演化搜索基础设施：把 **ResearchSpec、Hidden Evaluator、Candidate DB、MAP-Elites / Islands、四层 Mutation、Research Graph** 串成一个可复现的研究闭环。

## 项目定位

ResearchEvolve 不是“让一个 LLM 一次性回答数学题”的 Prompt 工程，而是一个研究执行框架：

```text
ResearchSpec
    ↓
Seed Candidates
    ↓
Four-level Mutation
    ↓
Hidden Evaluator
    ↓
Candidate DB
    ↓
MAP-Elites / Islands
    ↓
Research Graph
    └──────────────→ next generation
```

v0.1 优先解决 **machine-evaluable mathematical discovery**：只要一个问题能把候选方案表示成结构化对象，并能通过程序可靠判断合法性和质量，就可以接入 ResearchEvolve。

适合的第一类任务包括：

- 组合构造与离散数学搜索
- 图、矩阵、编码理论与 qLDPC 参数搜索
- 几何构造与 packing
- 算法参数 / 程序构造搜索
- 可以数值验证的符号表达式或猜想探索

证明搜索、文献智能体、Conjecture ↔ Proof ↔ Counterexample 闭环暂不属于 v0.1 的核心范围。

---

## v0.1 六个核心模块

### 1. ResearchSpec

`research_evolve/spec.py`

每次研究都由结构化 `ResearchSpec` 描述：

- 问题陈述
- domain
- research mode
- objectives
- constraints
- MAP-Elites behavior dimensions
- generations / population / timeout / random seed

这使同一个 Research Engine 可以切换不同数学领域，而不用把领域知识硬编码到 orchestrator 中。

### 2. Hidden Evaluator

`research_evolve/evaluation.py`

Evaluator 运行在独立 Python 子进程中，只通过 JSON 协议接收 candidate、返回评测结果：

```json
{
  "valid": true,
  "score": -0.25,
  "metrics": {
    "distance": 0.25
  },
  "behavior": {
    "representation": "graph"
  },
  "diagnostics": {}
}
```

约定：

- `valid`：候选是否满足硬约束
- `score`：统一为 **越大越好** 的 canonical score
- `metrics`：保留领域原始指标
- `behavior`：MAP-Elites 的行为特征
- `diagnostics`：错误、约束、调试信息

> **安全说明**：v0.1 实现的是“进程边界 + 隐藏评测接口契约”，不是强安全沙箱。真实的 LLM Agent 场景中，应把 evaluator 放到 Agent 无法读取的独立 Docker / VM / grader 环境，只暴露评测协议。

### 3. Candidate DB

`research_evolve/candidates.py`

使用 SQLite 持久化：

- candidate payload
- parent lineage
- mutation level
- generation
- valid / score
- metrics
- behavior
- diagnostics

默认工作区会生成：

```text
.researchevolve/run/
├── candidates.sqlite3
├── research_graph.sqlite3
└── summary.json
```

### 4. MAP-Elites + Islands

`research_evolve/evolution.py`

ResearchEvolve 不只保留全局 Top-K。

每个 behavior cell 维护一个 elite，并由多个独立 island 同时探索；每 5 代进行一次轻量 elite migration。

这样可以避免搜索快速塌缩到“同一种思路的十个微小变体”。

### 5. 四层 Mutation

`research_evolve/mutation.py`

统一抽象为：

```text
Level 1  Local Mutation
Level 2  Structural Mutation
Level 3  Algebraic Mutation
Level 4  Representation Mutation
```

内置 `FourLevelMutator` 只是通用 fallback，用于验证整个框架。

真正的数学研究建议针对 domain 自定义 mutation，例如 qLDPC 可以分别映射为：

- Local：修改少量生成元 / 位移 / 参数
- Structural：改变 block、lift、连接模式或 shape
- Algebraic：修改群代数、多项式或 lifted-product 结构
- Representation：在 matrix / graph / polynomial / group-algebra 等表示之间切换

CLI 已支持通过 `module:Class` 加载自定义 mutator。

### 6. Research Graph

`research_evolve/graph.py`

Research Graph 不等于普通 RAG。

它记录研究过程中的实体和因果 / lineage 关系：

```text
Problem
  └─ investigates → Candidate A
                         ├─ evaluated_as → Evaluation A
                         └─ mutated_to   → Candidate B
                                              └─ evaluated_as → Evaluation B
```

当前节点包括 problem、candidate、evaluation；后续版本会扩展 hypothesis、experiment、conjecture、lemma、counterexample、proof 等对象。

---

# 快速开始

## 1. 安装

```bash
git clone https://github.com/tfyf103/ResearchEvolve.git
cd ResearchEvolve

python -m venv .venv
```

Linux / macOS：

```bash
source .venv/bin/activate
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

安装：

```bash
pip install -e ".[dev]"
```

---

## 2. 跑第一个完整 Demo

仓库自带 `target42`，目标是搜索 `x ≈ 42`。这个问题本身故意非常简单，用来检查 ResearchEvolve 的整个闭环是否正常。

```bash
research-evolve run \
  --spec examples/target42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/target42/seeds.json \
  --workspace .researchevolve/target42 \
  --islands 4
```

运行完成后会输出类似：

```json
{
  "research_name": "target-42-demo",
  "evaluated": 194,
  "valid": 194,
  "archive_size": 8,
  "best_candidate_id": "...",
  "best_score": 0.0,
  "best_payload": {
    "x": 42,
    "representation": "..."
  },
  "workspace": ".researchevolve/target42"
}
```

具体结果会随 mutation 路径变化。

---

## 3. 查看最佳候选

```bash
research-evolve inspect \
  --workspace .researchevolve/target42 \
  --limit 10
```

---

## 4. 导出 Research Graph

```bash
research-evolve graph \
  --workspace .researchevolve/target42 \
  --output research-graph.json
```

之后可以使用 NetworkX、Neo4j、Gephi 或自定义 Web UI 可视化。

---

# 创建自己的研究任务

## Step 1：生成 ResearchSpec

```bash
research-evolve init research.json
```

一个最小 ResearchSpec：

```json
{
  "name": "my-search",
  "problem": "Find a better mathematical construction.",
  "domain": "generic",
  "mode": "metric_search",
  "objectives": [
    {"name": "quality", "direction": "maximize", "weight": 1.0}
  ],
  "constraints": [],
  "behavior_dimensions": ["representation"],
  "budget": {
    "generations": 20,
    "population_size": 32,
    "evaluator_timeout_seconds": 30,
    "seed": 0
  },
  "metadata": {}
}
```

`direction` 和 `weight` 目前主要作为研究语义保留；v0.1 的搜索核心直接使用 evaluator 返回的 canonical `score`。

---

## Step 2：定义 seeds

`seeds.json`：

```json
[
  {
    "parameter_a": 4,
    "parameter_b": 7,
    "representation": "direct"
  }
]
```

Candidate payload 的结构由你的领域决定。

---

## Step 3：实现 evaluator

Evaluator 从 stdin 读取一个 JSON candidate，并向 stdout 输出一个 JSON result。

```python
import json
import sys

candidate = json.load(sys.stdin)

# 1. hard constraint
valid = candidate["parameter_a"] > 0

if not valid:
    print(json.dumps({
        "valid": False,
        "score": None,
        "diagnostics": {"reason": "parameter_a must be positive"}
    }))
    raise SystemExit(0)

# 2. domain metrics
quality = candidate["parameter_a"] * candidate["parameter_b"]

# 3. canonical score: larger is always better
score = float(quality)

print(json.dumps({
    "valid": True,
    "score": score,
    "metrics": {"quality": quality},
    "behavior": {
        "representation": candidate.get("representation", "direct")
    },
    "diagnostics": {}
}))
```

---

## Step 4：按需实现领域 Mutation

```python
from research_evolve.mutation import FourLevelMutator


class MyDomainMutator(FourLevelMutator):
    def local(self, payload, rng):
        payload["parameter_a"] += rng.choice([-1, 1])
        return payload

    def structural(self, payload, rng):
        # 改变结构级参数
        return payload

    def algebraic(self, payload, rng):
        # 使用领域代数变换
        return payload

    def representation(self, payload, rng):
        # 改变问题表示
        return payload
```

运行：

```bash
research-evolve run \
  --spec research.json \
  --evaluator evaluator.py \
  --seeds seeds.json \
  --mutator my_domain.mutations:MyDomainMutator
```

---

# qLDPC 接入建议

ResearchEvolve 很适合把 qLDPC 作为第一个真实 domain pack。

推荐 candidate：

```json
{
  "family": "lifted_product",
  "representation": "group_algebra",
  "lift": 31,
  "generators_a": [1, 4, 9],
  "generators_b": [2, 7, 11]
}
```

推荐 evaluator cascade：

```text
Candidate
   ↓
Syntax / parameter validation
   ↓
construct Hx / Hz
   ↓
CSS commutation check
   ↓
GF(2) rank → n, k
   ↓
cheap distance estimate
   ↓
BP-OSD
   ↓
MILP exact distance for elites
```

最终 evaluator 可以统一返回：

```json
{
  "valid": true,
  "score": 0.82,
  "metrics": {
    "n": 360,
    "k": 24,
    "distance_estimate": 18,
    "rate": 0.0667
  },
  "behavior": {
    "family": "lifted_product",
    "representation": "group_algebra",
    "symmetry": "cyclic"
  },
  "diagnostics": {
    "css_commutes": true
  }
}
```

对 qLDPC 来说，建议 behavior dimensions 不只放 score，而是放 `family / representation / symmetry / complexity bucket` 等特征，让 MAP-Elites 主动保存不同数学思想。

---

# Python API

CLI 只是最薄的一层入口，也可以直接嵌入 Agent / MCP / ChatGPT Skill：

```python
from research_evolve.engine import ResearchEngine
from research_evolve.spec import ResearchSpec

spec = ResearchSpec.from_dict(...)

with ResearchEngine(spec, workspace=".researchevolve/my-run") as engine:
    summary = engine.run(
        seed_payloads=[{"x": 0}],
        evaluator_path="evaluator.py",
    )

print(summary.to_dict())
```

这也是后续接 LLM Explorer、Research Director、MCP Server 和 Web UI 的主要入口。

---

# 测试

```bash
pytest -q
```

当前测试覆盖：

- ResearchSpec validation
- Candidate DB round-trip
- MAP-Elites diversity
- 四层 mutation 基本契约
- Research Graph edges
- Engine end-to-end integration

---

# v0.1 边界

当前版本已经具备一个可运行的 evolutionary research harness，但还没有试图一次性完成所有“自主数学家”能力。

**已经实现：**

```text
ResearchSpec
Hidden evaluator protocol
Candidate DB
MAP-Elites archive
Island populations + migration
Four-level mutation abstraction
Research Graph
CLI
Runnable demo
Tests
```

**后续路线：**

```text
v0.2  Evaluator cascade + Pareto / novelty + checkpoints
v0.3  LLM Explorer / mutation proposal / experiment agents
v0.4  Conjecture + counterexample loop
v0.5  Proof planner / prover / verifier
v0.6  Lean / symbolic formal verification
v1.0  Autonomous Mathematical Research Lab
```

长期目标不是做一个巨大的数学 Prompt，而是形成：

```text
LLM creativity
      +
Evolutionary search
      +
Automated evaluation
      +
Structured research memory
      +
Independent verification
```

让数学研究从“一次回答”变成一个可以持续搜索、积累、验证和复现的研究过程。
