# ResearchEvolve

> **A research harness for AI-driven mathematical discovery.**
>
> ResearchEvolve 把 LLM 的创造性与可审计的演化搜索、自动评测、结构化研究记忆分开：**Explorer 负责提出想法，Evaluator 负责决定真假与质量。**

当前版本：**v0.3.0**

## 为什么做这个项目

ResearchEvolve 不是“让一个 LLM 一次回答数学题”的 Prompt，也不是固定的多 Agent 聊天室。它更接近一个可扩展的数学研究执行环境：

```text
                         ResearchSpec
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
        Four-level Mutation        Explorer / LLM
                 │                         │
                 │                  Research Proposal
                 │                         │
                 │                    Idea Genome
                 │                         │
                 │             Semantic Mutation/Crossover
                 │                         │
                 └──────────────┬──────────┘
                                ▼
                            Candidate
                                │
                                ▼
                        Evaluator Cascade
                                │
                ┌───────────────┼───────────────┐
                ▼               ▼               ▼
           MAP-Elites        Pareto          Novelty
            + Islands        Archive         Archive
                │               │               │
                └───────────────┼───────────────┘
                                ▼
                         Candidate DB
                                │
                         Research Graph
                                │
                    IdeaMemory / Feedback
                                │
                    Checkpoint + Manifest
```

核心原则：

1. **LLM 不拥有最终裁决权。** 所有 semantic candidate 仍要通过独立 evaluator。
2. **不要只保留 Top-K。** MAP-Elites、islands、Pareto 与 novelty 共同保护不同研究路线。
3. **研究过程必须可追踪。** 候选、父子关系、Idea Genome、提案、评测结果都持久化。
4. **昂贵验证要分层。** cheap-to-expensive evaluator cascade 避免把计算浪费在明显无效候选上。
5. **外部模型是可替换组件。** v0.3 不绑定 OpenAI / Claude / Gemini / 本地模型中的任何一家。

---

# 版本能力

## v0.1 — Research Harness

- `ResearchSpec`
- process-separated Hidden Evaluator protocol
- SQLite `CandidateDB`
- MAP-Elites
- island populations + migration
- 四层 Mutation：Local / Structural / Algebraic / Representation
- persistent `ResearchGraph`
- CLI 与 target42 demo

## v0.2 — Search Quality & Reproducibility

- Evaluator Cascade
- multi-objective Pareto Archive
- Novelty Archive + novelty-biased parent selection
- Checkpoint / Resume
- reproducibility `manifest.json`
- formal `DomainPack` interface
- built-in qLDPC reference benchmark
- GitHub Actions CI

## v0.3 — Semantic Research Explorer

- provider-neutral `Explorer` protocol
- external `CommandExplorer`
- structured `ResearchProposal`
- persistent `IdeaGenome`
- restricted `SemanticPatch`
- semantic mutation
- semantic crossover
- `IdeaMemory` feedback loop
- Research Graph 中的 Idea → Proposal → Candidate lineage
- Explorer failure isolation
- `ideas` / `proposals` CLI inspection
- semantic42 end-to-end demo

详细设计见 [`docs/V0.3.md`](docs/V0.3.md)。

---

# 安装

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

测试：

```bash
pytest -q
```

---

# Demo 1：target42 — 验证基础闭环

```bash
research-evolve run \
  --spec examples/target42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/target42/seeds.json \
  --workspace .researchevolve/target42 \
  --islands 4
```

查看最佳候选：

```bash
research-evolve inspect \
  --workspace .researchevolve/target42 \
  --limit 10
```

查看 Pareto frontier：

```bash
research-evolve pareto \
  --workspace .researchevolve/target42
```

查看实验 manifest：

```bash
research-evolve manifest \
  --workspace .researchevolve/target42
```

导出 Research Graph：

```bash
research-evolve graph \
  --workspace .researchevolve/target42 \
  --output target42-graph.json
```

---

# Demo 2：qLDPC Domain Pack

v0.2 开始内置一个**纯 Python、小规模、正确性优先**的 circulant bicycle/CSS benchmark：

```bash
research-evolve run \
  --spec examples/qldpc/spec.json \
  --domain-pack qldpc \
  --seeds examples/qldpc/seeds.json \
  --workspace .researchevolve/qldpc \
  --islands 4
```

