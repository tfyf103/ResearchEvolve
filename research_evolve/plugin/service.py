from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from .. import __version__
from ..candidates import CandidateDB
from ..certificate import ResearchCertificate
from ..conjectures import ConjectureMemory
from ..formal import FormalMemory
from ..graph import ResearchGraph
from ..proofs import ProofMemory
from ..semantic_bridge import SemanticAuditMemory, SemanticRegistry
from ..spec import ResearchSpec
from .actors import ROLE_REQUIRED, validate_actor_response
from .native_actors import actor_policy, project_actor_request
from .state import PluginState, stable_hash


class PluginError(ValueError):
    pass


_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


class PluginService:
    """Workspace-confined service used by the CLI and stdio MCP facade."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.control = self.root / ".researchevolve" / "plugin"
        self.control.mkdir(parents=True, exist_ok=True)
        self.state = PluginState(self.control / "state.sqlite3")

    def close(self) -> None:
        self.state.close()

    def __enter__(self) -> "PluginService":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _inside(self, relative: str | Path, *, must_exist: bool = False) -> Path:
        raw = Path(relative)
        if raw.is_absolute():
            raise PluginError("absolute paths are not accepted by the plugin")
        candidate = (self.root / raw).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise PluginError("path escapes the configured plugin root") from exc
        if must_exist and not candidate.exists():
            raise PluginError(f"path does not exist: {relative}")
        return candidate

    def _in_project(self, project: Path, relative: str | Path, *, must_exist: bool = False) -> Path:
        raw = Path(relative)
        if raw.is_absolute():
            raise PluginError("project file paths must be relative")
        candidate = (project / raw).resolve(strict=False)
        try:
            candidate.relative_to(project)
        except ValueError as exc:
            raise PluginError("path escapes the registered project") from exc
        if must_exist and not candidate.exists():
            raise PluginError(f"project path does not exist: {relative}")
        return candidate

    @staticmethod
    def _id(value: str, label: str = "id") -> str:
        if not _ID.fullmatch(value):
            raise PluginError(f"{label} must match {_ID.pattern}")
        return value

    @staticmethod
    def _limit(value: int) -> int:
        if not 1 <= value <= 200:
            raise PluginError("limit must be between 1 and 200")
        return value

    def _project(self, project_id: str) -> tuple[dict[str, Any], Path]:
        try:
            record = self.state.get_project(self._id(project_id, "project_id"))
        except ValueError as exc:
            raise PluginError(str(exc)) from exc
        path = self._inside(record["relative_path"], must_exist=True)
        return record, path

    def _idempotent(self, request_id: str, operation: str, payload: dict[str, Any], action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        if not request_id or len(request_id) > 128:
            raise PluginError("request_id must be 1..128 characters")
        try:
            replay = self.state.reserve_request(request_id, operation, payload)
            if replay is not None:
                return replay
            try:
                return self.state.finish_request(request_id, action())
            except Exception:
                self.state.abandon_request(request_id)
                raise
        except ValueError as exc:
            raise PluginError(str(exc)) from exc

    def project_create(self, *, request_id: str, project_id: str, objective: str, directory: str | None = None) -> dict[str, Any]:
        project_id = self._id(project_id, "project_id")
        if not objective.strip() or len(objective) > 10000:
            raise PluginError("objective must contain 1..10000 characters")
        relative = directory or f"research-projects/{project_id}"
        payload = {"project_id": project_id, "objective": objective, "directory": relative}

        def create() -> dict[str, Any]:
            target = self._inside(relative)
            if target.exists() and any(target.iterdir()):
                raise PluginError(f"refusing to scaffold non-empty directory: {relative}")
            target.mkdir(parents=True, exist_ok=True)
            (target / "actors").mkdir(exist_ok=True)
            (target / "lean").mkdir(exist_ok=True)
            spec = {
                "name": project_id, "problem": objective.strip(), "domain": "generic", "mode": "metric_search",
                "objectives": [{"name": "quality", "direction": "maximize", "weight": 1.0}],
                "constraints": [], "behavior_dimensions": ["representation"],
                "budget": {"generations": 20, "population_size": 32, "evaluator_timeout_seconds": 30, "seed": 0},
                "search": {"novelty_probability": 0.25, "novelty_k": 5, "migration_interval": 5, "migrants_per_island": 1, "checkpoint_interval": 1},
                "explorer": {"enabled": False, "interval": 1, "proposals_per_interval": 2, "context_candidates": 8, "feedback_items": 12, "timeout_seconds": 60},
                "conjecture": {"enabled": False, "interval": 1, "observations_per_interval": 12, "conjectures_per_interval": 2, "context_candidates": 24, "context_conjectures": 12, "counterexample_trials": 8, "min_evidence": 3, "timeout_seconds": 60},
                "metadata": {"created_by": "research-evolve-codex-plugin-v1.2"},
            }
            seeds = [{"value": 0, "representation": "seed"}]
            evaluator = """from __future__ import annotations

