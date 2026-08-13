---
name: research-proof
description: Plan, write, and independently review ResearchEvolve natural-language proofs. Use when developing an empirically supported conjecture into a proof, decomposing lemmas, reviewing gaps, or diagnosing verified_natural_language status.
---

# Research Proof

Develop the exact frozen ProofSpec with independent adversarial review.

## Workflow

1. Inspect conjectures, counterexamples, proof specs, and available evidence with `research_artifact_list`.
2. Select only a gate-eligible conjecture; do not turn finite evidence into a universal proof.
3. Call `research_proof_start` with the default `actor_backend=codex-native`.
4. Let Core invoke Planner, Prover, and Reviewer sequentially in three fresh ephemeral Codex processes. Do not manually relay hidden reasoning between them.
5. Inspect `research_actor_run_list` and require distinct session ids plus completed audit records for the three roles.
6. Inspect proof-plans, proof-artifacts, and proof-reviews. Report `verified_natural_language` only when the Core gate records it.

## Review Standard

Attack quantifier changes, hidden assumptions, circular dependencies, missing cases, misuse of experiments, and failure to prove the exact final statement. A reviewer response is evidence for the Core gate, not authority to set status directly.

Use `actor_backend=manual` only to diagnose or recover a failed native invocation. Manual exchange does not satisfy the v1.2 process-isolation claim and must be disclosed.