它使用三阶段 evaluator cascade：

```text
Candidate
   ↓
constraints + CSS commutation
   ↓
GF(2) rank → n, k, rate, row weight
   ↓
exact small-code distance enumeration
```

> 当前 exact distance 只用于 `size <= 7` 的集成 benchmark，不是生产级 qLDPC distance solver。未来可以把后两级替换/扩展成 BP、BP-OSD、OSD-CS、MILP，而无需改动通用搜索引擎。

qLDPC pack 也实现了领域化四层 mutation：

```text
Local           少量 circulant shift 修改
Structural      shift 数量 / 小规模结构变化
Algebraic       modular affine / unit transform
Representation  circulant ↔ polynomial
```

---

# Demo 3：semantic42 — v0.3 Explorer 闭环

这个 demo 不需要 API key。`examples/semantic42/explorer.py` 是一个确定性脚本，用来模拟真正 LLM Explorer 应遵守的 JSON 协议。

```bash
research-evolve run \
  --spec examples/semantic42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/semantic42/seeds.json \
  --explorer-command "python examples/semantic42/explorer.py" \
  --workspace .researchevolve/semantic42 \
  --islands 2
```

查看 Idea Genome：

```bash
research-evolve ideas \
  --workspace .researchevolve/semantic42
```

查看提案与 evaluator 结果：

```bash
research-evolve proposals \
  --workspace .researchevolve/semantic42
```

你会得到类似的研究链：

```text
Candidate A
    │
    └── inspired
          ▼
       Proposal
          │
          ├── proposed_as ◄── Idea Genome
          │
          └── realized_as
                 ▼
             Candidate B
                 │
                 ├── evaluated_as → Evaluation
                 └── expresses ───→ Idea Genome
```

下一次 Explorer 调用会看到之前提案的 accepted / rejected / invalid 状态与 score，从而形成最小的**经验反馈闭环**。

---

# 创建自己的 ResearchSpec

```bash
research-evolve init research.json
```

核心结构：

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
  "explorer": {
    "enabled": false,
    "interval": 1,
    "proposals_per_interval": 2,
    "context_candidates": 8,
    "feedback_items": 12,
    "timeout_seconds": 60
  }
}
```

---

# Evaluator 协议

Evaluator 从 stdin 读取一个 candidate JSON，并向 stdout 输出：

```json
{
  "valid": true,
  "score": 0.82,
  "metrics": {
    "distance": 12,
    "rate": 0.15
  },
  "behavior": {
    "representation": "graph"
  },
  "diagnostics": {}
}
```

约定：

- `valid`：硬约束是否通过。
- `score`：统一为**越大越好**的 canonical score，供 MAP-Elites 等单分数机制使用。
- `metrics`：保留原始领域指标，Pareto Archive 按 `ResearchSpec.objectives` 使用。
- `behavior`：MAP-Elites / novelty 的行为特征。
- `diagnostics`：评测、cascade 和研究元数据。

多个 evaluator 可以形成 cascade：

```bash
research-evolve run \
  --spec research.json \
  --evaluator evaluator_constraints.py \
  --evaluator evaluator_cheap.py \
  --evaluator evaluator_exact.py \
  --seeds seeds.json
```

任意阶段 `valid=false` 时后续阶段不会执行。

---

# 四层 Mutation

内置 `FourLevelMutator` 是通用 fallback：

```text
Level 1  Local Mutation
Level 2  Structural Mutation
Level 3  Algebraic Mutation
Level 4  Representation Mutation
```

领域项目建议继承：

```python
from research_evolve.mutation import FourLevelMutator


class MyMutator(FourLevelMutator):
    def local(self, payload, rng):
        return payload

    def structural(self, payload, rng):
        return payload

    def algebraic(self, payload, rng):
        return payload

    def representation(self, payload, rng):
        return payload
```

CLI：

```bash
research-evolve run \
  --spec research.json \
  --evaluator evaluator.py \
  --seeds seeds.json \
  --mutator my_domain.mutations:MyMutator
