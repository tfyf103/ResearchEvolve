# ResearchEvolve

> **A research harness for AI-driven mathematical discovery.**
>
> ResearchEvolve 把 **LLM 创造性、演化搜索、自动评测、经验猜想、反例攻击、自然语言证明与独立验证** 放进一个可审计、可恢复、可复现的科研执行环境。

当前版本：**v0.5.0**

## 核心思想

ResearchEvolve 不让一个模型同时扮演“提出想法、评测、证明、宣布正确”的全部角色，而是把研究过程拆成彼此可审计的可信边界：

```text
ResearchSpec
    │
    ├── Four-level Mutation ─────────────┐
    │                                    │
    ├── Explorer → Idea Genome ──────────┤
    │                                    ▼
    │                                Candidate
    │                                    │
    │                            Evaluator Cascade
    │                                    │
    │                   MAP-Elites / Pareto / Novelty
    │                                    │
    └── Observation → Conjecture ─────────┤
                         │                │
                         ▼                │
                 Counterexample Search ◄──┘
                         │
                         ▼
              empirically_supported
                         │
                         ▼
                   Proof Pipeline
      ProofSpec → ProofPlan → Lemma DAG → Prover
                                      │
                                      ▼
                           Independent Verifier
                                      │
                                      ▼
                         verified_natural_language
```

### 不可跨越的状态边界

```text
有限实验通过
    ≠ theorem proved

LLM 写出证明
    ≠ proof verified

独立自然语言 verifier 接受
    = verified_natural_language
    ≠ formal_verified
```

真正的 `formal_verified` 留给后续 Lean / Coq / Isabelle / kernel checking。

---

# 版本能力

## v0.1 — Research Harness

- `ResearchSpec`
- process-separated Hidden Evaluator protocol
- SQLite `CandidateDB`
- MAP-Elites + island populations
- 四层 Mutation：Local / Structural / Algebraic / Representation
- persistent `ResearchGraph`

## v0.2 — Search Quality & Reproducibility

- Evaluator Cascade
- Pareto Archive
- Novelty Archive
- Checkpoint / Resume
- reproducibility `manifest.json`
- `DomainPack`
- qLDPC reference benchmark
- GitHub Actions CI

## v0.3 — Semantic Research Explorer

- provider-neutral `Explorer`
- `CommandExplorer`
- `ResearchProposal`
- `IdeaGenome`
- semantic mutation / crossover
- persistent `IdeaMemory`

详细设计：[`docs/V0.3.md`](docs/V0.3.md)

## v0.4 — Observation → Conjecture → Counterexample

- deterministic `ObservationExtractor`
- provider-neutral `Conjecturer`
- safe machine-testable `Predicate` DSL
- archive-first + mutation-driven Counterexample Search
- persistent `ConjectureMemory`
- conjecture refinement lineage
- `proposed / empirically_supported / refuted / invalid`

详细设计：[`docs/V0.4.md`](docs/V0.4.md)

## v0.5 — Proof Planner → Prover → Independent Verifier

- frozen `ProofSpec`
- explicit proof assumptions / definitions
- structured `ProofPlan`
- acyclic `LemmaSpec` dependency graph
- structured `ProofArtifact`
- provider-neutral Planner / Prover / Verifier interfaces
- CandidateDB proof preflight
- hidden-assumption rejection
- Prover / Verifier implementation-separation check
- deterministic verifier gate
- stale-proof invalidation when a later counterexample appears
- persistent `ProofMemory`
- independent `proof_manifest.json`
- proof lineage in `ResearchGraph`

详细设计：[`docs/V0.5.md`](docs/V0.5.md)

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
pytest -q
```

---

# Demo 1：target42

验证最基础的 evolutionary research loop：

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
```

---

# Demo 2：qLDPC Domain Pack

```bash
research-evolve run \
  --spec examples/qldpc/spec.json \
  --domain-pack qldpc \
  --seeds examples/qldpc/seeds.json \
  --workspace .researchevolve/qldpc \
  --islands 4
```

当前内置 benchmark 是纯 Python、小规模、正确性优先的 circulant bicycle/CSS pipeline：

```text
constraints + CSS commutation
        ↓
GF(2) rank → n, k, rate, row weight
        ↓
exact small-code distance enumeration
```

