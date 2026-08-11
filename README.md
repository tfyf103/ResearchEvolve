# ResearchEvolve

> **A research harness for AI-driven mathematical discovery.**
>
> ResearchEvolve 把 **LLM 创造性、演化搜索、自动评测、经验猜想、反例攻击、自然语言证明与独立验证** 放进一个可审计的科研执行环境里。

当前版本：**v0.5.0**

## 项目定位

ResearchEvolve 不是一个巨大 Prompt，也不是固定的多 Agent 聊天室。它是一套把“提出想法”和“决定真假”分开的研究基础设施：

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
                    ┌─────────┴──────────┐
                    ▼                    ▼
               IdeaMemory         ConjectureMemory
                                         │
                                         ▼
                                  Proof Pipeline
                              ┌──────────┼──────────┐
                              ▼          ▼          ▼
                         ProofPlan    Prover    Verifier
                              └──────────┼──────────┘
                                         ▼
                                   ProofMemory
```

## 核心原则

1. **LLM 不拥有最终裁决权。** Explorer、Conjecturer、Prover 都不能单方面宣布结果正确。
2. **有限实验不是证明。** v0.4 只有 `empirically_supported`，没有 `proved`。
3. **自然语言验证也不是形式化证明。** v0.5 的最高状态是 `verified_natural_language`。
4. **先找反例，再花证明预算。** ProofPipeline 会先扫描所有已经独立评测过的 Candidate。
5. **Verifier 与 Prover 必须独立。** Command adapter 会按底层脚本/命令内容计算 role-independent implementation identity。
6. **不要只保留 Top-K。** MAP-Elites、islands、Pareto 和 novelty 共同保护不同研究路线。
7. **昂贵验证分层。** Evaluator Cascade 从便宜到昂贵短路无效候选。
8. **研究过程必须可恢复、可追踪。** Candidate、Idea、Observation、Conjecture、Counterexample、ProofPlan、Lemma、ProofArtifact、ProofReview 都有持久化 lineage。
9. **模型供应商可替换。** 外部 command adapter 可以包装 OpenAI、Claude、Gemini、本地模型或确定性程序。

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
- archive-first + mutation-driven Counterexample Search
- persistent `ConjectureMemory`
- conjecture refinement lineage
- explicit `proposed / empirically_supported / refuted / invalid` statuses
- resume-safe empirical test journal

详细设计：[`docs/V0.4.md`](docs/V0.4.md)

## v0.5 — Proof Planner → Prover → Independent Verifier

- frozen `ProofSpec`
- structured `ProofPlan`
- validated acyclic `LemmaSpec` dependency graph
- structured `ProofArtifact`
- provider-neutral `ProofPlanner / Prover / ProofVerifier` protocols
- `CommandProofPlanner / CommandProver / CommandProofVerifier`
- deterministic proof preflight over all valid CandidateDB entries
- hidden-assumption check against the frozen ProofSpec
- independent verifier implementation check
- deterministic review gate: verifier errors override a claimed `verified`
- `verified_natural_language / rejected / inconclusive / invalid` proof statuses
- persistent `ProofMemory`
- independent `proof_manifest.json`
- proof lineage in Research Graph

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

查看：

```bash
research-evolve observations --workspace .researchevolve/conjecture42
research-evolve conjectures --workspace .researchevolve/conjecture42
research-evolve counterexamples --workspace .researchevolve/conjecture42
```

Demo 故意提出一个错误猜想 `score < 0`，已有 `x=42` candidate 会把它 refute；另一个 `distance_to_42 >= 0` 在有限测试中可以进入 `empirically_supported`，但不会被标成 proved。

---

# Demo 5：proof42 — ProofSpec / Lemma Graph / Independent Verifier

v0.5 证明阶段运行在一个已经完成的 research workspace 上。先生成 v0.4 猜想：

```bash
research-evolve run \
  --spec examples/conjecture42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/conjecture42/seeds.json \
  --conjecturer-command "python examples/conjecture42/conjecturer.py" \
  --workspace .researchevolve/proof42 \
  --islands 2
