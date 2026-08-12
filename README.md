# ResearchEvolve

> **A research harness for AI-driven mathematical discovery.**

ResearchEvolve 把 **演化搜索、LLM 研究提案、自动评测、经验猜想、反例攻击、自然语言证明、独立验证、Lean 形式化验证、冻结 Lean/Lake 工程与形式化 premise retrieval** 放进同一个可审计、可恢复、可复现的科研执行环境。

当前版本：**v0.7.0**

## 核心思想

ResearchEvolve 不允许同一个生成模型同时拥有“提出想法”和“宣布正确”的权力，而是让结论沿着一条逐步增强的证据链前进：

```text
ResearchSpec
    │
    ├── Mutation / Semantic Explorer ───────► Candidate
    │                                           │
    │                                   Evaluator Cascade
    │                                           │
    └── Observation → Conjecture ◄──────────────┘
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
                 frozen formal contract
                          │
                          ▼
                LeanProjectLock
                          │
              PremiseIndex / Retrieval
                          │
                          ▼
                 Formalizer / Repairer
                          │
                          ▼
                    Lake build
                          │
                          ▼
              Lean compile + axiom audit
                          │
                          ▼
                leanchecker --fresh
                          │
                          ▼
                    formal_verified
```

## 可信状态边界

```text
有限实验通过
    ≠ theorem proved

LLM 写出自然语言证明
    ≠ proof verified

独立自然语言 verifier 接受
    = verified_natural_language
    ≠ Lean formal verification

冻结 statement + machine Predicate
+ 冻结 theorem signature/imports/toolchain
+ 冻结 Lean/Lake project fingerprint
+ Lean 编译成功
+ #print axioms 审计通过
+ leanchecker --fresh replay 成功
    = formal_verified
```

`formal_verified` 只表示**一个显式冻结的 Lean theorem 在显式冻结的形式环境中通过了配置的验证 gate**。自然语言研究命题到 Lean theorem 的语义映射仍然是必须审查的研究假设。

---

# v0.7 新增能力

## 1. Frozen Lean/Lake Project

v0.6 已经冻结 theorem signature、imports、trusted preamble 与 Lean toolchain；v0.7 进一步冻结**赋予这些 imports 实际语义的整个 Lean/Lake 工程环境**。

`LeanProjectLock` schema v2 内容寻址：

- `lean-toolchain`；
- `lakefile.toml` / `lakefile.lean`；
- `lake-manifest.json`；
- resolved dependency records；
- trusted `.lean` source files；
- explicitly tracked extra files；
- `.lake/packages` 中真正会进入验证环境的 dependency source bytes。

因此即使 manifest revision 没变，只要本地 dependency checkout 被修改，project fingerprint 也会变化并阻止认证。

生成锁：

```bash
research-evolve lean-project-lock \
  --project-root path/to/lean-project \
  --source-root . \
  --output project-lock.json
```

依赖项目进入认证路径时必须具备 `lake-manifest.json` 与可验证的 dependency cache。`--allow-unlocked-dependencies` 仅用于开发阶段观察/导出；严格 `verify_project()`、premise indexing 和 ProjectLeanKernel 不会接受这种环境作为证书来源。

## 2. Formal Premise Retrieval

v0.7 新增：

- `Premise`
- `PremiseIndex`
- `PremiseSelector`
- `PremiseSelection`
- `formal_retrieval.sqlite3`

构建索引：

```bash
research-evolve premise-index \
  --project-root path/to/lean-project \
  --project-lock project-lock.json \
  --output premise-index.json
```

预览检索：

```bash
research-evolve premise-search \
  --premise-index premise-index.json \
  --query "rank nonnegative kernel dimension" \
  --module MyProject.Algebra \
  --limit 12
```

默认 selector 是**确定性的 lexical baseline**。它只从冻结工程中的 theorem / lemma / def / abbrev 建索引，并且默认只返回已经存在于 `FormalizationSpec.imports` 中的模块，不允许 retrieval 偷偷扩展 trusted import surface。

Formal contract 必须同时绑定：

```text
project_fingerprint
premise_index_fingerprint
```

运行时任一不一致都会 fail closed。

## 3. ProjectLeanKernel

v0.7 project mode 的认证链：

```text
re-capture project lock
        ↓
verify project fingerprint
        ↓
verify Lean toolchain
        ↓
materialize frozen project copy
        ↓
lake build
        ↓
compile ResearchEvolveGenerated.lean
        ↓
Lean diagnostics gate
        ↓
#print axioms audit
        ↓
lake env leanchecker --fresh ResearchEvolveGenerated
        ↓
formal_verified
```

Project audit 写入：

```text
formal_project.sqlite3
```

记录：

- project fingerprint；
- Lake build command / exit code / stdout / stderr；
- generated Lean compile command / result；
- `leanchecker --fresh` command / result；
- gate reason；
- final pass/fail。

