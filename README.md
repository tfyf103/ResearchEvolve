# ResearchEvolve

> **A research harness for AI-driven mathematical discovery.**
>
> ResearchEvolve 把 **演化搜索、LLM 研究提案、自动评测、经验猜想、反例攻击、自然语言证明、独立验证与 Lean 形式化验证** 放进同一个可审计、可恢复、可复现的科研执行环境。

当前版本：**v0.6.0**

## ResearchEvolve 在解决什么问题？

ResearchEvolve 不让一个模型同时承担：

```text
提出想法
+ 生成候选
+ 评测候选
+ 提出猜想
+ 写证明
+ 宣布证明正确
```

而是把研究过程拆成越来越强的可信边界：

```text
                         ResearchSpec
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
     Four-level Mutation   Explorer/LLM     Observation
            │                 │                 │
            │             Idea Genome           ▼
            │                 │             Conjecturer
            └──────────┬──────┘                 │
                       ▼                        ▼
                   Candidate                Conjecture
                       │                        │
                       ▼                        ▼
               Evaluator Cascade      Counterexample Search
                       │                        │
          MAP-Elites / Pareto / Novelty        │
                       │                        │
                       └────────────┬───────────┘
                                    ▼
                           empirically_supported
                                    │
                                    ▼
                              Proof Pipeline
            ProofSpec → Lemma DAG → Prover → Independent Verifier
                                    │
                                    ▼
                         verified_natural_language
                                    │
                                    ▼
                           FormalizationSpec
                                    │
                                    ▼
                                Formalizer
                                    │
                              Lean proof term
                                    │
                                    ▼
                              Lean Kernel Gate
                              │             │
                           failure       success
                              │             │
                              ▼             ▼
                         diagnostics    axiom audit
                              │             │
                              ▼             ▼
                           Repairer    formal_verified
                              │
                              └──────────► Lean
```

## 状态边界

ResearchEvolve 刻意区分：

```text
实验样本都通过
    ≠ 数学证明

LLM 写出一段证明
    ≠ 已验证证明

独立自然语言 verifier 接受
    = verified_natural_language
    ≠ Lean kernel proof

冻结的 Lean theorem
+ 指定 Lean toolchain
+ kernel 成功
+ axiom audit 通过
    = formal_verified
```

即使得到 `formal_verified`，它也只说明**被冻结的 Lean theorem**通过了形式系统。自然语言研究命题到 Lean theorem 的语义映射仍必须由显式 `formal_contracts` 审计。

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
- stale-proof invalidation
- persistent `ProofMemory`
- independent `proof_manifest.json`

详细设计：[`docs/V0.5.md`](docs/V0.5.md)

## v0.6 — Lean Formal Verification + Repair

- frozen `FormalizationSpec`
- explicit `ResearchSpec.metadata.formal_contracts`
- frozen Lean imports / preamble / theorem signature / toolchain
- provider-neutral Formalizer / Repairer protocol
- model may generate only theorem body / proof term
- conservative source escape-hatch gate
- exact Lean version check
- real Lean compiler/kernel execution
- structured Lean diagnostics
- kernel-driven repair loop
- `#print axioms` dependency audit
- default allowlist: `propext`, `Classical.choice`, `Quot.sound`
- persistent `FormalMemory`
- `formal_manifest.json`
- persisted `.lean` source artifacts
- stale formal-proof invalidation
- first genuine `formal_verified` status

详细设计：[`docs/V0.6.md`](docs/V0.6.md)

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

安装 Python 包：

```bash
pip install -e ".[dev]"
pytest -q
```

v0.6 的形式化阶段还需要 Lean。仓库包含：

```text
lean-toolchain
```

用于固定集成验证 toolchain。

---

# Demo 1：target42 — 基础搜索闭环

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

当前内置 benchmark：

```text
constraints + CSS commutation
        ↓
GF(2) rank → n, k, rate, row weight
        ↓
exact small-code distance enumeration
```

> exact distance 当前只用于 `size <= 7` 的集成 benchmark，不是生产级 qLDPC distance solver。以后可以接 BP / BP-OSD / OSD-CS / MILP，而无需重写通用 Research Engine。

---

# Demo 3：semantic42 — Idea Genome / Semantic Evolution

```bash
research-evolve run \
  --spec examples/semantic42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/semantic42/seeds.json \
  --explorer-command "python examples/semantic42/explorer.py" \
  --workspace .researchevolve/semantic42 \
  --islands 2
```

```bash
research-evolve ideas --workspace .researchevolve/semantic42
research-evolve proposals --workspace .researchevolve/semantic42
```

---

# Demo 4：conjecture42 — Conjecture / Counterexample

```bash
research-evolve run \
  --spec examples/conjecture42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/conjecture42/seeds.json \
  --conjecturer-command "python examples/conjecture42/conjecturer.py" \
  --workspace .researchevolve/conjecture42 \
  --islands 2
```

```bash
research-evolve observations --workspace .researchevolve/conjecture42
research-evolve conjectures --workspace .researchevolve/conjecture42
research-evolve counterexamples --workspace .researchevolve/conjecture42
```