```

然后运行 proof pipeline：

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

`proof42` 使用三个不同的确定性脚本模拟 Planner、Prover、Verifier，因此 CI 不需要任何 API key。

预期链路：

```text
Empirically Supported Conjecture
              │
              ▼
      scan CandidateDB again
              │
              ▼
           ProofSpec
              │
              ▼
           ProofPlan
              │
      ┌───────┼───────┐
      ▼       ▼       ▼
    Lemma A Lemma B Lemma C
      └───────┼───────┘
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

# v0.4 Predicate DSL

Conjecturer 必须把猜想写成机器可测试 predicate：

```json
{
  "statement": "distance is non-negative",
  "predicate": {
    "left": {"source": "metrics", "key": "distance"},
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

比较：

```text
lt  le  gt  ge  eq  ne
```

不支持任意 Python 表达式，也不会调用 `eval` / `exec`。

---

# v0.5 Proof Pipeline 可信边界

## 1. Frozen ProofSpec

ProofSpec 固定：

```text
conjecture_id
statement
predicate
assumptions
evidence_candidate_ids
source generation
```

Planner/Prover 不能通过修改目标来“证明更容易的命题”。

## 2. Lemma DAG

ProofPlan 的 lemma 必须：

```text
label unique
statement non-empty
dependency exists
no self dependency
no cycle
within max_lemmas
```

## 3. ProofArtifact structural checks

Prover 必须为每个 planned lemma 给出 argument，并给出 final argument。声明使用的 assumption 必须来自 ProofSpec。

## 4. Independent verifier

Verifier 与 Prover 的 command implementation identity 必须不同。

Verifier 返回 `verified` 也不是无条件接受：

```text
any error issue → rejected
verified + confidence < threshold → inconclusive
verified + no error + confidence >= threshold → verified_natural_language
```

## 5. 不等于形式化证明

`verified_natural_language` 只表示：

> 自然语言证明经过结构检查，并被独立 adversarial verifier 接受。

它**不等于** Lean / Coq / Isabelle / proof-kernel verification。

---

# 持久化产物

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

证明阶段有独立 `proof_manifest.json`，不会修改 source research run 的 manifest/checkpoint。

---

# Research Graph

到 v0.5，图谱覆盖：

```text
Problem
├── Candidate
│   ├── Evaluation
│   ├── Idea / Proposal lineage
│   └── Counterexample
├── Observation
├── Conjecture
└── ProofSpec
    └── ProofPlan
        ├── Lemma
        ├── Lemma
        └── Lemma
             │
             ▼
        ProofArtifact
             │
             ▼
         ProofReview
```

v0.5 新关系包括：

```text
targets_conjecture
plans_for
decomposes_into
depends_on
implements_plan
claims_proof_of
reviews
supports_natural_language_proof_of
rejects_proof_for
proof_actor_failed
```

注意使用 `claims_proof_of` 而不是直接 `proves`：未验证 artifact 不应被 Research Graph 当作 theorem。

---

# Checkpoint / Resume

Evolutionary research 仍然使用：

```bash
research-evolve run ... --resume
```

恢复会对 CandidateDB、IdeaMemory、ConjectureMemory、Research Graph 和 archive 做 generation-consistent 清理与恢复。

v0.5 proof pipeline 是 source run 之后的独立阶段，不参与 `checkpoint.json`。这样 proof model 的非确定性不会改变 source evolution 的可恢复性。

---

# 安全边界

详细见 [`SECURITY.md`](SECURITY.md)。

简要说：

- Evaluator subprocess 是协议边界，不是强安全沙箱。
- CommandExplorer / CommandConjecturer / Proof command actors 也是协议边界，不是强沙箱。
- 生产环境应该把 Agent、Evaluator、Prover、Verifier 分容器、VM 或远程 worker。
- v0.4 Predicate DSL 不执行任意模型生成代码。
- v0.5 ProofArtifact 只作为结构化文本保存，不会由 ResearchEvolve 当代码执行。
- `verified_natural_language` 不能宣传成形式化证明。

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

长期目标是构建：

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
Independent verification
      +
Formal verification (later)
      +
Structured research memory
```

让数学研究从一次回答，变成一个可以持续搜索、积累、失败、修正、验证和复现的过程。
