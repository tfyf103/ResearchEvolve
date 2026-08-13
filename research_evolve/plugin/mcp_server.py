from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .service import PluginService


def create_server(root: str | Path) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised by the CLI error path
        raise RuntimeError('install ResearchEvolve with the plugin extra: pip install -e ".[plugin]"') from exc

    service = PluginService(root)
    mcp = FastMCP("ResearchEvolve", instructions="Operate the auditable ResearchEvolve core. Trusted statuses are granted only by deterministic gates.")

    @mcp.tool()
    def research_doctor(project_id: str | None = None) -> dict[str, Any]:
        """Check Python, workspace, optional project, and Lean/Lake availability."""
        return service.doctor(project_id=project_id)

    @mcp.tool()
    def research_project_create(request_id: str, project_id: str, objective: str, directory: str | None = None) -> dict[str, Any]:
        """Create an idempotent, workspace-confined ResearchEvolve project scaffold."""
        return service.project_create(request_id=request_id, project_id=project_id, objective=objective, directory=directory)

    @mcp.tool()
    def research_project_list() -> dict[str, Any]:
        """List projects registered in this plugin workspace."""
        return service.project_list()

    @mcp.tool()
    def research_project_validate(project_id: str) -> dict[str, Any]:
        """Validate a registered project's ResearchSpec, seeds, and evaluator scaffold."""
        return service.project_validate(project_id=project_id)

    @mcp.tool()
    def research_project_status(project_id: str) -> dict[str, Any]:
        """Read project validation and available discovery/proof/formal summaries."""
        return service.project_status(project_id=project_id)

    @mcp.tool()
    def research_run_start(request_id: str, project_id: str, resume: bool = False, islands: int = 4, actor_backend: str = "codex-native") -> dict[str, Any]:
        """Start bounded discovery with isolated Codex-native actors by default."""
        return service.run_start(request_id=request_id, project_id=project_id, resume=resume, islands=islands, actor_backend=actor_backend)

    @mcp.tool()
    def research_run_status(job_id: str) -> dict[str, Any]:
        """Read background-job state and a bounded log tail."""
        return service.run_status(job_id=job_id)

    @mcp.tool()
    def research_run_cancel(request_id: str, job_id: str) -> dict[str, Any]:
        """Idempotently cancel a running discovery job."""
        return service.run_cancel(request_id=request_id, job_id=job_id)

    @mcp.tool()
    def research_proof_start(request_id: str, project_id: str, timeout_seconds: float = 300, actor_backend: str = "codex-native") -> dict[str, Any]:
        """Start planner, prover, and reviewer in separate audited Codex sessions."""
        return service.proof_start(request_id=request_id, project_id=project_id, timeout_seconds=timeout_seconds, actor_backend=actor_backend)

    @mcp.tool()
    def research_formalize_start(request_id: str, project_id: str, mode: str, project_root: str, project_lock: str, premise_index: str, semantic_registry: str, build_targets: list[str] | None = None, timeout_seconds: float = 300, actor_backend: str = "codex-native") -> dict[str, Any]:
        """Start certified Lean formalization from project-relative frozen inputs."""
        return service.formalize_start(request_id=request_id, project_id=project_id, mode=mode, project_root=project_root, project_lock=project_lock, premise_index=premise_index, semantic_registry=semantic_registry, build_targets=build_targets, timeout_seconds=timeout_seconds, actor_backend=actor_backend)

    @mcp.tool()
    def research_artifact_list(project_id: str, kind: str, limit: int = 20) -> dict[str, Any]:
        """List typed candidates, conjectures, proofs, Lean results, audits, or graph data."""
        return service.artifact_list(project_id=project_id, kind=kind, limit=limit)

    @mcp.tool()
    def research_actor_task_create(request_id: str, project_id: str, role: str, request: dict[str, Any]) -> dict[str, Any]:
        """Create a content-addressed task for one supported Codex research role."""
        return service.actor_task_create(request_id=request_id, project_id=project_id, role=role, request=request)

    @mcp.tool()
    def research_actor_task_list(project_id: str, status: str | None = "pending", limit: int = 20) -> dict[str, Any]:
        """List actor exchange tasks by project and state."""
        return service.actor_task_list(project_id=project_id, status=status, limit=limit)

    @mcp.tool()
    def research_actor_task_get(task_id: str) -> dict[str, Any]:
        """Read the immutable bounded context and response contract for an actor task."""
        return service.actor_task_get(task_id=task_id)

    @mcp.tool()
    def research_actor_policy(role: str) -> dict[str, Any]:
        """Inspect the frozen context projection and process isolation policy for a role."""
        return service.actor_policy_get(role=role)

    @mcp.tool()
    def research_actor_run_list(project_id: str, limit: int = 20) -> dict[str, Any]:
        """List isolated Codex session fingerprints and terminal audit outcomes."""
        return service.actor_run_list(project_id=project_id, limit=limit)

    @mcp.tool()
    def research_actor_output_submit(request_id: str, task_id: str, expected_revision: int, response: dict[str, Any]) -> dict[str, Any]:
        """Submit a schema-validated actor output using optimistic concurrency."""
        return service.actor_output_submit(request_id=request_id, task_id=task_id, expected_revision=expected_revision, response=response)

    @mcp.tool()
    def research_actor_task_reject(request_id: str, task_id: str, expected_revision: int, reason: str) -> dict[str, Any]:
        """Reject an actor task explicitly without fabricating a model output."""
        return service.actor_task_reject(request_id=request_id, task_id=task_id, expected_revision=expected_revision, reason=reason)

    @mcp.tool()
    def research_semantic_registry_validate(project_id: str, registry: str) -> dict[str, Any]:
        """Validate and fingerprint a project-relative trusted semantic registry."""
        return service.semantic_registry_validate(project_id=project_id, registry=registry)

    @mcp.tool()
    def research_certificate_export(request_id: str, project_id: str, registry: str, project_lock: str, premise_index: str, output: str = "certificate") -> dict[str, Any]:
        """Export an idempotent content-addressed certificate from trusted artifacts."""
        return service.certificate_export(request_id=request_id, project_id=project_id, registry=registry, project_lock=project_lock, premise_index=premise_index, output=output)

    @mcp.tool()
    def research_certificate_verify(project_id: str, certificate: str = "certificate", lean_project: str | None = None, timeout_seconds: float = 300) -> dict[str, Any]:
        """Verify certificate lineage and optionally perform fresh Lean replay."""
        return service.certificate_verify(project_id=project_id, certificate=certificate, lean_project=lean_project, timeout_seconds=timeout_seconds)

    return mcp


def main() -> int:
    parser = argparse.ArgumentParser(prog="research-evolve-mcp")
    parser.add_argument("--root", default=".", help="only this directory and its descendants are visible to the server")
    args = parser.parse_args()
    create_server(args.root).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
