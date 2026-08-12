# ResearchEvolve Security Model

ResearchEvolve separates **idea/conjecture/proof generation** from **truth evaluation and verification**. The default CLI is a research/development harness, not a hardened multi-tenant sandbox.

## Evaluator

`HiddenEvaluator` / `EvaluatorCascade` use separate JSON-speaking processes. This creates a protocol boundary, not OS isolation. A process running as the same user may still inspect files, environment variables, or other local processes.

## Explorer and Conjecturer

`CommandExplorer` and `CommandConjecturer` receive summarized research state through JSON. ResearchEvolve does not intentionally send evaluator source to them, but local subprocesses are not security sandboxes.

## v0.4 Predicate DSL

Conjecturers submit a restricted `Predicate`, not Python code. Predicates may reference `score`, `payload`, `metrics`, or `behavior` and use only `lt/le/gt/ge/eq/ne` comparisons. ResearchEvolve does not use `eval` or `exec` to evaluate them.

Finite tests produce empirical statuses only. They never produce a formal theorem status.

## v0.5 Natural-language Proof Pipeline

The v0.5 pipeline freezes a `ProofSpec`, validates an acyclic lemma graph, checks proof coverage and assumptions, and separates Prover from Verifier implementation identity.

Its strongest status is:

```text
verified_natural_language
```

That is not Lean/Coq/Isabelle/kernel verification.

## v0.6 Formalizer / Lean Gate

A Formalizer or Repairer is untrusted and does not choose the theorem target.

`ResearchSpec.metadata.formal_contracts` freezes:

- exact natural-language conjecture statement;
- exact normalized v0.4 machine `Predicate`;
- Lean backend and toolchain;
- imports;
- trusted preamble definitions;
- theorem name and complete theorem signature;
- axiom policy.

Matching both statement and normalized predicate prevents identical prose with different executable semantics from reusing the same Lean theorem contract. The theorem signature must not contain `:=`; generated actors provide only the theorem body.

### Generated top-level helpers disabled

v0.6+ rejects non-empty model-supplied `helper_source`. The model may use local `have` / `show` inside the theorem body, but cannot inject arbitrary global declarations before the target theorem.

### Conservative source gate

Before Lean runs, ResearchEvolve rejects generated source containing:

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

This is defense in depth, not a proof of sandbox safety.

### Toolchain, kernel, and axiom gate

Every formal contract freezes a Lean toolchain. ResearchEvolve checks the actual Lean version and appends:

```lean
#print axioms theoremName
```

Successful elaboration alone is not enough. The default allowed Lean axioms are:

```text
propext
Classical.choice
Quot.sound
```

Dependencies such as `sorryAx`, `Lean.trustCompiler`, or custom axioms block `formal_verified` under the default policy.

`formal_verified` is a statement about a **specific frozen Lean theorem**. It does not automatically prove that the informal research statement was translated correctly; the formal contract remains an auditable modeling assumption.

## v0.7 Frozen Lean/Lake Project Boundary

v0.7 recognizes that freezing an import name is insufficient. If `import MyProject.Definitions` resolves to different source or dependency bytes, the same theorem signature can acquire different semantics.

### `LeanProjectLock` schema v2

Project-mode verification uses a content-addressed lock containing:

- `lean-toolchain` contents;
- `lakefile.toml` or `lakefile.lean` hash;
- `lake-manifest.json` hash when present;
- normalized resolved dependency package records from the Lake manifest;
- hashes of tracked trusted `.lean` sources;
- hashes of explicitly tracked extra project files;
- source-root configuration;
- **hashes of the actual dependency source files under `.lake/packages` that will be copied into verification**.

The resulting `project_fingerprint` is copied into the frozen formal contract metadata. `ProjectLeanKernel` requires an exact match before certification.

This closes an important gap: a dependency checkout modified locally while its manifest revision remains unchanged no longer counts as the same formal environment, because the dependency source byte hashes change.

Tracked project and dependency-cache files reject symlinks. Nested `.git`, `.lake`, and `__pycache__` directories are excluded from the dependency source hash set so build/VCS caches are not treated as trusted source inputs.

### Dependency resolution is strict on the certification path

A dependency-bearing project used for premise indexing or formal certification must have:

- `lake-manifest.json`;
- resolved dependency records;
- a populated `.lake/packages` cache;
- content-addressed dependency source files matching the lock.

