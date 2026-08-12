---
name: research-proof
description: Plan, write, and independently review ResearchEvolve natural-language proofs. Use when developing an empirically supported conjecture into a proof, decomposing lemmas, reviewing gaps, or diagnosing verified_natural_language status.
---

# Research Proof

Develop the exact frozen ProofSpec with independent adversarial review.

## Workflow

1. Inspect conjectures, counterexamples, proof specs, and available evidence with `research_artifact_list`.
2. Select only a gate-eligible conjecture; do not turn finite evidence into a universal proof.
3. Create a `proof-planner` actor task and submit a lemma DAG that preserves the statement and assumptions.
4. Create a separate `prover` task from the frozen plan. Declare every assumption used.
5. Create a `proof-reviewer` task with the frozen spec, plan, and proof artifact. Do not expose hidden prover reasoning or let the prover self-review.
6. Submit only schema-valid outputs with the task's current revision.
7. Inspect proof-plans, proof-artifacts, and proof-reviews. Report `verified_natural_language` only when the Core gate records it.

## Review Standard

Attack quantifier changes, hidden assumptions, circular dependencies, missing cases, misuse of experiments, and failure to prove the exact final statement. A reviewer response is evidence for the Core gate, not authority to set status directly.