import json
import sys


def main() -> int:
    candidate = json.load(sys.stdin)
    # Replace this placeholder with deterministic domain semantics.
    json.dump({"valid": False, "score": None, "diagnostics": {"reason": "placeholder evaluator"}}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
            (target / "research.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            (target / "seeds.json").write_text(json.dumps(seeds, indent=2) + "\n", encoding="utf-8")
            (target / "evaluator.py").write_text(evaluator, encoding="utf-8")
            (target / ".gitignore").write_text("workspace/\ncertificate/\n", encoding="utf-8")
            try:
                record = self.state.create_project(project_id, str(target.relative_to(self.root)), {"objective": objective.strip()})
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise
            return {"status": "created", "project": record, "files": ["research.json", "seeds.json", "evaluator.py", ".gitignore"]}

        return self._idempotent(request_id, "project_create", payload, create)

    def project_validate(self, *, project_id: str) -> dict[str, Any]:
        record, path = self._project(project_id)
        errors: list[str] = []
        warnings: list[str] = []
        try:
            ResearchSpec.from_dict(json.loads((path / "research.json").read_text(encoding="utf-8")))
        except Exception as exc:
            errors.append(f"research.json: {exc}")
        try:
            seeds = json.loads((path / "seeds.json").read_text(encoding="utf-8"))
            if not isinstance(seeds, list) or not all(isinstance(item, dict) for item in seeds):
                errors.append("seeds.json must be a list of objects")
        except Exception as exc:
            errors.append(f"seeds.json: {exc}")
        evaluator = path / "evaluator.py"
        if not evaluator.is_file():
            errors.append("evaluator.py is missing")
        elif "placeholder evaluator" in evaluator.read_text(encoding="utf-8"):
            warnings.append("evaluator.py is still the scaffold placeholder")
        return {"status": "valid" if not errors else "invalid", "project": record, "errors": errors, "warnings": warnings}

    def project_list(self) -> dict[str, Any]:
        return {"projects": self.state.list_projects()}

    def project_status(self, *, project_id: str) -> dict[str, Any]:
        record, path = self._project(project_id)
        workspace = path / "workspace"
        summary: dict[str, Any] = {}
        for name in ("summary.json", "proof_summary.json", "formal_summary.json", "manifest.json", "proof_manifest.json", "formal_manifest.json"):
            artifact = workspace / name
            if artifact.is_file():
                try:
                    summary[name] = json.loads(artifact.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    summary[name] = {"error": "invalid JSON"}
        return {"project": record, "validation": self.project_validate(project_id=project_id), "workspace_exists": workspace.is_dir(), "summaries": summary}

    @staticmethod
    def _actor_backend(value: str) -> str:
        if value not in {"codex-native", "manual"}:
            raise PluginError("actor_backend must be codex-native or manual")
        return value

    def run_start(self, *, request_id: str, project_id: str, resume: bool = False, islands: int = 4, actor_backend: str = "codex-native") -> dict[str, Any]:
        if not 1 <= islands <= 64:
            raise PluginError("islands must be between 1 and 64")
        _, path = self._project(project_id)
        validation = self.project_validate(project_id=project_id)
        if validation["errors"] or validation["warnings"]:
            raise PluginError("project is not runnable: " + "; ".join([*validation["errors"], *validation["warnings"]]))
        actor_backend = self._actor_backend(actor_backend)
        payload = {"project_id": project_id, "resume": resume, "islands": islands, "actor_backend": actor_backend}

        def start() -> dict[str, Any]:
            spec = ResearchSpec.from_dict(json.loads((path / "research.json").read_text(encoding="utf-8")))
            command = [sys.executable, "-m", "research_evolve.plugin.job_runner", "--phase", "discovery", "--project", str(path), "--islands", str(islands)]
            bridge = lambda role, timeout: self._command_text([sys.executable, "-m", "research_evolve.plugin.actor_bridge", "--root", str(self.root), "--project-id", project_id, "--role", role, "--timeout", str(timeout), "--backend", actor_backend])
            if spec.explorer.enabled:
                command.extend(["--explorer-command", bridge("explorer", spec.explorer.timeout_seconds)])
            if spec.conjecture.enabled:
                command.extend(["--conjecturer-command", bridge("conjecturer", spec.conjecture.timeout_seconds)])
            if resume:
                command.append("--resume")
            return self._start_job(request_id=request_id, project_id=project_id, phase="discovery", command=command)

        return self._idempotent(request_id, "run_start", payload, start)

    @staticmethod
    def _command_text(arguments: Sequence[str]) -> str:
        if os.name == "nt":
            return subprocess.list2cmdline(list(arguments))
        return shlex.join(arguments)

    def _actor_command(self, module: str, project_id: str, timeout: float, actor_backend: str = "codex-native") -> str:
        identity_file = Path(__file__).with_name(module.rsplit(".", 1)[-1] + ".py")
        return self._command_text([sys.executable, "-m", module, "--root", str(self.root), "--project-id", project_id, "--backend", self._actor_backend(actor_backend), "--identity-file", str(identity_file), "--timeout", str(timeout)])

    def _start_job(self, *, request_id: str, project_id: str, phase: str, command: list[str]) -> dict[str, Any]:
        _, project = self._project(project_id)
        active = self.state.running_job(project_id, phase)
        if active is not None:
            active = self.run_status(job_id=active["id"])["job"]
            if active["status"] == "running":
                raise PluginError(f"project already has a running {phase} job: {active['id']}")
        digest = stable_hash(request_id)[:32]
        marker = project / ".researchevolve-plugin" / f"{digest}.json"
        command.extend(["--result", str(marker)])
        log_dir = self.control / "jobs"
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"{digest}.log"
        log = log_path.open("w", encoding="utf-8")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(command, cwd=project, stdout=log, stderr=subprocess.STDOUT, text=True, creationflags=flags)
        log.close()
        job = self.state.create_job(project_id, phase, process.pid, stable_hash(command), json.dumps({"log": str(log_path.relative_to(self.root)), "result": str(marker.relative_to(self.root))}, sort_keys=True))
        return {"status": "running", "job": job}

    def proof_start(self, *, request_id: str, project_id: str, timeout_seconds: float = 300, actor_backend: str = "codex-native") -> dict[str, Any]:
        if timeout_seconds <= 0 or timeout_seconds > 3600:
            raise PluginError("timeout_seconds must be between 0 and 3600")
        _, project = self._project(project_id)
        if not (project / "workspace/conjectures.sqlite3").is_file():
            raise PluginError("discovery/conjecture workspace is required before proof")
        actor_backend = self._actor_backend(actor_backend)
        payload = {"project_id": project_id, "timeout_seconds": timeout_seconds, "actor_backend": actor_backend}
        def start() -> dict[str, Any]:
            command = [sys.executable, "-m", "research_evolve.plugin.job_runner", "--phase", "proof", "--project", str(project),
                "--timeout-seconds", str(timeout_seconds),
                "--planner-command", self._actor_command("research_evolve.plugin.proof_planner_bridge", project_id, timeout_seconds, actor_backend),
                "--prover-command", self._actor_command("research_evolve.plugin.prover_bridge", project_id, timeout_seconds, actor_backend),
                "--reviewer-command", self._actor_command("research_evolve.plugin.proof_reviewer_bridge", project_id, timeout_seconds, actor_backend)]
            return self._start_job(request_id=request_id, project_id=project_id, phase="proof", command=command)
        return self._idempotent(request_id, "proof_start", payload, start)

    def formalize_start(self, *, request_id: str, project_id: str, mode: str, project_root: str, project_lock: str, premise_index: str, semantic_registry: str, build_targets: list[str] | None = None, timeout_seconds: float = 300, actor_backend: str = "codex-native") -> dict[str, Any]:
        if mode not in {"interactive", "whole"}:
            raise PluginError("mode must be interactive or whole")
        if timeout_seconds <= 0 or timeout_seconds > 3600:
            raise PluginError("timeout_seconds must be between 0 and 3600")
        _, project = self._project(project_id)
        frozen = {"project_root": self._in_project(project, project_root, must_exist=True), "project_lock": self._in_project(project, project_lock, must_exist=True), "premise_index": self._in_project(project, premise_index, must_exist=True), "semantic_registry": self._in_project(project, semantic_registry, must_exist=True)}
        targets = build_targets or []
        if len(targets) > 20 or any(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", item) is None for item in targets):
            raise PluginError("build targets must be simple dotted identifiers")
        actor_backend = self._actor_backend(actor_backend)
        payload = {"project_id": project_id, "mode": mode, "project_root": project_root, "project_lock": project_lock, "premise_index": premise_index, "semantic_registry": semantic_registry, "build_targets": targets, "timeout_seconds": timeout_seconds, "actor_backend": actor_backend}
        def start() -> dict[str, Any]:
            role = "tactic-generator" if mode == "interactive" else "formalizer"
            actor = self._command_text([sys.executable, "-m", "research_evolve.plugin.actor_bridge", "--root", str(self.root), "--project-id", project_id, "--role", role, "--timeout", str(timeout_seconds), "--backend", actor_backend])
            command = [sys.executable, "-m", "research_evolve.plugin.job_runner", "--phase", "formalize", "--project", str(project), "--formal-mode", mode, "--formal-actor-command", actor, "--project-root", str(frozen["project_root"]), "--project-lock", str(frozen["project_lock"]), "--premise-index", str(frozen["premise_index"]), "--semantic-registry", str(frozen["semantic_registry"])]
            if mode == "whole":
                repair = self._command_text([sys.executable, "-m", "research_evolve.plugin.actor_bridge", "--root", str(self.root), "--project-id", project_id, "--role", "formal-repairer", "--timeout", str(timeout_seconds), "--backend", actor_backend])
                command.extend(["--formal-repair-command", repair])
            command.extend(["--timeout-seconds", str(timeout_seconds)])
            for target in targets:
                command.extend(["--build-target", target])
            return self._start_job(request_id=request_id, project_id=project_id, phase="formalize", command=command)
        return self._idempotent(request_id, "formalize_start", payload, start)

    @staticmethod
    def _pid_running(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    @staticmethod
    def _job_locations(job: dict[str, Any]) -> dict[str, str]:
        try:
            locations = json.loads(job["directory"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise PluginError("job metadata is invalid") from exc
        if not isinstance(locations, dict) or not isinstance(locations.get("log"), str) or not isinstance(locations.get("result"), str):
            raise PluginError("job metadata is invalid")
        return locations

    def run_status(self, *, job_id: str) -> dict[str, Any]:
        try:
            job = self.state.get_job(job_id)
        except ValueError as exc:
            raise PluginError(str(exc)) from exc
        locations = self._job_locations(job)
        marker = self._inside(locations["result"], must_exist=False)
        if job["status"] == "running" and marker.is_file():
            try:
                result = json.loads(marker.read_text(encoding="utf-8"))
                job = self.state.update_job_status(job_id, "completed" if result.get("exit_code") == 0 else "failed")
            except json.JSONDecodeError:
                job = self.state.update_job_status(job_id, "failed")
        elif job["status"] == "running" and not self._pid_running(job["pid"]):
            job = self.state.update_job_status(job_id, "failed")
        log_path = self._inside(locations["log"], must_exist=False)
        tail = ""
        if log_path.is_file():
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-12000:]
        return {"job": job, "log_tail": tail}

    def run_cancel(self, *, request_id: str, job_id: str) -> dict[str, Any]:
        payload = {"job_id": job_id}

        def cancel() -> dict[str, Any]:
            job = self.state.get_job(job_id)
            if job["status"] != "running":
                return {"status": job["status"], "job": job}
            if self._pid_running(job["pid"]):
                if os.name == "nt":
                    completed = subprocess.run(["taskkill", "/PID", str(job["pid"]), "/T"], capture_output=True, text=True, check=False)
                    if completed.returncode != 0 and self._pid_running(job["pid"]):
                        raise PluginError(completed.stderr.strip() or "could not cancel job")
                else:
                    os.kill(int(job["pid"]), signal.SIGTERM)
            return {"status": "cancelled", "job": self.state.update_job_status(job_id, "cancelled")}

        try:
            return self._idempotent(request_id, "run_cancel", payload, cancel)
        except ValueError as exc:
            raise PluginError(str(exc)) from exc

    def artifact_list(self, *, project_id: str, kind: str, limit: int = 20) -> dict[str, Any]:
        _, project = self._project(project_id)
        workspace = project / "workspace"
        limit = self._limit(limit)
        readers: dict[str, Callable[[], Any]] = {
            "candidates": lambda: self._candidate_rows(workspace, limit),
            "observations": lambda: self._conjecture_rows(workspace, "observations", limit),
            "conjectures": lambda: self._conjecture_rows(workspace, "conjectures", limit),
            "counterexamples": lambda: self._conjecture_rows(workspace, "counterexamples", limit),
            "proof-specs": lambda: self._proof_rows(workspace, "specs", limit),
            "proof-plans": lambda: self._proof_rows(workspace, "plans", limit),
            "proof-artifacts": lambda: self._proof_rows(workspace, "artifacts", limit),
            "proof-reviews": lambda: self._proof_rows(workspace, "reviews", limit),
            "formal-specs": lambda: self._formal_rows(workspace, "specs", limit),
            "formal-artifacts": lambda: self._formal_rows(workspace, "artifacts", limit),
            "kernel-runs": lambda: self._formal_rows(workspace, "kernel", limit),
            "semantic-contracts": lambda: self._semantic_rows(workspace, limit),
            "research-graph": lambda: self._graph(workspace),
        }
        if kind not in readers:
            raise PluginError(f"unsupported artifact kind: {kind}")
        if not workspace.exists():
            return {"kind": kind, "items": []}
        return {"kind": kind, "items": readers[kind]()}

    @staticmethod
    def _candidate_rows(workspace: Path, limit: int) -> list[dict[str, Any]]:
        if not (workspace / "candidates.sqlite3").is_file():
            return []
        db = CandidateDB(workspace / "candidates.sqlite3")
        try:
            return [item.to_dict() for item in db.all()[-limit:]]
        finally:
            db.close()

    @staticmethod
    def _conjecture_rows(workspace: Path, kind: str, limit: int) -> list[dict[str, Any]]:
        if not (workspace / "conjectures.sqlite3").is_file():
            return []
        db = ConjectureMemory(workspace / "conjectures.sqlite3")
        try:
            return db.recent_observations(limit) if kind == "observations" else db.recent_conjectures(limit) if kind == "conjectures" else db.list_counterexamples(limit)
        finally:
            db.close()

    @staticmethod
    def _proof_rows(workspace: Path, kind: str, limit: int) -> list[dict[str, Any]]:
        if not (workspace / "proofs.sqlite3").is_file():
            return []
        db = ProofMemory(workspace / "proofs.sqlite3")
        try:
            return {"specs": db.list_specs, "plans": db.list_plans, "artifacts": db.list_artifacts, "reviews": db.list_reviews}[kind](limit)
        finally:
            db.close()

    @staticmethod
    def _formal_rows(workspace: Path, kind: str, limit: int) -> list[dict[str, Any]]:
        if not (workspace / "formal.sqlite3").is_file():
            return []
        db = FormalMemory(workspace / "formal.sqlite3")
        try:
            return {"specs": db.list_specs, "artifacts": db.list_artifacts, "kernel": db.list_kernel_runs}[kind](limit)
        finally:
            db.close()

    @staticmethod
    def _semantic_rows(workspace: Path, limit: int) -> list[dict[str, Any]]:
        if not (workspace / "semantic_contracts.sqlite3").is_file():
            return []
        db = SemanticAuditMemory(workspace / "semantic_contracts.sqlite3")
        try:
            return db.list(limit)
        finally:
            db.close()

    @staticmethod
    def _graph(workspace: Path) -> dict[str, Any]:
        if not (workspace / "research_graph.sqlite3").is_file():
            return {"nodes": [], "edges": []}
        graph = ResearchGraph(workspace / "research_graph.sqlite3")
        try:
            return graph.export()
        finally:
            graph.close()

    def actor_task_create(self, *, request_id: str, project_id: str, role: str, request: dict[str, Any]) -> dict[str, Any]:
        self._project(project_id)
        if role not in ROLE_REQUIRED:
            raise PluginError(f"unsupported actor role: {role}")
        if not isinstance(request, dict) or len(json.dumps(request, ensure_ascii=False).encode("utf-8")) > 1_000_000:
            raise PluginError("actor request must be a JSON object smaller than 1 MB")
        if not isinstance(request.get("response_contract"), dict):
            raise PluginError("actor request requires a response_contract object")
        try:
            projected = project_actor_request(role, request, backend="manual")
        except ValueError as exc:
            raise PluginError(str(exc)) from exc
        payload = {"project_id": project_id, "role": role, "request": projected}
        return self._idempotent(request_id, "actor_task_create", payload, lambda: {"task": self.state.create_actor_task(project_id, role, projected)})

    def actor_task_get(self, *, task_id: str) -> dict[str, Any]:
        try:
            return {"task": self.state.get_actor_task(task_id)}
        except ValueError as exc:
            raise PluginError(str(exc)) from exc

    def actor_task_list(self, *, project_id: str, status: str | None = "pending", limit: int = 20) -> dict[str, Any]:
        self._project(project_id)
        if status not in {None, "pending", "submitted", "rejected"}:
            raise PluginError("invalid actor task status")
        return {"tasks": self.state.list_actor_tasks(project_id, status, self._limit(limit))}

    def actor_policy_get(self, *, role: str) -> dict[str, Any]:
        try:
            return actor_policy(role)
        except ValueError as exc:
            raise PluginError(str(exc)) from exc

    def actor_run_list(self, *, project_id: str, limit: int = 20) -> dict[str, Any]:
        self._project(project_id)
        return {"runs": self.state.list_actor_runs(project_id, self._limit(limit))}

    def actor_output_submit(self, *, request_id: str, task_id: str, expected_revision: int, response: dict[str, Any]) -> dict[str, Any]:
        try:
            task = self.state.get_actor_task(task_id)
            if task["request"].get("actor_isolation", {}).get("backend", "manual") == "codex-native":
                raise PluginError("Codex-native actor tasks accept output only from their isolated runner")
            if len(json.dumps(response, ensure_ascii=False).encode("utf-8")) > 1_000_000:
                raise PluginError("actor response must be smaller than 1 MB")
            validate_actor_response(task["role"], response)
        except ValueError as exc:
            raise PluginError(str(exc)) from exc
        payload = {"task_id": task_id, "expected_revision": expected_revision, "response": response}
        return self._idempotent(request_id, "actor_output_submit", payload, lambda: {"task": self.state.update_actor_task(task_id, expected_revision, response=response)})

    def actor_task_reject(self, *, request_id: str, task_id: str, expected_revision: int, reason: str) -> dict[str, Any]:
        if not reason.strip() or len(reason) > 4000:
            raise PluginError("rejection reason must contain 1..4000 characters")
        try:
            task = self.state.get_actor_task(task_id)
        except ValueError as exc:
            raise PluginError(str(exc)) from exc
        if task["request"].get("actor_isolation", {}).get("backend", "manual") == "codex-native":
            raise PluginError("Codex-native actor tasks can be rejected only by their isolated runner")
        payload = {"task_id": task_id, "expected_revision": expected_revision, "reason": reason}
        return self._idempotent(request_id, "actor_task_reject", payload, lambda: {"task": self.state.update_actor_task(task_id, expected_revision, rejection_reason=reason.strip())})

    def semantic_registry_validate(self, *, project_id: str, registry: str) -> dict[str, Any]:
        _, project = self._project(project_id)
        path = self._in_project(project, registry, must_exist=True)
        try:
            item = SemanticRegistry.read(path)
        except ValueError as exc:
            return {"status": "invalid", "error": str(exc)}
        return {"status": "valid", "fingerprint": item.fingerprint, "project_fingerprint": item.project_fingerprint, "premise_index_fingerprint": item.premise_index_fingerprint, "symbols": len(item.symbols)}

    def certificate_export(self, *, request_id: str, project_id: str, registry: str, project_lock: str, premise_index: str, output: str = "certificate") -> dict[str, Any]:
        _, project = self._project(project_id)
        paths = {name: self._in_project(project, value, must_exist=name != "output") for name, value in {"registry": registry, "project_lock": project_lock, "premise_index": premise_index, "output": output}.items()}
        payload = {"project_id": project_id, "registry": registry, "project_lock": project_lock, "premise_index": premise_index, "output": output}
        def export() -> dict[str, Any]:
            manifest = ResearchCertificate.export(project / "workspace", paths["output"], semantic_registry=paths["registry"], project_lock=paths["project_lock"], premise_index=paths["premise_index"])
            return {"status": "exported", "output": str(paths["output"].relative_to(self.root)), "fingerprint": manifest["fingerprint"]}
        return self._idempotent(request_id, "certificate_export", payload, export)

    def certificate_verify(self, *, project_id: str, certificate: str = "certificate", lean_project: str | None = None, timeout_seconds: float = 300) -> dict[str, Any]:
        _, project = self._project(project_id)
        cert = self._in_project(project, certificate, must_exist=True)
        lean = self._in_project(project, lean_project, must_exist=True) if lean_project else None
        result = ResearchCertificate.verify(cert, project_root=lean, timeout_seconds=timeout_seconds)
        return result.to_dict()

    def doctor(self, *, project_id: str | None = None) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        checks.append({"name": "python", "status": "pass", "detail": sys.version.split()[0]})
        checks.append({"name": "research-evolve", "status": "pass", "detail": __version__})
        probe = self.control / ".write-test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            checks.append({"name": "workspace", "status": "pass", "detail": str(self.root)})
        except OSError as exc:
            checks.append({"name": "workspace", "status": "fail", "detail": str(exc)})
        for command in ("codex", "lean", "lake"):
            executable = shutil.which(command)
            required_for = "Codex-native actors" if command == "codex" else "formalization"
            status = "pass" if executable else "warn"
            detail = executable or f"not found; only required for {required_for}"
            if command == "codex" and executable:
                try:
                    probe = subprocess.run(
                        [executable, "--version"], text=True, capture_output=True,
                        timeout=5, check=False,
                    )
                    if probe.returncode != 0:
                        status = "warn"
                        detail = (probe.stderr or probe.stdout or f"exit={probe.returncode}")[-1000:]
                    else:
                        detail = (probe.stdout or executable).strip()
                except (OSError, subprocess.TimeoutExpired) as exc:
                    status = "warn"
                    detail = f"found but not executable for native actors: {exc}"
            checks.append({"name": command, "status": status, "detail": detail})
        if project_id:
            validation = self.project_validate(project_id=project_id)
            checks.append({"name": "project", "status": "pass" if not validation["errors"] else "fail", "detail": validation})
        return {"status": "pass" if all(item["status"] != "fail" for item in checks) else "fail", "checks": checks}