## 4. Project copy 不等于 OS sandbox

v0.7 会在每次验证中创建一次性冻结工程副本，但这只是**逻辑与工程环境隔离**。

对于真正不可信、可能主动攻击宿主机的 Formalizer/Repairer，仍应在容器、VM 或远程 worker 中运行，并限制：

- filesystem；
- network；
- CPU / memory / processes；
- provider secrets；
- private evaluator source。

---

# 快速开始

## 安装

```bash
python -m pip install -e ".[dev]"
```

查看 CLI：

```bash
research-evolve --help
```

## 最小演化搜索

```bash
research-evolve run \
  --spec examples/target42/spec.json \
  --evaluator examples/target42/evaluator.py \
  --seeds examples/target42/seeds.json \
  --workspace .researchevolve/target42 \
  --islands 4
```

查看候选：

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

# v0.7 reference integration

仓库包含无外部 API key、无第三方 Lean dependency 的：

```text
examples/formal_project42/
├── project-lock.json
├── premise-index.json
├── spec.json
├── seeds.json
├── formalizer.py
├── repairer.py
└── lean_project/
    ├── lean-toolchain
    ├── lakefile.toml
    ├── FormalProject42.lean
    └── FormalProject42/
        └── Premises.lean
```

它故意让 Formalizer 第一次输出错误 proof，然后要求 Repairer **必须实际看到检索出的 `FormalProject42.distance_nonnegative`** 才能修复。

完整 project-mode formalize：

```bash
research-evolve formalize \
  --workspace .researchevolve/formal-project42 \
  --formalizer-command "python examples/formal_project42/formalizer.py" \
  --repairer-command "python examples/formal_project42/repairer.py" \
  --project-root examples/formal_project42/lean_project \
  --project-lock examples/formal_project42/project-lock.json \
  --project-build-target FormalProject42 \
  --premise-index examples/formal_project42/premise-index.json \
  --premise-limit 4 \
  --max-repairs 2
```

注意：`formalize` 需要当前 workspace 已经具有前序 research/conjecture/proof lineage。CI 中的 `formal_project42` 会实际执行 v0.4 → v0.5 → v0.7 全链。

查看检索与工程验证记录：

```bash
research-evolve premise-selections \
  --workspace .researchevolve/formal-project42

research-evolve project-checks \
  --workspace .researchevolve/formal-project42
```

---

# Workspace

一个完整的 ResearchEvolve workspace 可以包含：

```text
.researchevolve/run/
├── candidates.sqlite3
├── ideas.sqlite3
├── conjectures.sqlite3
├── proofs.sqlite3
├── formal.sqlite3
├── formal_retrieval.sqlite3
├── formal_project.sqlite3
├── research_graph.sqlite3
├── checkpoint.json
├── manifest.json
├── proof_manifest.json
├── formal_manifest.json
├── summary.json
├── proof_summary.json
├── formal_summary.json
└── formal_sources/
```

---

# 版本路线

| 版本 | 核心能力 |
|---|---|
| v0.1 | ResearchSpec / Evaluator / Candidate DB / MAP-Elites / islands / mutation / Research Graph |
| v0.2 | evaluator cascade / Pareto / novelty / checkpoint-resume / reproducibility / qLDPC domain pack |
| v0.3 | Semantic Explorer / IdeaGenome / semantic proposal loop |
| v0.4 | empirical observations / conjectures / executable Predicate / counterexample search |
| v0.5 | ProofSpec / lemma DAG / Prover / independent NL Verifier |
| v0.6 | frozen formal contract / Lean kernel / repair loop / axiom audit |
| **v0.7** | **frozen Lean/Lake project / dependency-byte lock / premise retrieval / Lake build / `leanchecker --fresh`** |

详细设计：

- `docs/V0.3.md`
- `docs/V0.4.md`
- `docs/V0.5.md`
- `docs/V0.6.md`
- `docs/V0.7.md`
- `SECURITY.md`

---

# qLDPC 接入方向

ResearchEvolve 的搜索层已经支持 domain pack / evaluator cascade / structured mutation；形式化层现在也具备冻结项目、premise retrieval 和 fresh replay，因此后续可以逐步把 qLDPC 中适合形式化的局部不变量接入，例如：

```text
CSS commutation
circulant / polynomial identities
lifted-product construction invariants
rank / dimension lemmas
parameter legality conditions
```

搜索、经验验证与形式化证明应保持分层：昂贵的距离估计、BP-OSD、MILP 等继续属于 evaluator；适合符号化的代数不变量再进入 Lean formal contract。

---

# 测试

```bash
pytest -q
```

GitHub Actions 在 Python 3.10 / 3.12 上执行从早期 smoke tests 到 v0.7 的完整链，并安装固定 Lean toolchain 运行真实 Lean/Lake 集成验证。

`formal_verified` 的最终边界始终是：**生成模型不能自己授予这个状态。**