Demo 中：

- `score < 0` 被 `x=42, score=0` 反驳；
- `distance_to_42 >= 0` 只会变成 `empirically_supported`；
- 有限实验不会被宣传成证明。

---

# Demo 5：proof42 — Natural-language Proof Pipeline

## 1. Research / Conjecture

```bash
research-evolve run \
  --spec examples/proof42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/proof42/seeds.json \
  --conjecturer-command "python examples/conjecture42/conjecturer.py" \
  --workspace .researchevolve/proof42 \
  --islands 2
```

## 2. Proof

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

查看：

```bash
research-evolve proof-specs --workspace .researchevolve/proof42
research-evolve proof-plans --workspace .researchevolve/proof42
research-evolve proof-artifacts --workspace .researchevolve/proof42
research-evolve proof-reviews --workspace .researchevolve/proof42
research-evolve proof-manifest --workspace .researchevolve/proof42
```

v0.5 最强状态：

```text
verified_natural_language
```

而不是 `formal_verified`。

---

# Demo 6：formal42 — Real Lean Kernel + Repair Loop

`formal42` 从头跑完：

```text
Research
→ Conjecture
→ Natural-language Proof
→ Independent NL Verification
→ Lean Formalization
→ Lean failure
→ Repair
→ Lean success
→ Axiom audit
→ formal_verified
```

## Step 1：研究 / 猜想

```bash
research-evolve run \
  --spec examples/formal42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/formal42/seeds.json \
  --conjecturer-command "python examples/conjecture42/conjecturer.py" \
  --workspace .researchevolve/formal42 \
  --islands 2
```

## Step 2：自然语言证明

```bash
research-evolve prove \
  --workspace .researchevolve/formal42 \
  --planner-command "python examples/proof42/planner.py" \
  --prover-command "python examples/proof42/prover.py" \
  --verifier-command "python examples/proof42/verifier.py"
```

## Step 3：Lean Formalization

```bash
research-evolve formalize \
  --workspace .researchevolve/formal42 \
  --formalizer-command "python examples/formal42/formalizer.py" \
  --repairer-command "python examples/formal42/repairer.py" \
  --lean-command lean \
  --max-targets 4 \
  --max-repairs 2
```

这个 demo 的第一次 Formalizer 输出**故意是错误的 Lean proof term**：

```lean
by exact 0
```

真实 Lean 必须拒绝它。Repairer 再基于 kernel diagnostics 返回：

```lean
by exact Nat.zero_le _
```

只有第二次真实 kernel gate 成功后，状态才可能成为：

```text
formal_verified
```

查看：

```bash
research-evolve formal-specs --workspace .researchevolve/formal42
research-evolve formal-artifacts --workspace .researchevolve/formal42
research-evolve kernel-runs --workspace .researchevolve/formal42
research-evolve formal-manifest --workspace .researchevolve/formal42
```

---

# v0.6：为什么需要 `formal_contracts`？

如果让 Formalizer 自己同时写 theorem signature 和 proof，它可以把困难命题改成简单命题再让 Lean 验证。

所以 ResearchSpec 必须显式冻结目标：

```json
{
  "metadata": {
    "formal_contracts": [
      {
        "conjecture_statement": "Distance to 42 is always non-negative for evaluated candidates.",
        "backend": "lean4",
        "toolchain": "leanprover/lean4:v4.30.0",
        "theorem_name": "distance_to_42_nonnegative",
        "theorem_signature": "theorem distance_to_42_nonnegative (x : Int) : 0 ≤ Int.natAbs (x - 42)",
        "imports": [],
        "preamble": "",
        "metadata": {
          "semantic_mapping": "Explain why this Lean theorem faithfully represents the research claim."
        }
      }
    ]
  }
}
```

Contract 通过完整 `conjecture_statement` 匹配。

如果没有 contract：

```text
missing_contract
```

ResearchEvolve 不会让 Formalizer 临时发明一个 theorem。

---

# Frozen `preamble`

如果形式化命题需要项目自己的定义，把定义放进受信任的 contract：

```json
{
  "preamble": "def distanceTo42 (x : Int) : Nat := Int.natAbs (x - 42)",
  "theorem_signature": "theorem distanceTo42_nonnegative (x : Int) : 0 ≤ distanceTo42 x"
}
```

最终 Lean source：

```lean
-- frozen imports

-- frozen preamble
def distanceTo42 ... := ...

-- frozen theorem signature; only body is generated
theorem distanceTo42_nonnegative ... := by
  ...

#print axioms distanceTo42_nonnegative
```

Formalizer 不能重新定义 `distanceTo42`。

---

# v0.6 Lean Kernel Gate

模型输出首先经过 conservative source gate。

默认拒绝：

```text
sorry
admit
axiom
unsafe
extern
opaque
run_tac
elab
macro
syntax
#eval
#run
```

v0.6 还拒绝 model-supplied top-level `helper_source`。

Formalizer 可以在 theorem body 中写：

