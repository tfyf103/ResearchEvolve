# ResearchEvolve

> A research harness for AI-driven mathematical discovery.
>
> 面向机器可评测数学研究的演化搜索基础设施：把 **ResearchSpec、Evaluator Cascade、Candidate DB、MAP-Elites / Islands、Pareto / Novelty、四层 Mutation、Checkpoint、Research Graph** 串成一个可持续、可恢复、可复现的研究闭环。

## 项目定位

ResearchEvolve 不是“让一个 LLM 一次性回答数学题”的 Prompt 工程，而是给未来的 LLM Explorer / Research Agent 提供一个可信的科研执行底座。

```text
ResearchSpec
    ↓
Seed Candidates
    ↓
Four-level Mutation
    ↓
Evaluator Cascade
    ↓
Candidate DB
    ↓
┌───────────────┬────────────────┬────────────────┐
│ MAP-Elites    │ Pareto Archive │ Novelty Archive│
│ + Islands     │ multi-objective│ behavior search│
└───────┬───────┴────────┬───────┴────────┬───────┘
        │                │                │
        └────────────────┴────────────────┘
                         ↓
                  Research Graph
                         ↓
               Checkpoint + Manifest
                         ↓
                   next generation
```

v0.2 仍然聚焦 **machine-evaluable mathematical discovery**：只要一个问题能把候选表示成结构化对象，并能通过程序可靠判断合法性、指标和质量，就可以接入 ResearchEvolve。

适合的任务包括：

- 组合构造与离散数学搜索
- 图、矩阵、编码理论与 qLDPC 参数搜索
- 几何构造与 packing
- 算法参数 / 程序构造搜索
- 可以数值验证的符号表达式或猜想探索

证明搜索、文献智能体、Conjecture ↔ Proof ↔ Counterexample 闭环会在后续版本加入。

---

# v0.2 新增能力

## 1. Evaluator Cascade

v0.1 每个 candidate 只有一个 evaluator。v0.2 支持把评测拆成从便宜到昂贵的多个阶段：

```text
Candidate
   ↓
Stage 1: syntax / hard constraints
   ↓
Stage 2: cheap numerical metrics
   ↓
Stage 3: approximate solver
   ↓
Stage 4: expensive exact verifier
```

任何阶段返回 `valid=false`，后续昂贵 evaluator 都不会执行。

CLI 通过重复 `--evaluator` 构造 cascade：

```bash
research-evolve run \
  --spec research.json \
  --evaluator evaluators/constraints.py \
  --evaluator evaluators/cheap_metric.py \
  --evaluator evaluators/exact_metric.py \
  --seeds seeds.json \
  --workspace .researchevolve/my-run
```

中间 evaluator 可以只返回 `valid / metrics / behavior`，最后一级 evaluator 必须为合法候选返回 canonical `score`。

Evaluator 协议：

```json
{
  "valid": true,
  "score": 0.82,
  "metrics": {
    "distance": 12,
    "rate": 0.18
  },
  "behavior": {
    "family": "cyclic",
    "representation": "polynomial"
  },
  "diagnostics": {}
}
```

约定：

- `valid`：是否通过该阶段
- `score`：统一为“越大越好”的 canonical score
- `metrics`：领域原始指标，供 Pareto / 报告使用
- `behavior`：MAP-Elites / Novelty 的行为特征
- `diagnostics`：评测诊断

> 当前仍是进程隔离接口，不是强安全沙箱。真实 LLM Agent 场景应把私有 evaluator 放到 Agent 无法读取的 grader container / VM 中。

---

## 2. Pareto Archive

v0.2 会真正使用 `ResearchSpec.objectives` 维护非支配前沿。

例如：

```json
"objectives": [
  {"name": "distance", "direction": "maximize"},
  {"name": "rate", "direction": "maximize"},
  {"name": "row_weight_x", "direction": "minimize"}
]
```

ResearchEvolve 不再只问：

> 哪个 candidate 的单一 score 最大？

还会保留：

> 哪些 candidate 在多目标意义下互不支配？

运行后自动生成：

```text
.researchevolve/run/pareto.json
```

查看：

```bash
research-evolve pareto \
  --workspace .researchevolve/run
```

---

## 3. Novelty Search

MAP-Elites 负责保存不同 behavior cell 的 elite，Novelty Archive 进一步估计 candidate 与历史行为的差异程度。

每个 candidate 的 novelty 会写入：

```json
"diagnostics": {
  "search": {
    "novelty": 0.73
  }
}
```

父代选择可以按概率偏向高 novelty elite：

```json
"search": {
  "novelty_probability": 0.25,
  "novelty_k": 5
}
```

