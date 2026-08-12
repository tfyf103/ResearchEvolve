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

v0.6 executes generated Lean theorem bodies, so it adds a stronger trust boundary.

### Frozen formal contract

A Formalizer or Repairer is untrusted. It does not choose the theorem target.

`ResearchSpec.metadata.formal_contracts` freezes:

- exact natural-language conjecture statement;
- exact normalized v0.4 machine `Predicate`;
- Lean backend;
- Lean toolchain;
- imports;
- trusted preamble definitions;
- theorem name;
- complete theorem signature;
- axiom policy.

Matching both the statement and normalized predicate prevents two conjectures with identical prose but different executable semantics from reusing the same Lean theorem contract.

The theorem signature must not contain `:=`; only the theorem body is generated.

### Trusted preamble

Project definitions that affect theorem meaning belong in the frozen `preamble` or pinned imports. They are trusted research inputs.

Do not let a model define the object it is supposed to prove facts about. For example, if the theorem concerns `distanceTo42`, its Lean definition should be frozen before the Formalizer runs.

### Generated top-level helpers disabled

v0.6+ rejects non-empty model-supplied `helper_source`. The model may use local `have` / `show` inside the theorem body, but cannot inject arbitrary global declarations before the target theorem.

This reduces attacks or accidental semantic changes through notation, namespaces, instances, syntax, macros, or name resolution.

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

### Toolchain pinning

Every formal contract freezes a Lean toolchain.

For version detection and theorem compilation, v0.6 creates a temporary Lean working directory containing the frozen:

```text
lean-toolchain
```

An Elan-managed `lean` proxy therefore selects the requested project toolchain even though the generated source lives outside the repository root. ResearchEvolve additionally checks `lean --version` against the frozen contract version.

A missing/mismatched environment yields `environment_error`, not theorem success or theorem rejection.

### Kernel and axiom gate

Successful elaboration alone is not enough. ResearchEvolve appends:

```lean
#print axioms theoremName
```

and audits the result.

Default allowed Lean axioms:

```text
propext
Classical.choice
Quot.sound
```

Dependencies such as the following block `formal_verified` under the default policy:

```text
sorryAx
Lean.trustCompiler
Custom.someAxiom
```

The v0.6 success condition is:

```text
exact frozen statement + machine predicate mapping
+ frozen imports/preamble/theorem signature
+ accepted generated theorem body
+ requested Lean toolchain selected
+ Lean version matches
+ Lean exits successfully
+ no Lean error diagnostics
+ #print axioms is parseable
+ no disallowed axiom dependency
= formal_verified
```

`formal_verified` is therefore a statement about a **specific frozen Lean theorem**. It does not automatically prove that the informal research statement was translated correctly; the formal contract itself remains an auditable modeling assumption.

## v0.7 Frozen Lean/Lake Project Boundary

v0.7 recognizes that freezing an import name is not enough. If `import MyProject.Definitions` resolves to different local source or dependency revisions, the same theorem signature can acquire different semantics.

### `LeanProjectLock`

Project-mode verification therefore freezes a content-addressed project lock containing:

- `lean-toolchain` contents;
- `lakefile.toml` or `lakefile.lean` hash;
- `lake-manifest.json` hash when present;
- normalized resolved dependency package records from the Lake manifest;
- hashes of tracked trusted `.lean` sources;
- hashes of explicitly tracked extra project files;
- source-root configuration.

The resulting `project_fingerprint` is copied into the frozen formal contract metadata. `ProjectLeanKernel` requires an exact match before certification.

If a tracked source, Lake configuration, toolchain, or locked dependency changes, project verification fails closed until the research contract is intentionally updated.

Dependency-bearing projects are expected to have `lake-manifest.json`. `lean-project-lock --allow-unlocked-dependencies` exists only for explicit development use and should not be used for high-assurance or reproducible research runs.

### Symlinks and project copying

The lock refuses symlinked tracked files. Verification copies the exact frozen project into a temporary working directory instead of editing the trusted source project in place.

For projects with resolved dependencies, v0.7 copies `.lake/packages` into the temporary project. If the frozen project declares dependency records but the dependency cache is missing, project verification fails rather than silently downloading a different dependency graph.

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
verify project fingerprint
→ verify toolchain
→ materialize frozen project
→ lake build
→ compile generated theorem inside Lake environment
→ #print axioms audit
→ lake env leanchecker --fresh ResearchEvolveGenerated
→ formal_verified
```

From Lean 4.28 onward, the former external `lean4checker` functionality is shipped with Lean as `leanchecker`. v0.7 therefore uses the toolchain-provided `leanchecker --fresh` rather than depending on the now-deprecated external checker repository.

`--fresh` is a replay/environment-integrity check, not a second independently implemented proof assistant. It strengthens validation against environment contamination or cached declaration replacement, but does not eliminate the need for OS isolation when the proof generator itself is hostile.

`formal_project.sqlite3` records the build, compile, and checker commands, exit codes, outputs, project fingerprint, and gate reason. These are audit records. The active certificate remains the `FormalizationSpec`/`KernelResult` lineage and can be invalidated when upstream research state becomes stale.

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
- immutable or content-addressed dependency caches where practical.

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