```lean
by
  have h₁ : ... := ...
  have h₂ : ... := ...
  exact ...
```

但不能在 theorem 前注入新的全局声明。

---

# Toolchain 与 Axiom Audit

`formal_verified` 不只依赖 `lean` 返回码。

ResearchEvolve 要求：

```text
source gate pass
        +
lean --version == frozen toolchain
        +
Lean exit code = 0
        +
no Lean error
        +
#print axioms output parseable
        +
no disallowed axiom dependency
```

默认允许的 Lean 标准公理：

```text
propext
Classical.choice
Quot.sound
```

默认拒绝例如：

```text
sorryAx
Lean.trustCompiler
Custom.myAxiom
```

因此：

```text
Lean process returned 0
```

仍不等于一定拿到：

```text
formal_verified
```

---

# Repair Loop

失败结果会形成结构化 `KernelResult`：

```text
exit_code
stdout / stderr
detected Lean version
diagnostics
axiom dependencies
gate_reason
source SHA-256
```

Repairer 看到：

```text
Frozen FormalizationSpec
Previous proof term
KernelResult
Previous kernel history
```

然后只能替换 theorem body：

```text
FormalArtifact #0
      │
      ├── kernel_rejected
      │
      └── repaired_into
             ▼
       FormalArtifact #1
             │
             └── formal_verified
```

预算耗尽：

```text
repair_exhausted
```

Lean 环境不存在 / 版本错误：

```text
environment_error
```

---

# 持久化文件

完整 workspace 现在可能包含：

```text
.researchevolve/run/
├── candidates.sqlite3
├── ideas.sqlite3
├── conjectures.sqlite3
├── proofs.sqlite3
├── formal.sqlite3
├── research_graph.sqlite3
├── checkpoint.json
├── manifest.json
├── proof_manifest.json
├── formal_manifest.json
├── pareto.json
├── summary.json
├── proof_summary.json
├── formal_summary.json
└── formal_sources/
    ├── formal-artifact-....lean
    └── formal-artifact-....lean
```

三个研究 journal：

```text
ideas.sqlite3
conjectures.sqlite3
proofs.sqlite3
formal.sqlite3
```

每次 Lean source 都会保存 SHA-256，kernel run 也保留 diagnostics 与 axiom dependency。

---

# Stale proof / formal invalidation

ResearchEvolve 不把历史形式化结果当永久不可撤销标签。

例如后来发现反例：

```text
Conjecture → refuted
Natural-language ProofSpec → invalid
FormalizationSpec → invalidated
FormalArtifact → invalidated
KernelResult → invalidated
```

历史 kernel 结果仍保留用于审计，但不会继续作为当前有效 formal certificate。

这里表达的是：

> Lean 在过去可能确实正确证明了某个 frozen theorem，但如果这个 theorem 与当前研究命题之间的上游映射/证据链失效，它不应该继续代表当前研究结论。

---

# Research Graph

到 v0.6，Research Graph 已覆盖：

```text
Problem
├── Candidate → Evaluation
├── Idea → Proposal → Candidate
├── Observation
├── Conjecture → Counterexample
├── ProofSpec
│   └── ProofPlan → Lemma DAG → ProofArtifact → ProofReview
└── FormalizationSpec
    └── FormalArtifact
        ├── LeanKernelResult
        └── repaired_into → FormalArtifact
```

新增关系包括：

```text
formalizes_proof
formal_target_for
implements_formalization
checks_formal_artifact
repaired_into
formally_verifies
formally_verifies_proof
```

---

# 安全边界

详细见 [`SECURITY.md`](SECURITY.md)。

尤其注意：

- subprocess JSON 协议不是 OS sandbox；
- Lean elaboration/tactics 本身是可执行计算；
- v0.6 source gate 是 defense in depth，不是完整隔离；
- autonomous Formalizer 应把 Lean 放到独立 container / VM / remote worker；
- Lean worker 不应拥有模型 API key、private evaluator 或其他敏感文件；
- `formal_verified` 只对 frozen Lean theorem 有意义，informal-to-formal mapping 仍需审计。

---

# 路线图

```text
v0.1  Research Harness
v0.2  Evaluator Cascade + Pareto + Novelty + DomainPack
v0.3  Explorer + Idea Genome + Semantic Evolution
v0.4  Observation + Conjecture + Counterexample
v0.5  ProofSpec + Lemma DAG + Prover + Independent NL Verifier
v0.6  Frozen Formal Contract + Lean Kernel + Axiom Audit + Proof Repair
v0.7  Formal Library Retrieval + Premise Selection + Lean Project/Mathlib Environment
v1.0  Autonomous Mathematical Research Lab
```

下一阶段最值得做的不是再造更多 Agent，而是让 formal layer 能处理真正研究级数学库：

```text
Mathlib / project environment
        +
formal theorem retrieval
        +
premise selection
        +
lemma search
        +
formal proof repair
        +
multiple isolated Lean workers
```

这样 ResearchEvolve 才能从 `formal42` 这种 reference benchmark 逐步走向真实组合数学、代数和 qLDPC 定理验证。