> exact distance 当前只用于 `size <= 7` 的集成 benchmark，不是生产级 qLDPC distance solver。后续可以替换为 BP、BP-OSD、OSD-CS、MILP，而无需改通用 Research Engine。

---

# Demo 3：semantic42

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

---

# Demo 4：conjecture42

```bash
research-evolve run \
  --spec examples/conjecture42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/conjecture42/seeds.json \
  --conjecturer-command "python examples/conjecture42/conjecturer.py" \
  --workspace .researchevolve/conjecture42 \
  --islands 2
```

查看：

```bash
research-evolve observations --workspace .researchevolve/conjecture42
research-evolve conjectures --workspace .researchevolve/conjecture42
research-evolve counterexamples --workspace .researchevolve/conjecture42
```

Demo 中：

- `score < 0` 会被 `x=42, score=0` 反驳；
- `distance_to_42 >= 0` 会在有限证据下变成 `empirically_supported`；
- v0.4 不会把它标成 proved。

---

# Demo 5：proof42

v0.5 证明阶段是一个**独立的 post-research phase**。

## Step 1：先完成发现 / 猜想阶段

proof42 使用专用 ResearchSpec，因为其中显式声明了证明所需的距离定义：

```json
{
  "metadata": {
    "proof_assumptions": [
      "For every evaluated numeric candidate x, distance_to_42 is defined as abs(x - 42)."
    ]
  }
}
```

运行：

```bash
research-evolve run \
  --spec examples/proof42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/proof42/seeds.json \
  --conjecturer-command "python examples/conjecture42/conjecturer.py" \
  --workspace .researchevolve/proof42 \
  --islands 2
```

## Step 2：运行 Proof Pipeline

```bash
research-evolve prove \
  --workspace .researchevolve/proof42 \
  --planner-command "python examples/proof42/planner.py" \
  --prover-command "python examples/proof42/prover.py" \
  --verifier-command "python examples/proof42/verifier.py" \
  --max-conjectures 4 \
  --max-lemmas 24 \
  --min-verifier-confidence 0.7
```

## Step 3：检查证明链

```bash
research-evolve proof-specs --workspace .researchevolve/proof42
research-evolve proof-plans --workspace .researchevolve/proof42
research-evolve proof-artifacts --workspace .researchevolve/proof42
research-evolve proof-reviews --workspace .researchevolve/proof42
research-evolve proof-manifest --workspace .researchevolve/proof42
```

预期链路：

```text
Empirically Supported Conjecture
              │
              ▼
     scan every valid CandidateDB row
              │
       counterexample?
        ┌─────┴─────┐
       yes          no
        ▼            ▼
     refuted      ProofSpec
                      │
                      ▼
                  ProofPlan
                      │
               Lemma DAG
                      │
                      ▼
                 ProofArtifact
                      │
                      ▼
            Independent Verifier
                      │
                      ▼
          verified_natural_language
```

---

# v0.5：ProofSpec 为什么必须冻结 assumptions

一个数学证明经常依赖：

- 定义；
- 归一化约定；
- 问题硬约束；
- 已明确给定的公理或领域前提。

这些不能由 Prover 自己临时补出来。

ResearchEvolve v0.5 把两类前提冻结进 `ProofSpec.assumptions`：

1. `ResearchSpec.constraints` 中的 hard constraints；
2. `ResearchSpec.metadata.proof_assumptions` 中显式声明的定义 / 公理。

例如：

```json
{
  "metadata": {
    "proof_assumptions": [
      "Hx * Hz^T = 0 over GF(2) by the construction definition.",
      "The group operation is taken modulo l."
    ]
  }
}
```

Prover 返回：

```json
{
  "assumptions_used": [
    "The group operation is taken modulo l."
  ]
}
```

如果它使用了 ProofSpec 中不存在的前提，artifact 会在进入 Verifier 之前直接 `invalid`。

> `proof_assumptions` 是显式研究输入，不是系统替你证明的事实。对真实论文工作，应确保这些前提本身来自问题定义、已验证构造或可引用的定理。

---

# v0.5：Verifier gate

Verifier 的原始输出：

```text
verified | rejected | inconclusive
```

ResearchEvolve 再做确定性 gate：

```text
verifier says rejected
    → rejected

any VerificationIssue.severity == error
    → rejected

verified + confidence < threshold
    → inconclusive

verified + no error + confidence >= threshold
    → verified_natural_language
```

