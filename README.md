# ResearchEvolve

> **A research harness for AI-driven mathematical discovery.**

ResearchEvolve 把 **演化搜索、LLM 研究提案、自动评测、经验猜想、反例攻击、自然语言证明、独立验证与 Lean 形式化验证** 放进同一个可审计、可恢复、可复现的科研执行环境。

当前版本：**v0.6.0**

## 核心思想

ResearchEvolve 不允许同一个生成模型同时拥有“提出想法”和“宣布正确”的权力，而是逐步提高验证强度：

```text
ResearchSpec
    │
    ├── Mutation / Explorer ───────────────► Candidate
    │                                         │
    │                                 Evaluator Cascade
    │                                         │
    └── Observation → Conjecture ◄────────────┘
                          │
                          ▼
                Counterexample Search
                          │
                          ▼
                empirically_supported
                          │
                          ▼
                    Proof Pipeline
       ProofSpec → Lemma DAG → Prover
                          │
                          ▼
               Independent NL Verifier
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
                 ┌────────┴────────┐
                 ▼                 ▼
             diagnostics       axiom audit
                 │                 │
                 ▼                 ▼
              Repairer       formal_verified
```

### 状态边界

```text
有限实验通过
    ≠ theorem proved

LLM 写出证明
    ≠ proof verified

独立自然语言 verifier 接受
    = verified_natural_language
    ≠ Lean formal verification

冻结的 Lean theorem
+ 固定 Lean toolchain
+ Lean 成功
+ axiom audit 通过
    = formal_verified
```

`formal_verified` 只说明**被冻结的 Lean theorem**通过了配置的形式验证 gate。自然语言研究命题到 Lean theorem 的语义映射仍然必须由显式、可审计的 `formal_contracts` 提供。

---

# 版本能力

## v0.1 — Research Harness

- `ResearchSpec`
- Hidden Evaluator protocol
- SQLite `CandidateDB`
- MAP-Elites + islands
- 四层 Mutation：Local / Structural / Algebraic / Representation
- persistent `ResearchGraph`

## v0.2 — Search Quality & Reproducibility

- Evaluator Cascade
- Pareto Archive
- Novelty Archive
- Checkpoint / Resume
- `manifest.json`
- `DomainPack`
- qLDPC reference benchmark
- GitHub Actions CI

## v0.3 — Semantic Research Explorer

- `Explorer` / `CommandExplorer`
- `ResearchProposal`
- `IdeaGenome`
- semantic mutation / crossover
- `IdeaMemory`

详见 [`docs/V0.3.md`](docs/V0.3.md)。

## v0.4 — Observation → Conjecture → Counterexample

- `ObservationExtractor`
- `Conjecturer`
- safe machine-testable `Predicate` DSL
- archive-first + mutation-driven Counterexample Search
- `ConjectureMemory`
- conjecture refinement lineage
- `proposed / empirically_supported / refuted / invalid`

详见 [`docs/V0.4.md`](docs/V0.4.md)。

## v0.5 — Natural-language Proof Verification

- frozen `ProofSpec`
- explicit proof assumptions
- `ProofPlan` + acyclic Lemma DAG
- Prover / independent Verifier separation
- hidden-assumption rejection
- CandidateDB proof preflight
- stale-proof invalidation
- `ProofMemory`
- `proof_manifest.json`
- strongest status: `verified_natural_language`

详见 [`docs/V0.5.md`](docs/V0.5.md)。

## v0.6 — Lean Formal Verification + Repair

- frozen `FormalizationSpec`
- `ResearchSpec.metadata.formal_contracts`
- contract binds **exact conjecture statement + normalized v0.4 predicate**
- frozen Lean imports / trusted preamble / theorem signature / toolchain
- Formalizer / Repairer only provide theorem body (`proof_term`)
- model-supplied top-level helper declarations rejected in v0.6
- conservative source escape-hatch gate
- actual Lean compiler/kernel execution
- pinned `lean-toolchain`
- structured diagnostics-driven repair loop
- `#print axioms` audit
- default allowed axioms: `propext`, `Classical.choice`, `Quot.sound`
- `FormalMemory`, `formal_manifest.json`, `formal_summary.json`
- exact generated `.lean` files persisted under `formal_sources/`
- stale formal-certificate invalidation
- first `formal_verified` state