`lean-project-lock --allow-unlocked-dependencies` exists only as a development/inspection escape hatch. Such a lock cannot pass strict `verify_project()`, cannot build a trusted premise index, and cannot enter `ProjectLeanKernel` certification. This prevents the formal verification path from silently resolving or downloading an unfrozen dependency graph.

### Exact project materialization

Verification copies the frozen project into a temporary working directory instead of editing the trusted source tree.

For resolved dependencies, v0.7 copies **only the files explicitly present in `dependency_cache_files`**. It does not blindly copy the entire `.lake/packages` directory. If a locked dependency file is missing or its source project no longer reproduces the project fingerprint, verification fails closed.

This **does not make the temporary directory an OS security sandbox**. A hostile process running as the same user can still attempt filesystem, process, environment, or network access outside that directory. Use a container, VM, remote worker, or another OS-level sandbox for adversarial Formalizers.

### Frozen premise retrieval

`PremiseIndex` is bound to the exact `project_fingerprint`, and retrieval mode additionally requires the exact `premise_index_fingerprint` frozen in the formal contract.

Premise selection is advisory only. Retrieved declarations cannot:

- add imports;
- change the theorem signature;
- modify trusted definitions;
- change the project lock;
- grant `formal_verified`.

The v0.7 built-in source scanner intentionally indexes only tracked local declarations and is a conservative retrieval layer, not a complete Lean elaborator. It may miss declarations or namespace details in complex source layouts. Missing retrieval lowers proof-generation quality; it must never weaken kernel verification.

Retrieval is restricted to modules already listed in the frozen `FormalizationSpec.imports`. This is intentionally conservative: transitive imports may contain useful premises that are not surfaced by the default selector.

### Lake build + Lean + fresh replay

Project mode requires this chain:

```text
strict project lock re-validation
→ exact project_fingerprint
→ exact toolchain
→ materialize frozen project + dependency bytes
→ lake build
→ compile generated theorem inside Lake environment
→ Lean diagnostics gate
→ #print axioms audit
→ lake env leanchecker --fresh ResearchEvolveGenerated
→ formal_verified
```

`leanchecker --fresh` is a replay/environment-integrity check, not a second independently implemented proof assistant. It strengthens validation against environment contamination or cached declaration replacement, but it does not eliminate the need for OS isolation when the proof generator itself is hostile.

`formal_project.sqlite3` records build, compile, and checker commands, exit codes, outputs, project fingerprint, and gate reason. `formal_retrieval.sqlite3` records premise-selection decisions. These are audit records; the active certificate remains the formal lineage and can be invalidated when upstream research state becomes stale.

## Recommended production separation

```text
Explorer / Conjecturer
        │
        ▼
Research Orchestrator
        │
        ├──────────────► Private Evaluator / Grader
        │
        ▼
Natural-language Prover
        │
        ▼
Independent NL Verifier
        │
        ▼
Formalizer / Repairer
        │   theorem body only
        ▼
Isolated Frozen Lake Project Worker
        │   build + kernel + axiom audit
        ▼
leanchecker --fresh replay
        │
        ▼
Research state
```

For autonomous/untrusted Formalizers, isolate Lean in a container, VM, or remote worker with:

- no model/provider secrets;
- no private evaluator source;
- read-only trusted formal contracts/libraries where practical;
- restricted filesystem access;
- CPU, memory, process, and wall-time limits;
- disabled or restricted network access;
- pinned Lean and dependencies;
- immutable/content-addressed dependency caches where practical.

The local subprocess boundary is not sufficient containment for hostile code.

## Secrets

Do not put API keys or other secrets in:

- candidate payloads;
- ResearchSpec metadata;
- Explorer / Conjecturer metadata;
- proof artifacts / reviews;
- formal contracts;
- generated Lean source;
- command-line arguments.

Use environment variables or a secret manager for provider credentials. ResearchEvolve persists metadata in SQLite journals and manifests; assume persisted metadata can be inspected.

## Stale-result invalidation

A later counterexample can invalidate a previously accepted natural-language proof. v0.6+ propagates this into formal records:

```text
FormalizationSpec → invalidated
FormalArtifact     → invalidated
KernelResult       → invalidated
```

Historical kernel, project-check, and premise-selection information is retained for audit, but it no longer acts as the active formal certificate for the current research lineage.

This is necessary because the Lean kernel may have correctly proved a frozen theorem while the upstream mapping between that theorem and the current research claim has become stale.

## Reporting

If you discover a security issue, avoid publishing sensitive exploit details in a public issue until the repository owner has had a chance to review the report.
