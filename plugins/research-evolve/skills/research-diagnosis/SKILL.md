---
name: research-diagnosis
description: Diagnose ResearchEvolve projects, runs, trust gates, and portable certificates. Use when a status is missing, a run failed, Lean rejected a proof, artifacts disagree, or the user wants certificate export or fresh verification.
---

# Research Diagnosis

Trace the artifact lineage first and explain the earliest failed gate.

## Workflow

1. Run `research_doctor` and `research_project_status`.
2. Inspect the relevant typed artifacts and Research Graph with `research_artifact_list`.
3. Inspect `research_actor_run_list` for actor-backed phases; verify role, distinct ephemeral session id, policy/context/prompt/output fingerprints, and terminal status without requesting hidden prompts.
4. Find the first missing, rejected, stale, or fingerprint-mismatched predecessor.
5. Separate Codex auth/process/schema failures from research failures, and semantic failures from proof failures.
6. For export, call `research_certificate_export` with project-relative registry, lock, and index paths.
7. Call `research_certificate_verify`; supply the frozen Lean project only when fresh replay is intended.
8. Report the certificate fingerprint, whether verification was hash-only or fresh Lean replay, failed checks, and a minimal recovery action.

## Status Explanations

Never infer a higher status from downstream-looking files. Quote the stored gate status and supporting artifact ids. If a model output exists but has not passed its deterministic gate, describe it as submitted or proposed.

