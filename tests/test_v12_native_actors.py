from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from research_evolve.plugin.native_actors import (
    CodexNativeActorRunner,
    actor_output_schema,
    actor_policy,
    project_actor_request,
)
from research_evolve.plugin.service import PluginError, PluginService


def _proof_request(role: str) -> dict[str, Any]:
    request: dict[str, Any] = {
        "schema_version": 1,
        "action": "prove" if role == "prover" else "adversarial_verify",
        "context": {
            "problem": "Prove P",
            "generation": 3,
            "proof_spec": {"statement": "P"},
            "conjecture": {"statement": "P"},
            "evidence_candidates": [],
            "observations": [],
            "prior_proofs": [{"private": "must not cross the boundary"}],
            "metadata": {
                "domain": "algebra",
                "truth_policy": "do not overclaim",
                "api_key": "sk-secret",
                "custom_secret": "hidden",
            },
        },
        "proof_spec": {"statement": "P"},
        "proof_plan": {"strategy": "direct", "lemmas": []},
        "proof_artifact": {"final_argument": "argument"},
        "response_contract": {},
        "integrity_policy": "preserve target",
        "verification_policy": "attack the proof",
        "provider_token": "top-level-secret",
    }
    return request


def test_context_projection_is_role_scoped_and_redacts_secrets() -> None:
    projected = project_actor_request("proof-reviewer", _proof_request("proof-reviewer"))
    assert "provider_token" not in projected
    assert "integrity_policy" not in projected
    assert "verification_policy" in projected
    assert "prior_proofs" not in projected["context"]
    assert projected["context"]["metadata"] == {
        "domain": "algebra",
        "truth_policy": "do not overclaim",
    }
    assert projected["actor_isolation"]["policy_fingerprint"] == actor_policy("proof-reviewer")["fingerprint"]


def test_actor_policy_fingerprint_is_role_bound_and_stable() -> None:
    first = actor_policy("prover")
    assert first == actor_policy("prover")
    assert first["fingerprint"] != actor_policy("proof-reviewer")["fingerprint"]
    assert first["sandbox"] == "read-only"
    assert first["session_persistence"] == "ephemeral"
    with pytest.raises(ValueError, match="unsupported"):
        actor_policy("status-granter")


def test_output_schema_captures_reviewer_gate_fields() -> None:
    schema = actor_output_schema("proof-reviewer")
    assert schema["properties"]["decision"]["enum"] == ["verified", "rejected", "inconclusive"]
    assert schema["properties"]["confidence"]["maximum"] == 1
    assert set(schema["required"]) == {"decision", "issues", "confidence", "adversarial_notes"}


