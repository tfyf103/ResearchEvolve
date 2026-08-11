# ResearchEvolve Security Model

ResearchEvolve separates **idea/conjecture/proof generation** from **truth evaluation and verification**. The default CLI remains a research/development harness, not a hardened multi-tenant sandbox.

## Trust boundaries

### Evaluator

`HiddenEvaluator` / `EvaluatorCascade` execute evaluator scripts in separate Python processes and communicate over JSON stdin/stdout. This is a narrow protocol boundary, not OS isolation. A process running as the same user may still inspect files, environment variables, processes, or other local resources.

### Explorer and Conjecturer

`CommandExplorer` and `CommandConjecturer` receive summarized research state through JSON. ResearchEvolve does not intentionally send evaluator source code to them, but the subprocess boundary alone does not prevent local filesystem/process inspection.

### v0.4 Predicate DSL

Conjecturers submit a restricted `Predicate`, not Python code. It may reference `score`, `payload`, `metrics`, or `behavior`, and only supports `lt/le/gt/ge/eq/ne` comparisons. ResearchEvolve does not call `eval` or `exec` to evaluate conjectures.

Finite tests may produce `empirically_supported` or `refuted`; they never produce a formal theorem status.

### v0.5 Proof Planner / Prover / Verifier

The natural-language proof pipeline freezes a `ProofSpec`, validates an acyclic lemma plan, checks that every lemma receives an argument, rejects hidden assumptions, and requires the verifier implementation to differ from the prover implementation.

Its strongest status is `verified_natural_language`. That means an independent configured verifier accepted a natural-language proof artifact. It is **not** Lean/Coq/Isabelle/kernel verification.

### v0.6 Formalizer / Lean kernel

v0.6 introduces executable Lean source and therefore adds a stronger trust boundary.

A Formalizer or Repairer is **untrusted**. It may only propose a theorem body (`proof_term`). It cannot choose the target theorem declaration. The following are frozen by `ResearchSpec.metadata.formal_contracts` before the actor runs:

- exact conjecture statement to match;
- Lean backend;
- Lean toolchain;
- imports;
- trusted preamble definitions;
- theorem name;
- complete theorem signature;
- allowed axiom policy.

The model-supplied theorem signature is never accepted. `theorem_signature` must not contain `:=`, so the proof body remains the only generated part.

#### v0.6 source gate

Before Lean executes, `LeanKernel` conservatively rejects model output containing escape hatches or metaprogramming surfaces including:

- `sorry`, `admit`, `axiom`;
- `unsafe`, `extern`, `opaque`;
- `run_tac`, `elab`, `macro`, `syntax`;
- `#eval`, `#run`.

v0.6 also rejects any non-empty model-supplied top-level `helper_source`. Local `have` / `show` steps inside the theorem body remain available. Trusted top-level definitions belong in the frozen `preamble`, not in model output.

This source scan is intentionally conservative. It is a defense-in-depth policy, not a substitute for the Lean kernel.

#### Toolchain pinning

`formal_verified` requires the detected `lean --version` to match the frozen formal contract toolchain. The repository includes a `lean-toolchain` file pinning the integration test environment.

Changing the source research fingerprint, proof manifest, formal contracts, actor identities, or formal policy changes `formal_manifest.json` and prevents silent mixing of incompatible formal runs.

#### Axiom audit

Successful Lean elaboration alone is not enough. ResearchEvolve appends:

```lean
#print axioms theoremName
```

and parses the result. By default v0.6 accepts only Lean's standard logical axioms:

```text
propext
Classical.choice
Quot.sound
```

Dependencies such as `sorryAx`, `Lean.trustCompiler`, or custom axioms prevent `formal_verified` unless a future explicit policy intentionally changes that allowlist.

Therefore the v0.6 success condition is approximately:

```text
frozen theorem target
+ accepted source gate
+ exact pinned Lean version
+ Lean exit code 0
+ no Lean errors
+ parsed #print axioms output
+ no disallowed axioms
= formal_verified
```

`formal_verified` is still relative to the **formal theorem that was frozen**. Mapping an informal research claim to a Lean theorem is a modeling responsibility, which is why `formal_contracts` must be explicit and auditable.

### Formal proof execution is still not an OS sandbox

Lean elaboration and tactics are code execution. Even with v0.6's conservative token gate, the default local process is not a hardened sandbox against a hostile formalizer or malicious dependency/import.

For untrusted autonomous formalization, run Lean in an isolated container/VM/remote worker with:

- no provider secrets;
- no evaluator source;
- read-only trusted libraries/contracts where practical;
- restricted filesystem access;
- CPU, memory, process, and wall-time limits;
- disabled or restricted network access;
- a pinned Lean/toolchain and dependency lock.

Do not treat the local subprocess boundary as sufficient containment.

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
        │   proof term only
        ▼
Isolated Lean Worker
        │   kernel result + axiom audit
        ▼
Research state
```

The Prover and natural-language Verifier should remain independently implemented/configured. The Lean worker should not inherit the model worker's filesystem/secrets by default.

## Secrets

Do not put provider API keys or other secrets in candidate payloads, `ResearchSpec.metadata`, Explorer/Conjecturer metadata, proof artifacts, formal contracts, formal artifacts, command-line arguments, or generated Lean source.

Use environment variables or a secret manager for provider credentials. ResearchEvolve persists metadata in SQLite journals and manifests; assume persisted metadata is inspectable.

## Stale-result invalidation

A later counterexample can invalidate a previously accepted natural-language proof. v0.6 propagates that staleness into formal records: `FormalizationSpec`, generated artifacts, and historical kernel results are marked `invalidated`, while the original historical result is retained for audit.

A historical kernel success does not remain an active theorem certificate when its source research/proof lineage is no longer current.

## Reporting

If you discover a security issue, avoid publishing sensitive exploit details in a public issue until the repository owner has had a chance to review the report.
