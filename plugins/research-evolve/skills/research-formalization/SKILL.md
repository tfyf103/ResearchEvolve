---
name: research-formalization
description: Audit semantics and formalize eligible ResearchEvolve conjectures with Lean. Use for semantic registries, frozen project locks, premise indexes, Lean tactic generation, kernel diagnostics, axiom audits, or formal_verified questions.
---

# Research Formalization

Preserve the frozen theorem target and let Lean, not Codex, certify proof terms.

## Workflow

1. Inspect the verified natural-language proof and frozen formalization inputs.
2. Call `research_semantic_registry_validate`; compare its project and premise-index fingerprints with the frozen lock and index.
3. Refuse unsupported fields or types. Revise the Predicate or trusted registry instead of guessing a translation.
4. Use a `formalizer`, `formal-repairer`, or `tactic-generator` actor task only for the theorem body/tactics allowed by the frozen contract.
5. Never modify imports, theorem signature, preamble, project lock, or premise index to make a proof pass.
6. Inspect formal-specs, formal-artifacts, kernel-runs, semantic-contracts, and research-graph.
7. Explain diagnostics precisely. Report `formal_verified` only after the project build, Lean kernel, axiom audit, and fresh checker gates pass.

## Failure Modes

Classify failures as unsupported semantics, semantic-audit rejection, proof-search exhaustion, Lean diagnostics, environment failure, or certificate-lineage mismatch. Recommend the smallest trust-preserving correction.

