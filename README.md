# ResearchEvolve

> **A research harness for AI-driven mathematical discovery.**
>
> ResearchEvolve 把 **LLM 创造性、演化搜索、自动评测、经验猜想、反例攻击和结构化研究记忆** 放到同一个可审计闭环里，同时坚持：**生成者提出想法，Evaluator 决定候选是否合法；有限实验只能支持或反驳猜想，不能冒充证明。**

当前版本：**v0.4.0**

## 项目定位

ResearchEvolve 不是一个巨大 Prompt，也不是固定的多 Agent 聊天室。它是一个可以持续搜索、积累、恢复、复现和审计的数学研究执行环境。

```text
                              ResearchSpec
                                   │
                 ┌─────────────────┼─────────────────┐
                 ▼                 ▼                 ▼
        Four-level Mutation   Explorer / LLM    Observation
                 │                 │                 │
                 │          Research Proposal        ▼
                 │                 │            Conjecturer
                 │            Idea Genome            │
                 │                 │             Conjecture
                 │       Semantic Mutation/          │
                 │            Crossover              ▼
                 │                 │          Counterexample Search
                 └────────────┬────┘                 │
                              ▼                      │
                          Candidate ◄────────────────┘
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
                    ┌─────────┴─────────┐
                    ▼                   ▼
               IdeaMemory        ConjectureMemory
                    │                   │
                    └─────────┬─────────┘
                              ▼
                    Checkpoint + Manifest
```

## 核心原则

1. **LLM 不拥有最终裁决权。** Explorer 和 Conjecturer 都不能宣布 candidate 正确。
2. **有限实验不是证明。** v0.4 只有 `empirically_supported`，没有 `proved`。
3. **先找反例，再谈信心。** 每个机器可测试猜想都会先扫描已有 archive，再按预算主动搜索 counterexample。
4. **不要只保留 Top-K。** MAP-Elites、islands、Pareto 和 novelty 共同保护不同研究路线。
5. **昂贵验证分层。** Evaluator Cascade 从便宜到昂贵短路无效候选。
6. **研究过程必须可恢复、可追踪。** Candidate、Idea、Observation、Conjecture、Counterexample 都有持久化 lineage。
7. **模型供应商可替换。** CommandExplorer / CommandConjecturer 可以包装 OpenAI、Claude、Gemini、本地模型或确定性程序。

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
- semantic mutation / crossover
- `IdeaMemory` feedback loop
- Idea → Proposal → Candidate lineage
- Explorer failure isolation

详细设计：[`docs/V0.3.md`](docs/V0.3.md)

## v0.4 — Observation → Conjecture → Counterexample

- deterministic `ObservationExtractor`
- provider-neutral `Conjecturer`
- external `CommandConjecturer`
- safe machine-testable `Predicate` DSL
- archive-first Counterexample Scan
- mutation-driven Counterexample Search
- persistent `ConjectureMemory`
- `Observation / Conjecture / Counterexample` Research Graph nodes
- conjecture refinement lineage
- resume-safe empirical test journal
- explicit `proposed / empirically_supported / refuted / invalid` statuses

详细设计：[`docs/V0.4.md`](docs/V0.4.md)

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

安装与测试：

```bash
pip install -e ".[dev]"
pytest -q
```

---

# Demo 1：target42 — 基础 Evolutionary Harness

```bash
research-evolve run \
  --spec examples/target42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/target42/seeds.json \
  --workspace .researchevolve/target42 \
  --islands 4
```

查看：

```bash
research-evolve inspect --workspace .researchevolve/target42 --limit 10
research-evolve pareto --workspace .researchevolve/target42
research-evolve manifest --workspace .researchevolve/target42
research-evolve graph --workspace .researchevolve/target42 --output target42-graph.json
```

---

# Demo 2：qLDPC Domain Pack

v0.2 内置一个**纯 Python、小规模、正确性优先**的 circulant bicycle/CSS benchmark：

