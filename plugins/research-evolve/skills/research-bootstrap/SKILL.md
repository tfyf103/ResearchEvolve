---
name: research-bootstrap
description: Create or validate an auditable ResearchEvolve project from a mathematical objective. Use when starting a project, designing a ResearchSpec, evaluator, seed set, constraints, objectives, or budget, or checking whether a project is ready to run.
---

# Research Bootstrap

Turn the user's objective into an explicit project without silently inventing the mathematical semantics.

## Workflow

1. Run `research_doctor` before the first project operation.
2. Call `research_project_create` with a stable lowercase project id, a fresh request id, and the exact objective.
3. Inspect the generated `research.json`, `seeds.json`, and `evaluator.py`.
4. Ask for input only when evaluator semantics, admissible candidates, or optimization direction cannot be inferred safely.
5. Implement a deterministic evaluator and bounded seeds inside the registered project.
6. Call `research_project_validate`; resolve every error and disclose warnings.
7. Report the project id, frozen choices, remaining assumptions, and recommended next skill.

## Trust Boundary

Treat the generated files as a draft until validation passes. Never claim that scaffold creation proves, empirically supports, or formalizes anything. Never pass absolute paths or paths outside the plugin root.

