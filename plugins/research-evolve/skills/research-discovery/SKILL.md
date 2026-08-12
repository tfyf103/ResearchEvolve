---
name: research-discovery
description: Run, resume, and inspect reproducible ResearchEvolve discovery. Use for candidate search, Explorer proposals, conjecture generation, counterexample search, Pareto inspection, checkpoints, run logs, or empirical-support questions.
---

# Research Discovery

Run bounded discovery and distinguish observations from trusted empirical status.

## Workflow

1. Call `research_project_validate`; do not start a run with errors or a placeholder evaluator.
2. Review the ResearchSpec budget, objectives, constraints, seeds, and enabled actors.
3. Call `research_run_start` with a unique request id. Use `resume=true` only for the same recorded workspace.
4. Poll `research_run_status` without starting a duplicate run. Use cancellation only when requested or necessary to stop unsafe resource use.
5. Inspect typed data with `research_artifact_list`: candidates, observations, conjectures, counterexamples, and research-graph.
6. State statuses exactly as stored. A plausible pattern is not `empirically_supported`; the deterministic counterexample/evidence gate decides that status.
7. Report budget use, best evidence, counterexamples, failure diagnostics, and the next bounded experiment.

## Actor Exchange

Use `research_actor_task_create`, `research_actor_task_get`, and `research_actor_output_submit` only for supported roles. Preserve the supplied context and response contract. Reject a task explicitly if it requires hidden data or an unsupported claim.