```

---

# v0.3 Explorer 协议

Explorer 可以是：

- OpenAI 模型 wrapper
- Claude wrapper
- Gemini wrapper
- Qwen / DeepSeek / GLM wrapper
- 本地模型
- ChatGPT / Codex Skill
- 启发式算法

ResearchEvolve 本身不绑定 provider。

外部 Explorer 接收：

```text
Problem
Objectives / Constraints
Top / Pareto / Novel candidates
Candidate Idea Genomes
Recent proposal outcomes
```

它只能返回：

```text
semantic_mutation
semantic_crossover
```

并使用受限 `SemanticPatch`：

```json
{
  "set": {},
  "delete": [],
  "append": {}
}
```

因此 v0.3 的默认路径不是“让 LLM 任意执行代码”，而是：

```text
LLM idea
   ↓
structured proposal
   ↓
auditable patch/crossover
   ↓
candidate
   ↓
independent evaluator
```

完整 JSON schema 和 wrapper 示例见 [`docs/V0.3.md`](docs/V0.3.md)。

---

# Checkpoint / Resume

默认每代保存：

```text
checkpoint.json
```

恢复：

```bash
research-evolve run \
  --spec research.json \
  --evaluator evaluator.py \
  --seeds seeds.json \
  --workspace .researchevolve/my-run \
  --resume
```

Resume 会检查 manifest fingerprint。Spec、seeds、evaluators、mutator、domain pack 或 Explorer identity 变化时，不会静默把它们当成同一个实验继续。

对于外部 LLM，本项目不承诺采样结果 bit-for-bit 可复现；v0.3 的策略是把**实际返回的结构化提案和评测结果完整持久化**，保证研究过程可审计。

---

# 工作区 artifacts

一次 v0.3 运行通常产生：

```text
.researchevolve/run/
├── candidates.sqlite3
├── research_graph.sqlite3
├── ideas.sqlite3
├── checkpoint.json
├── manifest.json
├── pareto.json
└── summary.json
```

职责：

- `candidates.sqlite3`：candidate、lineage、score、metrics、behavior。
- `research_graph.sqlite3`：Problem / Idea / Proposal / Candidate / Evaluation 关系。
- `ideas.sqlite3`：Idea Genome、rationale、expected effects、provider metadata 与 evaluator outcome。
- `checkpoint.json`：恢复搜索状态。
- `manifest.json`：输入与运行环境指纹。
- `pareto.json`：最新多目标 frontier。
- `summary.json`：运行摘要。

---

# Domain Pack

Domain Pack 让领域数学知识不侵入通用 orchestrator。

一个 pack 可以提供：

```text
seed normalization
custom FourLevelMutator
evaluator cascade
```

内置：

```text
qldpc
```

长期可以扩展：

```text
combinatorics/
graph_theory/
circle_packing/
number_theory/
optimization/
geometry/
```

---

# 当前边界

ResearchEvolve v0.3 已经形成：

```text
Machine-evaluable discovery
        +
Evolutionary diversity search
        +
Structured semantic proposals
        +
Persistent idea memory
        +
Independent evaluation
```

但还没有把全部“AI 数学家”能力一次塞进来。当前尚未实现：

- 自动文献调查与引用核验
- 从实验数据自动形成 conjecture
- dedicated counterexample search
- proof decomposition / prover / skeptic
- Lean / Rocq formal verification

---

# Roadmap

```text
v0.1  ResearchSpec + evaluator + Candidate DB + MAP-Elites/islands + mutation + graph
v0.2  evaluator cascade + Pareto/novelty + checkpoint/resume + DomainPack + qLDPC
v0.3  Explorer + ResearchProposal + Idea Genome + semantic mutation/crossover + feedback
v0.4  Observation → Conjecture → Counterexample loop
v0.5  Proof planner + prover + skeptic + independent verifier
v0.6  Lean / symbolic formalization
v1.0  Autonomous Mathematical Research Lab
```

长期目标：

```text
Literature
    ↓
Explore
    ↓
Experiment / Evolve
    ↓
Observe
    ↓
Conjecture
    ↓
Attack / Counterexample
    ↓
Prove
    ↓
Verify
    ↓
Research report
```

让数学研究从“一次回答”变成一个可以持续搜索、积累、攻击、验证和复现的研究过程。