详见 [`docs/V0.6.md`](docs/V0.6.md)。

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

安装 Python 包并测试：

```bash
pip install -e ".[dev]"
pytest -q
```

v0.6 `formalize` 阶段还需要 Lean。仓库根目录包含固定的：

```text
lean-toolchain
```

---

# Demo 1：target42

```bash
research-evolve run \
  --spec examples/target42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/target42/seeds.json \
  --workspace .researchevolve/target42 \
  --islands 4
```

```bash
research-evolve inspect --workspace .researchevolve/target42
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

当前 reference cascade：

```text
constraints + CSS commutation
        ↓
GF(2) rank → n, k, rate, row weight
        ↓
exact small-code distance enumeration
```

> exact distance 当前仅用于 `size <= 7` 的小规模 integration benchmark，不是生产级 qLDPC distance solver。

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

```bash
research-evolve observations --workspace .researchevolve/conjecture42
research-evolve conjectures --workspace .researchevolve/conjecture42
research-evolve counterexamples --workspace .researchevolve/conjecture42
```

Demo 中 `score < 0` 会被反例推翻；`distance_to_42 >= 0` 最多进入 `empirically_supported`。

---

# Demo 5：proof42

先完成研究：

```bash
research-evolve run \
  --spec examples/proof42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/proof42/seeds.json \
  --conjecturer-command "python examples/conjecture42/conjecturer.py" \
  --workspace .researchevolve/proof42 \
  --islands 2
```

再运行自然语言证明 pipeline：

```bash
research-evolve prove \
  --workspace .researchevolve/proof42 \
  --planner-command "python examples/proof42/planner.py" \
  --prover-command "python examples/proof42/prover.py" \
  --verifier-command "python examples/proof42/verifier.py"
```

```bash
research-evolve proof-specs --workspace .researchevolve/proof42
research-evolve proof-plans --workspace .researchevolve/proof42
research-evolve proof-artifacts --workspace .researchevolve/proof42
research-evolve proof-reviews --workspace .researchevolve/proof42
```

---

# Demo 6：formal42 — Real Lean Failure → Repair → Kernel Success

完整链路：

```text
Research
→ Conjecture
→ verified_natural_language
→ Frozen Formal Contract
→ bad Lean proof term
→ real Lean failure
→ diagnostics
→ Repairer
→ real Lean success
→ #print axioms audit
→ formal_verified
```

## Step 1：Research / Conjecture

```bash
research-evolve run \
  --spec examples/formal42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/formal42/seeds.json \
  --conjecturer-command "python examples/conjecture42/conjecturer.py" \
  --workspace .researchevolve/formal42 \
  --islands 2
```

## Step 2：Natural-language Proof

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

`examples/formal42/formalizer.py` 故意先返回：

```lean
by exact 0
```

真实 Lean 必须拒绝它。Repairer 随后返回：

```lean
by exact Nat.zero_le _
```

只有真实 Lean gate + axiom audit 均成功，最终才会写入：

```text
formal_verified
```

检查形式化链：

```bash
research-evolve formal-specs --workspace .researchevolve/formal42
research-evolve formal-artifacts --workspace .researchevolve/formal42
research-evolve kernel-runs --workspace .researchevolve/formal42
research-evolve formal-manifest --workspace .researchevolve/formal42
```

---

# `formal_contracts`

v0.6 不允许 Formalizer 自己选择 theorem。ResearchSpec 必须提供明确映射：

```json
{
  "metadata": {
    "formal_contracts": [
      {
        "conjecture_statement": "Distance to 42 is always non-negative for evaluated candidates.",
        "conjecture_predicate": {
          "left": {"source": "metrics", "key": "distance_to_42"},
          "operator": "ge",
          "right_constant": 0
        },
        "backend": "lean4",
        "toolchain": "leanprover/lean4:v4.30.0",
        "theorem_name": "distance_to_42_nonnegative",
        "theorem_signature": "theorem distance_to_42_nonnegative (x : Int) : 0 ≤ Int.natAbs (x - 42)",
        "imports": [],
        "preamble": "",
        "metadata": {
          "semantic_mapping": "Explain and audit the informal→formal mapping here."
        }
      }
    ]
  }
}
```

Contract 同时绑定：

```text
exact conjecture statement
+
normalized v0.4 Predicate
```

所以即使两条 conjecture 的文字完全一样，只要机器 predicate 不同，也不能复用同一个 formal contract。

没有 exact contract 时：

```text
missing_contract
```

而不是让模型临时生成 theorem signature。

---

# Frozen preamble

项目专用定义必须由 contract 冻结，而不能让 Formalizer 自己定义：

```json
{
  "preamble": "def distanceTo42 (x : Int) : Nat := Int.natAbs (x - 42)",
  "theorem_signature": "theorem distanceTo42_nonnegative (x : Int) : 0 ≤ distanceTo42 x"
}
```

生成 source：

```lean
-- frozen imports

