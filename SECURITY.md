# ResearchEvolve Security Model

ResearchEvolve separates **idea/conjecture generation** from **truth evaluation**, but the default local CLI is a research/development harness rather than a hardened multi-tenant sandbox.

## Trust boundaries

### Evaluator

`HiddenEvaluator` / `EvaluatorCascade` execute evaluator scripts in separate Python processes and communicate over JSON stdin/stdout.

This provides a narrow protocol boundary, independent evaluator failure handling, and a deployment contract that can later be moved behind a grader service. It does **not** by itself prevent another process running as the same OS user from reading evaluator files or inspecting the local environment.

### Explorer and Conjecturer

`CommandExplorer` and `CommandConjecturer` launch external processes and receive summarized research state over JSON stdin. ResearchEvolve does not intentionally include evaluator source code in those requests.

However, an untrusted model wrapper running under the same OS user may still be able to inspect files, processes, environment variables, or other machine resources outside the JSON protocol. The default command adapters are therefore **protocol boundaries, not security sandboxes**.

### v0.4 predicate DSL

Conjecturers do not submit Python expressions. They submit a restricted `Predicate` composed of:

- a `ValueRef` to `score`, `payload`, `metrics`, or `behavior`;
- one comparison operator: `lt`, `le`, `gt`, `ge`, `eq`, or `ne`;
- a JSON constant or another `ValueRef`.

ResearchEvolve does not call `eval`, `exec`, or dynamically import code to interpret conjecture predicates. Unsupported or unresolvable comparisons are treated as non-testable rather than executed.

A finite set of successful tests only yields `empirically_supported`; the v0.4 loop never upgrades empirical evidence to `proved`.

## Recommended production deployment

For untrusted or autonomous agents, isolate at least these roles:

```text
┌──────────────────────────┐
│ Explorer / Conjecturer   │
│ no evaluator access      │
└────────────┬─────────────┘
             │ structured proposals / predicates
             ▼
┌──────────────────────────┐
│ Research Orchestrator    │
│ archive + scheduling     │
└────────────┬─────────────┘
             │ candidate only
             ▼
┌──────────────────────────┐
│ Private Grader           │
│ evaluator mounted        │
│ agent cannot read it     │
└────────────┬─────────────┘
             │ EvaluationResult only
             ▼
        Research state
```

Use containers, VMs, remote workers, or another isolation mechanism appropriate to the threat model. The private grader should mount evaluator code read-only and expose only the narrow candidate/result protocol.

## Secrets

Do not place provider API keys inside candidate payloads, ResearchSpec metadata, Explorer proposals, Conjecturer metadata, or command-line arguments.

Prefer environment variables or a secret manager for provider credentials. Command adapter identities in `manifest.json` are hash-based; wrappers should still avoid embedding secrets in argv. Explorer metadata is persisted in `ideas.sqlite3`, and Conjecturer metadata is persisted in `conjectures.sqlite3`, so neither should contain secrets.

## Generated code

v0.3 semantic proposals use restricted top-level JSON patch/crossover operations. v0.4 conjectures use the restricted predicate DSL above. Neither path executes arbitrary model-generated code directly.

Future code-generation features should execute untrusted generated programs in a separate sandbox with explicit CPU, memory, wall-time, filesystem, and network limits.

## Reporting

If you discover a security issue, avoid publishing sensitive exploit details in a public issue until the repository owner has had a chance to review the report.