```bash
research-evolve run \
  --spec examples/qldpc/spec.json \
  --domain-pack qldpc \
  --seeds examples/qldpc/seeds.json \
  --workspace .researchevolve/qldpc \
  --islands 4
```

Evaluator Cascade：

```text
Candidate
   ↓
constraints + CSS commutation
   ↓
GF(2) rank → n, k, rate, row weight
   ↓
exact small-code distance enumeration
```

> 当前 exact distance 只用于 `size <= 7` 的集成 benchmark，不是生产级 qLDPC distance solver。未来可以替换/扩展成 BP、BP-OSD、OSD-CS、MILP，而无需改通用 Research Engine。

领域化四层 mutation：

```text
Local           少量 circulant shift 修改
Structural      shift 数量 / 小规模结构变化
Algebraic       modular affine / unit transform
Representation  circulant ↔ polynomial
```

---

# Demo 3：semantic42 — Explorer / Idea Genome

无需 API key：

```bash
research-evolve run \
  --spec examples/semantic42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/semantic42/seeds.json \
  --explorer-command "python examples/semantic42/explorer.py" \
  --workspace .researchevolve/semantic42 \
  --islands 2
```

查看：

```bash
research-evolve ideas --workspace .researchevolve/semantic42
research-evolve proposals --workspace .researchevolve/semantic42
```

研究链：

```text
Candidate A
    │
    └── inspired
          ▼
       Proposal ◄── proposed_as ── Idea Genome
          │
          └── realized_as
                 ▼
             Candidate B
                 │
                 ▼
              Evaluator
```

---

# Demo 4：conjecture42 — Observation / Conjecture / Counterexample

无需 API key：

```bash
research-evolve run \
  --spec examples/conjecture42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/conjecture42/seeds.json \
  --conjecturer-command "python examples/conjecture42/conjecturer.py" \
  --workspace .researchevolve/conjecture42 \
  --islands 2
```

查看 observation：

```bash
research-evolve observations \
  --workspace .researchevolve/conjecture42
```

查看猜想：

```bash
research-evolve conjectures \
  --workspace .researchevolve/conjecture42
```

查看反例：

```bash
research-evolve counterexamples \
  --workspace .researchevolve/conjecture42
```

Demo 故意提出：

```text
Conjecture A: score < 0
```

因为 seed 中包含 `x=42`，其 canonical score 为 `0`，所以已有 archive 会立即给出 counterexample，猜想进入：

```text
refuted
```

同时另一个猜想：

```text
distance_to_42 >= 0
```

在有限测试中可以进入：

```text
empirically_supported
```

但永远不会被 v0.4 标记为 proved。

---

# v0.4 Predicate DSL

Conjecturer 必须把猜想写成机器可测试 predicate。

最小示例：

```json
{
  "statement": "distance is non-negative",
  "predicate": {
    "left": {
      "source": "metrics",
      "key": "distance"
    },
    "operator": "ge",
    "right_constant": 0
  }
}
```

可引用：

```text
score
payload.<key>
metrics.<key>
behavior.<key>
```

支持比较：

```text
lt  le  gt  ge  eq  ne
```

也可引用另一个字段：

```json
{
  "left": {"source": "metrics", "key": "distance"},
  "operator": "ge",
  "right_ref": {"source": "payload", "key": "lower_bound"}
}
```

不支持任意 Python 表达式，也不会调用 `eval` / `exec`。

---

# Counterexample Search

每个 conjecture 会依次经过：

```text
Conjecture
   ↓
Scan current MAP-Elites / Pareto / Novelty candidates
   ↓
found violation? ── yes ──> refuted
   │
   no
   ↓
Counterexample trials
   ↓
sample parent
   ↓
Four-level mutation
   ↓
Evaluator Cascade
   ↓
Predicate test
   ↓
violation? ── yes ──> refuted
   │
   no
   ↓
min_evidence reached?
   ├── yes → empirically_supported
   └── no  → proposed
```

主动反例搜索得到的新 candidate 会进入正常 archive，所以攻击猜想也会改善后续研究状态。

---

# ResearchSpec v0.4