def test_native_runner_uses_fresh_locked_down_process_and_audits(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def fake_invoke(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(
            json.dumps({"decision": "verified", "issues": [], "confidence": 0.9, "adversarial_notes": "checked"}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with PluginService(tmp_path) as service:
        service.project_create(request_id="project", project_id="native-project", objective="Prove P")
        projected = project_actor_request("proof-reviewer", _proof_request("proof-reviewer"), backend="codex-native")
        task = service.state.create_actor_task("native-project", "proof-reviewer", projected)
        result = CodexNativeActorRunner(
            service.control, service.state, codex_executable=sys.executable, invoke=fake_invoke
        ).run(task, 30)
        runs = service.actor_run_list(project_id="native-project")["runs"]

    assert result.response["decision"] == "verified"
    assert len(runs) == 1 and runs[0]["status"] == "completed"
    assert runs[0]["session_id"].startswith("actor-session-")
    assert runs[0]["output_fingerprint"]
    command = calls[0]["command"]
    assert command[1] == "exec"
    assert "--ephemeral" in command and "--ignore-user-config" in command and "--ignore-rules" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--ask-for-approval") + 1] == "never"
    assert "mcp_servers={}" in command and "plugins={}" in command
    assert "tools.web_search=false" in command and "agents.enabled=false" in command
    assert "features.shell_tool=false" in command and "apps._default.enabled=false" in command
    assert "memories.use_memories=false" in command and 'history.persistence="none"' in command
    assert Path(calls[0]["cwd"]).name == runs[0]["session_id"]
    assert "OPENAI_API_KEY" not in calls[0]["env"]
    assert "sk-secret" not in calls[0]["input"]
    assert "prior_proofs" not in calls[0]["input"]


def test_native_runner_fake_cli_end_to_end(tmp_path: Path) -> None:
    fake = tmp_path / "fake_codex.py"
    fake.write_text(
        "import json, pathlib, sys\n"
        "args=sys.argv[1:]\n"
        "assert args[0]=='exec' and '--ephemeral' in args and '--output-schema' in args\n"
        "json.load(open(args[args.index('--output-schema')+1], encoding='utf-8'))\n"
        "prompt=sys.stdin.read(); assert 'prior_proofs' not in prompt\n"
        "out=pathlib.Path(args[args.index('--output-last-message')+1])\n"
        "out.write_text(json.dumps({'lemma_arguments': {}, 'final_argument': 'P', 'assumptions_used': []}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    with PluginService(tmp_path / "root") as service:
        service.project_create(request_id="project", project_id="fake-cli", objective="Prove P")
        task = service.state.create_actor_task(
            "fake-cli", "prover",
            project_actor_request("prover", _proof_request("prover"), backend="codex-native"),
        )
        result = CodexNativeActorRunner(
            service.control, service.state, codex_executable=[sys.executable, str(fake)]
        ).run(task, 30)
        run = service.actor_run_list(project_id="fake-cli")["runs"][0]
    assert result.response["final_argument"] == "P"
    assert run["status"] == "completed" and run["output_fingerprint"]


def test_native_runner_fail_closed_and_records_failure(tmp_path: Path) -> None:
    def failed(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 7, stdout="", stderr="auth failed")

    with PluginService(tmp_path) as service:
        service.project_create(request_id="project", project_id="failed-project", objective="Prove P")
        task = service.state.create_actor_task(
            "failed-project", "prover", project_actor_request("prover", _proof_request("prover"), backend="codex-native")
        )
        with pytest.raises(RuntimeError, match="auth failed"):
            CodexNativeActorRunner(
                service.control, service.state, codex_executable=sys.executable, invoke=failed
            ).run(task, 30)
        runs = service.actor_run_list(project_id="failed-project")["runs"]
    assert runs[0]["status"] == "failed" and runs[0]["exit_code"] == 7
    assert runs[0]["output_fingerprint"] is None


def test_missing_codex_is_audited_before_fail_closed(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        service.project_create(request_id="project", project_id="missing-codex", objective="Prove P")
        task = service.state.create_actor_task(
            "missing-codex", "prover", project_actor_request("prover", _proof_request("prover"), backend="codex-native")
        )
        with pytest.raises(RuntimeError, match="not found"):
            CodexNativeActorRunner(
                service.control, service.state, codex_executable="research-evolve-no-such-codex"
            ).run(task, 30)
        runs = service.actor_run_list(project_id="missing-codex")["runs"]
    assert runs[0]["status"] == "failed" and "not found" in runs[0]["diagnostics"]


def test_manual_task_creation_stores_only_projected_context(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        service.project_create(request_id="project", project_id="manual-project", objective="Prove P")
        task = service.actor_task_create(
            request_id="task", project_id="manual-project", role="proof-reviewer",
            request=_proof_request("proof-reviewer"),
        )["task"]
    encoded = json.dumps(task["request"])
    assert "sk-secret" not in encoded and "prior_proofs" not in encoded
    assert task["request"]["actor_isolation"]["backend"] == "manual"


def test_native_and_manual_tasks_cannot_share_content_replay(tmp_path: Path) -> None:
    request = _proof_request("prover")
    with PluginService(tmp_path) as service:
        service.project_create(request_id="project", project_id="backend-project", objective="Prove P")
        manual = service.state.create_actor_task(
            "backend-project", "prover", project_actor_request("prover", request, backend="manual")
        )
        native = service.state.create_actor_task(
            "backend-project", "prover", project_actor_request("prover", request, backend="codex-native")
        )
    assert manual["id"] != native["id"]
    assert manual["input_hash"] != native["input_hash"]


def test_native_runner_rejects_manual_or_stale_isolation_envelopes(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        service.project_create(request_id="project", project_id="stale-policy", objective="Prove P")
        manual = service.state.create_actor_task(
            "stale-policy", "prover",
            project_actor_request("prover", _proof_request("prover"), backend="manual"),
        )
        runner = CodexNativeActorRunner(service.control, service.state, codex_executable=sys.executable)
        with pytest.raises(ValueError, match="codex-native"):
            runner.run(manual, 30)
        stale_request = project_actor_request("prover", _proof_request("prover"), backend="codex-native")
        stale_request["actor_isolation"]["policy_fingerprint"] = "stale"
        stale = service.state.create_actor_task("stale-policy", "prover", stale_request)
        with pytest.raises(ValueError, match="stale"):
            runner.run(stale, 30)


def test_mcp_submission_cannot_forge_native_actor_output(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        service.project_create(request_id="project", project_id="native-forgery", objective="Prove P")
        task = service.state.create_actor_task(
            "native-forgery", "prover",
            project_actor_request("prover", _proof_request("prover"), backend="codex-native"),
        )
        response = {"lemma_arguments": {}, "final_argument": "fake", "assumptions_used": []}
        with pytest.raises(PluginError, match="isolated runner"):
            service.actor_output_submit(
                request_id="forge", task_id=task["id"], expected_revision=1, response=response
            )
        with pytest.raises(PluginError, match="isolated runner"):
            service.actor_task_reject(
                request_id="forge-reject", task_id=task["id"], expected_revision=1, reason="fake"
            )


def test_pipeline_defaults_native_and_manual_backend_is_explicit(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        created = service.project_create(request_id="project", project_id="pipeline", objective="test")
        project = tmp_path / created["project"]["relative_path"]
        (project / "workspace").mkdir()
        (project / "workspace/conjectures.sqlite3").write_bytes(b"")
        started: list[list[str]] = []
        service._start_job = lambda **kwargs: started.append(kwargs["command"]) or {"status": "running"}  # type: ignore[method-assign]
        service.proof_start(request_id="native", project_id="pipeline")
        assert "--backend codex-native" in " ".join(started[-1])
        service.proof_start(request_id="manual", project_id="pipeline", actor_backend="manual")
        assert "--backend manual" in " ".join(started[-1])
        with pytest.raises(PluginError, match="actor_backend"):
            service.proof_start(request_id="bad", project_id="pipeline", actor_backend="shell")


def test_whole_formalization_uses_distinct_formalizer_and_repairer_roles(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        created = service.project_create(request_id="project", project_id="formal-pipeline", objective="test")
        project = tmp_path / created["project"]["relative_path"]
        (project / "lean").mkdir(exist_ok=True)
        for name in ("lock.json", "index.json", "registry.json"):
            (project / name).write_text("{}", encoding="utf-8")
        started: list[list[str]] = []
        service._start_job = lambda **kwargs: started.append(kwargs["command"]) or {"status": "running"}  # type: ignore[method-assign]
        service.formalize_start(
            request_id="formal", project_id="formal-pipeline", mode="whole",
            project_root="lean", project_lock="lock.json", premise_index="index.json",
            semantic_registry="registry.json",
        )
    command = " ".join(started[0])
    assert "--role formalizer" in command
    assert "--formal-repair-command" in command and "--role formal-repairer" in command