-- frozen preamble
def distanceTo42 ... := ...

-- frozen declaration; model controls only body
theorem distanceTo42_nonnegative ... := by
  ...

#print axioms distanceTo42_nonnegative
```

v0.6 拒绝 model-supplied top-level `helper_source`。Formalizer 需要辅助步骤时使用 theorem body 内部的 `have` / `show`。

---

# Lean gate

模型输出先经过 conservative source gate，默认拒绝：

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

然后 ResearchEvolve：

1. 在临时工作目录写入 frozen `lean-toolchain`；
2. 执行 `lean --version`；
3. 要求 detected version == contract version；
4. 编译完整 frozen theorem + generated proof term；
5. 检查 Lean exit code 与 diagnostics；
6. 解析 `#print axioms theoremName`；
7. 检查 axiom allowlist。

默认仅允许：

```text
propext
Classical.choice
Quot.sound
```

默认拒绝包括：

```text
sorryAx
Lean.trustCompiler
Custom.myAxiom
```

---

# Repair loop

失败的 `KernelResult` 会保存：

```text
exit_code
stdout / stderr
Lean diagnostics
detected_version
axioms
gate_reason
source_sha256
```

Repairer 只能修改 theorem body，不能改变：

```text
statement/predicate mapping
imports
preamble
theorem name
theorem signature
toolchain
```

预算耗尽：`repair_exhausted`。

环境错误（Lean 不存在或版本不符）：`environment_error`。

---

# Persistence

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
├── summary.json
├── proof_summary.json
├── formal_summary.json
└── formal_sources/
    └── *.lean
```

每个 Lean source 都会记录 SHA-256，并保留 kernel diagnostics / axiom dependency 供审计。

---

# Stale invalidation

如果后来出现新反例：

```text
Conjecture → refuted
ProofSpec → invalid
FormalizationSpec → invalidated
FormalArtifact → invalidated
KernelResult → invalidated
```

历史 Lean run 仍保留用于审计，但不再作为当前研究结论的有效 formal certificate。

---

# Security

详见 [`SECURITY.md`](SECURITY.md)。

几个关键限制：

- JSON subprocess boundary 不是 OS sandbox；
- Lean tactics / elaboration 是可执行计算；
- source gate 是 defense in depth，不是完整恶意代码隔离；
- autonomous Formalizer 应在独立 container / VM / remote worker 中运行 Lean；
- Lean worker 不应该拿到 API key、private evaluator 或其他敏感文件；
- `formal_verified` 只对 frozen Lean theorem 有意义；
- 对主动恶意 proof 的最高强度验证，后续还应加入 `lean4checker` / comparator / external checker。

---

# 路线图

```text
v0.1  Research Harness
v0.2  Evaluator Cascade + Pareto + Novelty + DomainPack
v0.3  Explorer + Idea Genome + Semantic Evolution
v0.4  Observation + Conjecture + Counterexample
v0.5  ProofSpec + Lemma DAG + Independent NL Verification
v0.6  Frozen Formal Contract + Lean Kernel + Axiom Audit + Repair
v0.7  Mathlib/Lean Project Environment + Premise Selection + lean4checker
v0.8  Comparator / External Checker + Isolated Formal Workers
v1.0  Autonomous Mathematical Research Lab
```

长期目标是让 ResearchEvolve 从：

```text
发现一个有趣结构
```

逐步走到：

```text
找到结构
→ 自动评测
→ 形成猜想
→ 寻找反例
→ 组织自然语言证明
→ 独立审查
→ 显式 formal contract
→ Lean kernel 验证
→ 可复现、可审计的研究证据链
```