如果 Verifier 进程崩溃，ResearchEvolve 会写入一个 synthetic `inconclusive` review，而不会把 artifact 留在一个容易被误读的“drafted but maybe verified”状态。

---

# Prover / Verifier 独立性

Command actors 有两种身份：

```text
config identity
    用于 manifest / 审计，包含角色和完整命令配置

independence identity
    用于判断是否同一个底层实现
```

因此即使：

```bash
python same_wrapper.py --role prover
python same_wrapper.py --role verifier
```

只要它们指向同一个 wrapper 实现，v0.5 仍会拒绝这种“自证”。

生产环境最好进一步使用：

- 不同模型；
- 不同系统提示；
- 不同 worker；
- 不同供应商；
- 或最终使用 proof assistant kernel。

---

# Stale proof invalidation

自然语言验证不是永久真理标签。

每次 `research-evolve prove` 都会先重新扫描所有已经独立评测过的 valid candidates。

如果一个过去 `verified_natural_language` 的猜想后来被新 Candidate 反驳：

```text
Conjecture → refuted
ProofSpec → invalid
ProofPlan → invalid
Lemma nodes → invalid
ProofArtifact → invalid
ProofReview → invalid
```

原始 verifier decision 仍然保存在 proof journal 中用于审计，但 gated status 会失效。

---

# 持久化文件

一个完整 v0.5 workspace 可能包含：

```text
.researchevolve/run/
├── candidates.sqlite3
├── ideas.sqlite3
├── conjectures.sqlite3
├── proofs.sqlite3
├── research_graph.sqlite3
├── checkpoint.json
├── manifest.json
├── proof_manifest.json
├── pareto.json
├── summary.json
└── proof_summary.json
```

`proofs.sqlite3`：

```text
proof_specs
proof_plans
proof_artifacts
proof_reviews
```

`proof_manifest.json` 独立记录：

- source research-run fingerprint；
- Planner / Prover / Verifier identities；
- Prover / Verifier independence keys；
- lemma / evidence budgets；
- verifier confidence threshold。

Proof 阶段不会修改 source `manifest.json` 或 `checkpoint.json`。

---

# Research Graph

到 v0.5，新增节点：

```text
ProofSpec
ProofPlan
Lemma
ProofArtifact
ProofReview
ProofActorError
```

典型 lineage：

```text
Conjecture
    ▲
    │ targets_conjecture
ProofSpec
    ▲
    │ plans_for
ProofPlan
    │
    ├── decomposes_into → Lemma A
    ├── decomposes_into → Lemma B
    └── decomposes_into → Lemma C

ProofArtifact
    ├── implements_plan → ProofPlan
    └── claims_proof_of → ProofSpec

ProofReview
    ├── reviews → ProofArtifact
    └── supports_natural_language_proof_of → Conjecture
```

这里故意叫 `claims_proof_of`，因为未验证 artifact 不应被当作 theorem。

---

# 安全边界

详见 [`SECURITY.md`](SECURITY.md)。

简要说：

- Evaluator、Explorer、Conjecturer、Proof command actors 都只是协议边界，不是强沙箱；
- 生产环境应该隔离 Agent、Private Grader、Prover、Verifier；
- v0.4 Predicate DSL 不执行任意模型代码；
- v0.5 ProofArtifact 作为结构化文本存储，不会被 ResearchEvolve 当代码执行；
- `verified_natural_language` 不能宣传成 Lean / Coq / Isabelle formal proof。

---

# 路线图

```text
v0.1  ResearchSpec + Hidden Evaluator + Candidate DB + MAP-Elites + Mutation + Research Graph
v0.2  Evaluator Cascade + Pareto + Novelty + Checkpoint + DomainPack + qLDPC
v0.3  Explorer + Idea Genome + Semantic Mutation/Crossover + IdeaMemory
v0.4  Observation + Conjecture + Counterexample + Empirical Refinement
v0.5  ProofSpec + ProofPlan + Lemma DAG + Prover + Independent Adversarial Verifier
v0.6  Formalizer + Lean / symbolic verification + proof repair
v1.0  Autonomous Mathematical Research Lab
```

长期目标：

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
Structured proof planning
      +
Independent natural-language verification
      +
Formal verification (later)
      +
Structured research memory
```