```json
{
  "conjecture": {
    "enabled": true,
    "interval": 1,
    "observations_per_interval": 12,
    "conjectures_per_interval": 2,
    "context_candidates": 24,
    "context_conjectures": 12,
    "counterexample_trials": 8,
    "min_evidence": 3,
    "timeout_seconds": 60
  }
}
```

完整模板：

```bash
research-evolve init research.json
```

---

# 外部 Conjecturer

任何模型都可以通过一个 wrapper 接入，只需要遵守 JSON stdin/stdout 协议：

```bash
research-evolve run \
  --spec research.json \
  --evaluator evaluator.py \
  --seeds seeds.json \
  --conjecturer-command "python my_conjecturer.py"
```

Conjecturer 会看到：

```text
problem
objectives
constraints
structured observations
candidate summaries
previous conjectures + statuses
```

它看不到 evaluator 源码，也没有权力决定 truth status。

---

# 持久化产物

一次 v0.4 run 典型产生：

```text
.researchevolve/run/
├── candidates.sqlite3
├── ideas.sqlite3
├── conjectures.sqlite3
├── research_graph.sqlite3
├── checkpoint.json
├── manifest.json
├── pareto.json
└── summary.json
```

`conjectures.sqlite3`：

```text
observations
conjectures
conjecture_tests
counterexamples
```

每一个 empirical test 都单独记录，因此 checkpoint resume 可以可靠 prune 半截 generation 并重新计算猜想状态。

---

# Research Graph

到 v0.4，图谱已经覆盖：

```text
Problem
├── Candidate
│   ├── Evaluation
│   ├── Idea / Proposal lineage
│   └── Counterexample
├── Observation
│   └── derived_from Candidate
└── Conjecture
    ├── suggested_by Observation
    ├── refined_from Conjecture
    └── refuted_by Candidate
```

主要关系：

```text
investigates
evaluated_as
mutated_to
inspired
proposed_as
realized_as
expresses
has_observation
derived_from
suggests
has_conjecture
refined_into
evidence_for
refutes
instantiated_as
counterexample_to
```

---

# Checkpoint / Resume

```bash
research-evolve run ... --resume
```

恢复时会：

```text
restore RNG + MAP-Elites / Pareto / Novelty
prune IdeaMemory after checkpoint generation
prune Observation / Conjecture / Tests / Counterexamples after checkpoint generation
recompute surviving conjecture statuses
resume next generation
```

Conjecture context 只从 checkpoint 恢复出的 archives 构造，不直接把 CandidateDB 中可能残留的半截 generation candidate 混入新上下文。

---

# 安全边界

详细见 [`SECURITY.md`](SECURITY.md)。

简要说：

- Evaluator subprocess 是协议边界，不是强安全沙箱。
- CommandExplorer / CommandConjecturer 也是协议边界，不是强沙箱。
- 生产环境应该把 Agent / Explorer / Conjecturer 与 private evaluator/grader 分容器、VM 或远程 worker。
- v0.4 Predicate DSL 不执行任意模型生成代码。

---

# 路线图

```text
v0.1  ResearchSpec + Hidden Evaluator + Candidate DB + MAP-Elites + Mutation + Research Graph
v0.2  Evaluator Cascade + Pareto + Novelty + Checkpoint + DomainPack + qLDPC
v0.3  Explorer + Idea Genome + Semantic Mutation/Crossover + IdeaMemory
v0.4  Observation + Conjecture + Counterexample + Empirical Refinement
v0.5  Proof Planner + Lemma Decomposition + Prover + Adversarial Verifier
v0.6  Lean / symbolic formal verification
v1.0  Autonomous Mathematical Research Lab
```

长期目标不是做一个会“说像数学家一样的话”的 Agent，而是构建：

```text
LLM creativity
      +
Evolutionary search
      +
Automated evaluation
      +
Empirical conjecture formation
      +
Counterexample attack
      +
Structured research memory
      +
Independent verification
```

让数学研究从一次回答，变成一个可以持续搜索、积累、失败、修正、验证和复现的过程。
