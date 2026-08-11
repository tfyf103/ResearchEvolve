# ResearchEvolve Security Model

ResearchEvolve separates **idea generation** from **truth evaluation**, but the default local CLI is a research/development harness rather than a hardened multi-tenant sandbox.

## Trust boundaries

### Evaluator

`HiddenEvaluator` / `EvaluatorCascade` execute evaluator scripts in separate Python processes and communicate over JSON stdin/stdout.

This provides:

- a narrow protocol boundary;
- independent evaluator failure handling;
- a deployment contract that can later be moved behind a grader service.

It does **not** by itself prevent another process on the same machine/user account from reading evaluator files or inspecting the local environment.

### Explorer

`CommandExplorer` launches an external process and only sends summarized research context over JSON stdin. ResearchEvolve does not intentionally include evaluator source code or evaluator paths in that request.

However, an untrusted Explorer running under the same OS user may still be able to inspect files, processes, environment variables, or other machine resources outside the JSON protocol.

Therefore the default `CommandExplorer` is **not a security sandbox**.

## Recommended production deployment

For untrusted or autonomous agents, use at least three isolated roles:

```text
┌──────────────────────┐
│ Agent / Explorer     │
│ no evaluator access  │
└──────────┬───────────┘
           │ structured proposal / candidate
           ▼
┌──────────────────────┐
│ Research Orchestrator│
│ archive + scheduling │
└──────────┬───────────┘
           │ candidate only
           ▼
┌──────────────────────┐
│ Private Grader       │
│ evaluator mounted    │
│ agent cannot read it │
└──────────┬───────────┘
           │ EvaluationResult only
           ▼
      Research state
```

Use containers, VMs, remote workers, or another isolation mechanism appropriate to the threat model. The private grader should mount evaluator code read-only and expose only the narrow candidate/result protocol.

## Secrets

Do not place provider API keys inside candidate payloads, ResearchSpec metadata, Explorer proposals, or command-line arguments.

Prefer environment variables or a secret manager for provider credentials. `CommandExplorer.name` stores only a hash-based identity in `manifest.json`; raw command arguments are not written there by ResearchEvolve.

Explorer-provided `metadata` is persisted in `ideas.sqlite3`, so wrappers should never include API keys or other secrets in proposal metadata.

## Generated code

v0.3 semantic proposals use restricted top-level JSON patch/crossover operations and do not execute arbitrary model-generated code through the semantic path.

Future code-generation features should execute untrusted generated programs in a separate sandbox with explicit CPU, memory, wall-time, filesystem, and network limits.

## Reporting

If you discover a security issue, avoid publishing sensitive exploit details in a public issue until the repository owner has had a chance to review the report.