这让搜索不容易快速塌缩到一个局部 family。

---

## 4. Checkpoint / Resume

每个 generation 边界可以保存：

- 当前 generation
- evaluated / valid 计数
- Python RNG state
- 每个 island 的 elite IDs
- Pareto frontier IDs
- Novelty archive IDs
- manifest fingerprint

默认文件：

```text
.researchevolve/run/checkpoint.json
```

继续运行：

```bash
research-evolve run \
  --spec research.json \
  --evaluator evaluator.py \
  --seeds seeds.json \
  --workspace .researchevolve/run \
  --resume
```

如果 spec、seeds、evaluator 文件内容、mutator 或 domain pack 已经变化，ResearchEvolve 会拒绝把旧 checkpoint 当作同一次实验继续。

---

## 5. Reproducibility Manifest

每个新 run 自动写：

```text
.researchevolve/run/manifest.json
```

包含：

- 完整 ResearchSpec
- seeds 的稳定 SHA-256
- evaluator 路径和文件 SHA-256
- mutator 类型
- domain pack
- ResearchEvolve 版本
- Python 版本
- 平台信息
- 输入 fingerprint

查看：

```bash
research-evolve manifest \
  --workspace .researchevolve/run
```

这不是“保证所有外部 solver 完全确定性”的魔法，但它建立了实验可追踪的最低契约。

---

## 6. Domain Pack

v0.2 引入正式 `DomainPack` 接口，把领域数学与通用搜索引擎分开。

一个 Domain Pack 负责提供：

```text
DomainPack
├── evaluator cascade
├── four-level mutator
└── seed normalization (optional)
```

内置 pack 可以直接使用短名称：

```bash
--domain-pack qldpc
```

自定义 pack 使用：

```bash
--domain-pack my_package.domain:MyDomainPack
```

基础接口：

```python
from pathlib import Path

from research_evolve.domain import DomainPack
from research_evolve.mutation import FourLevelMutator


class MyDomainPack(DomainPack):
    name = "my-domain"

    def evaluator_paths(self) -> list[Path]:
        return [
            Path("evaluators/constraints.py"),
            Path("evaluators/exact.py"),
        ]

    def mutator(self) -> FourLevelMutator:
        return MyDomainMutator()
```

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

## 2. 跑 target42 最小 Demo

```bash
research-evolve run \
  --spec examples/target42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/target42/seeds.json \
  --workspace .researchevolve/target42 \
  --islands 4
```

输出工作区：

```text
.researchevolve/target42/
├── candidates.sqlite3
├── research_graph.sqlite3
├── checkpoint.json
├── manifest.json
├── pareto.json
└── summary.json
```

查看最佳 canonical-score candidates：

```bash
research-evolve inspect \
  --workspace .researchevolve/target42 \
  --limit 10
```

导出 Research Graph：

```bash
research-evolve graph \
  --workspace .researchevolve/target42 \
  --output research-graph.json
```

---

# qLDPC v0.2 Benchmark

v0.2 自带第一个真实数学 Domain Pack：

```text
research_evolve/domains/qldpc/
├── common.py
├── mutation.py
├── evaluator_constraints.py
├── evaluator_parameters.py
└── evaluator_distance.py
```

它使用一个很小的 **circulant bicycle CSS code** 搜索空间，只依赖 Python 标准库，不需要 MAGMA。

运行：

```bash
research-evolve run \
  --spec examples/qldpc/spec.json \
  --domain-pack qldpc \
  --seeds examples/qldpc/seeds.json \
  --workspace .researchevolve/qldpc \
  --islands 4
```

Evaluator cascade：

```text
Candidate
   ↓
1. 输入 / CSS commutation
   ↓
2. GF(2) rank → n, k, rate, row weight
   ↓
3. 小规模 exact distance enumeration
   ↓
canonical score
```

示例 ResearchSpec 同时优化：

```text
distance ↑
rate ↑
row_weight_x ↓
```

并使用：

```text
representation
size
density_bucket
```

作为 behavior dimensions。

查看 Pareto frontier：

```bash
research-evolve pareto \
  --workspace .researchevolve/qldpc
```

### 重要边界

这个 qLDPC pack 是 **ResearchEvolve 集成 benchmark**，不是大规模 qLDPC 距离求解器。

为了让 CI 和新手电脑都能跑，当前把 circulant size 限制在很小范围，并使用 exact weight enumeration。

后续真正研究级 qLDPC pack 应替换为：

```text
Candidate
   ↓
syntax / construction validation
   ↓
CSS commutation
   ↓
GF(2) rank
   ↓
cheap distance proxy
   ↓
BP-OSD
   ↓
OSD-CS / stronger decoding estimate
   ↓
MILP exact distance for selected elites
```

核心 Search Engine 不需要因为 evaluator 升级而改动。

---

# ResearchSpec v0.2

生成模板：

```bash
research-evolve init research.json
```

典型结构：

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
  "search": {
    "novelty_probability": 0.25,
    "novelty_k": 5,
    "migration_interval": 5,
    "migrants_per_island": 1,
    "checkpoint_interval": 1
  },
  "metadata": {}
}
```

`score` 仍然负责通用 MAP-Elites / best candidate 排序；`objectives` 则用于 Pareto frontier。

---

# 四层 Mutation

统一抽象：

```text
Level 1  Local Mutation
Level 2  Structural Mutation
Level 3  Algebraic Mutation
Level 4  Representation Mutation
```

自定义：

```python
from research_evolve.mutation import FourLevelMutator


class MyDomainMutator(FourLevelMutator):
    def local(self, payload, rng):
        return payload

    def structural(self, payload, rng):
        return payload

    def algebraic(self, payload, rng):
        return payload

    def representation(self, payload, rng):
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

qLDPC 内置 mutator 对应：

- Local：移动一个 circulant shift
- Structural：增删 shift 或改变小规模 circulant size
- Algebraic：应用模环上的 affine/unit 变换
- Representation：circulant ↔ polynomial 表示切换

---

# Candidate DB

SQLite 保存：

- payload
- parent IDs
- mutation level
- generation
- valid / canonical score
- metrics
- behavior
- diagnostics

因此最佳结果不是一个孤立 JSON，而可以向上追溯整个 evolutionary lineage。

---

# Research Graph

当前自动记录：

```text
Problem
  └─ investigates → Candidate A
                         ├─ evaluated_as → Evaluation A
                         └─ mutated_to   → Candidate B
                                              └─ evaluated_as → Evaluation B
```

后续会扩展：

```text
Hypothesis
Experiment
Conjecture
Counterexample
Lemma
Proof
Verification
```

Research Graph 负责“研究进展与因果 lineage”；普通向量 RAG 以后负责“文献和外部知识检索”，二者职责不同。

---

# Python API

```python
from research_evolve.engine import ResearchEngine
from research_evolve.spec import ResearchSpec

spec = ResearchSpec.from_dict(...)

with ResearchEngine(spec, workspace=".researchevolve/my-run") as engine:
    summary = engine.run(
        seed_payloads=[{"x": 0}],
        evaluator_paths=[
            "evaluators/constraints.py",
            "evaluators/exact.py",
        ],
    )

print(summary.to_dict())
```

Resume：

```python
with ResearchEngine(spec, workspace=".researchevolve/my-run") as engine:
    summary = engine.run(
        seed_payloads=seeds,
        evaluator_paths=evaluators,
        resume=True,
    )
```

---

# 测试与 CI

本地：

```bash
pytest -q
```

GitHub Actions 会在 PR 上测试：

- Python 3.10
- Python 3.12
- pytest
- target42 CLI smoke test

当前测试覆盖：

- ResearchSpec / SearchPolicy validation
- Candidate DB round-trip
- MAP-Elites diversity
- Pareto dominance
- Novelty behavior distance
- 四层 mutation
- Evaluator Cascade short-circuit
- evaluator timeout / protocol path
- Research Graph edges
- Engine end-to-end
- checkpoint / resume
- resume input fingerprint protection
- qLDPC small-code parameters and exact distance reference

---

# 版本路线

## v0.1 — Research Harness Foundation

```text
ResearchSpec
Hidden evaluator protocol
Candidate DB
MAP-Elites
Island populations
Four-level mutation
Research Graph
CLI
```

## v0.2 — Search Quality & Reproducibility

```text
Evaluator Cascade
Pareto Archive
Novelty Search
Checkpoint / Resume
Reproducibility Manifest
Domain Pack interface
qLDPC benchmark
GitHub Actions CI
```

## v0.3 — LLM-guided Evolution

计划：

```text
LLM Explorer
Mutation Proposal Agent
Idea Genome
Semantic Crossover
Prompt / model adapters
Experiment budget accounting
```

## v0.4 — Scientific Reasoning Loop

```text
Observation → Conjecture
Counterexample search
Hypothesis refinement
Literature grounding
```

## v0.5+

```text
Proof planner
Independent prover / verifier
Lean / symbolic verification
Research report generation
Autonomous Mathematical Research Lab
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

让数学研究从“一次回答”变成一个可以持续搜索、积累、恢复、验证和复现的研究过程。
