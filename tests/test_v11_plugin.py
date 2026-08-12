from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from research_evolve import __version__
from research_evolve.plugin.service import PluginError, PluginService


def test_scaffold_is_idempotent_and_validates_placeholder(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        first = service.project_create(request_id="request-1", project_id="group-theory", objective="Study finite groups")
        again = service.project_create(request_id="request-1", project_id="group-theory", objective="Study finite groups")
        assert first == again
        assert first["status"] == "created"
        assert service.project_validate(project_id="group-theory")["status"] == "valid"
        assert "placeholder" in service.project_validate(project_id="group-theory")["warnings"][0]
        with pytest.raises(PluginError, match="different input"):
            service.project_create(request_id="request-1", project_id="other", objective="Other")
        assert not (tmp_path / "research-projects/other").exists()
        assert [item["id"] for item in service.project_list()["projects"]] == ["group-theory"]


def test_path_confinement_rejects_absolute_parent_and_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-research-evolve"
    outside.mkdir(exist_ok=True)
    with PluginService(tmp_path) as service:
        with pytest.raises(PluginError, match="absolute"):
            service.project_create(request_id="a", project_id="absolute", objective="x", directory=str(outside.resolve()))
        with pytest.raises(PluginError, match="escapes"):
            service.project_create(request_id="b", project_id="parent", objective="x", directory="../escape")
        link = tmp_path / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks are unavailable")
        with pytest.raises(PluginError, match="escapes"):
            service.project_create(request_id="c", project_id="linked", objective="x", directory="linked/project")


def test_actor_schema_revision_and_content_replay(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        service.project_create(request_id="project", project_id="proof-project", objective="Prove a theorem")
        request = {"action": "adversarial_verify", "response_contract": {"decision": "verified | rejected | inconclusive"}}
        task = service.actor_task_create(request_id="task-create", project_id="proof-project", role="proof-reviewer", request=request)["task"]
        duplicate = service.actor_task_create(request_id="task-create-2", project_id="proof-project", role="proof-reviewer", request=request)["task"]
        assert duplicate["id"] == task["id"]
        with pytest.raises(PluginError, match="field 'issues'"):
            service.actor_output_submit(request_id="bad", task_id=task["id"], expected_revision=1, response={"decision": "verified"})
        response = {"decision": "verified", "issues": [], "confidence": 0.9, "adversarial_notes": "checked"}
        submitted = service.actor_output_submit(request_id="submit", task_id=task["id"], expected_revision=1, response=response)
        assert submitted["task"]["status"] == "submitted"
        assert submitted["task"]["revision"] == 2
        assert service.actor_output_submit(request_id="submit", task_id=task["id"], expected_revision=1, response=response) == submitted
        with pytest.raises(PluginError, match="revision conflict|already submitted"):
            service.actor_task_reject(request_id="reject", task_id=task["id"], expected_revision=1, reason="stale")


def test_artifact_queries_do_not_create_workspace_when_absent(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        result = service.project_create(request_id="project", project_id="empty-project", objective="Inspect nothing")
        project = tmp_path / result["project"]["relative_path"]
        assert service.artifact_list(project_id="empty-project", kind="conjectures") == {"kind": "conjectures", "items": []}
        assert not (project / "workspace").exists()
        with pytest.raises(PluginError, match="unsupported artifact"):
            service.artifact_list(project_id="empty-project", kind="secrets")


def test_project_relative_certificate_paths_cannot_cross_projects(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        service.project_create(request_id="one", project_id="project-one", objective="one")
        service.project_create(request_id="two", project_id="project-two", objective="two")
        with pytest.raises(PluginError, match="registered project"):
            service.semantic_registry_validate(project_id="project-one", registry="../project-two/registry.json")
        with pytest.raises(PluginError, match="registered project"):
            service.certificate_verify(project_id="project-one", certificate="../project-two/certificate")


def test_actor_task_requires_explicit_response_contract(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        service.project_create(request_id="project", project_id="actor-project", objective="test")
        with pytest.raises(PluginError, match="response_contract"):
            service.actor_task_create(request_id="actor", project_id="actor-project", role="explorer", request={"action": "explore"})


def test_actor_bridge_round_trip(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        service.project_create(request_id="project", project_id="bridge-project", objective="test")
    command = [sys.executable, "-m", "research_evolve.plugin.actor_bridge", "--root", str(tmp_path), "--project-id", "bridge-project", "--role", "explorer", "--timeout", "10"]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdin is not None
    process.stdin.write(json.dumps({"action": "propose", "response_contract": {"proposals": []}}))
    process.stdin.close()
    task = None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with PluginService(tmp_path) as service:
            tasks = service.actor_task_list(project_id="bridge-project")["tasks"]
            if tasks:
                task = tasks[0]
                service.actor_output_submit(request_id="submit", task_id=task["id"], expected_revision=1, response={"proposals": []})
                break
        time.sleep(0.05)
    assert task is not None
    assert process.stdout is not None and process.stderr is not None
    stdout = process.stdout.read()
    stderr = process.stderr.read()
    assert process.wait(timeout=5) == 0, stderr
    assert json.loads(stdout) == {"proposals": []}


def test_fixed_discovery_job_runs_without_arbitrary_command(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        created = service.project_create(request_id="project", project_id="run-project", objective="maximize x")
        project = tmp_path / created["project"]["relative_path"]
        spec = json.loads((project / "research.json").read_text(encoding="utf-8"))
        spec["budget"].update({"generations": 1, "population_size": 2})
        (project / "research.json").write_text(json.dumps(spec), encoding="utf-8")
        (project / "seeds.json").write_text('[{"x": 0}]', encoding="utf-8")
        (project / "evaluator.py").write_text(
            "import json, sys\nc=json.load(sys.stdin)\njson.dump({'valid': True, 'score': c['x'], 'metrics': {}, 'behavior': {'representation': 'test'}, 'diagnostics': {}}, sys.stdout)\n",
            encoding="utf-8",
        )
        job = service.run_start(request_id="run", project_id="run-project", islands=1)["job"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with PluginService(tmp_path) as service:
            status = service.run_status(job_id=job["id"])
        if status["job"]["status"] != "running":
            break
        time.sleep(0.1)
    assert status["job"]["status"] == "completed", status
    assert (project / "workspace/manifest.json").is_file()


def test_doctor_version_and_manifests(tmp_path: Path) -> None:
    assert __version__ == "1.1.0"
    with PluginService(tmp_path) as service:
        result = service.doctor()
        assert result["status"] == "pass"
        assert next(item for item in result["checks"] if item["name"] == "research-evolve")["detail"] == "1.1.0"
    repo = Path(__file__).parents[1]
    plugin = json.loads((repo / "plugins/research-evolve/.codex-plugin/plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads((repo / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    mcp = json.loads((repo / "plugins/research-evolve/.mcp.json").read_text(encoding="utf-8"))
    assert plugin["version"] == "1.1.0" and plugin["skills"] == "./skills/"
    assert marketplace["plugins"][0]["source"]["path"] == "./plugins/research-evolve"
    assert mcp["mcpServers"]["research-evolve"]["command"] == "research-evolve-mcp"


def test_mcp_server_exposes_typed_tools(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from research_evolve.plugin.mcp_server import create_server

    server = create_server(tmp_path)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
    assert {"research_doctor", "research_project_create", "research_run_start", "research_proof_start", "research_formalize_start", "research_actor_output_submit", "research_certificate_verify"} <= set(tools)
    schema = tools["research_project_create"].parameters
    assert set(schema["required"]) == {"request_id", "project_id", "objective"}
    assert schema["properties"]["directory"]["anyOf"][0]["type"] == "string"


def test_proof_and_formal_jobs_are_fixed_and_project_confined(tmp_path: Path) -> None:
    with PluginService(tmp_path) as service:
        created = service.project_create(request_id="project", project_id="pipeline-project", objective="test")
        project = tmp_path / created["project"]["relative_path"]
        (project / "workspace").mkdir()
        (project / "workspace/conjectures.sqlite3").write_bytes(b"")
        started: list[tuple[str, list[str]]] = []
        service._start_job = lambda **kwargs: started.append((kwargs["phase"], kwargs["command"])) or {"status": "running", "job": {"id": "job-test"}}  # type: ignore[method-assign]
        proof = service.proof_start(request_id="proof", project_id="pipeline-project")
        assert proof["status"] == "running"
        phase, command = started[-1]
        assert phase == "proof" and "research_evolve.plugin.proof_reviewer_bridge" in " ".join(command)
        for name in ("lean", "lock.json", "index.json", "registry.json"):
            target = project / name
            target.mkdir(exist_ok=True) if name == "lean" else target.write_text("{}", encoding="utf-8")
        formal = service.formalize_start(request_id="formal", project_id="pipeline-project", mode="interactive", project_root="lean", project_lock="lock.json", premise_index="index.json", semantic_registry="registry.json", build_targets=["MyProject.Main"])
        assert formal["status"] == "running"
        assert started[-1][0] == "formalize" and "--tactic-generator-command" not in started[-1][1]
        with pytest.raises(PluginError, match="registered project"):
            service.formalize_start(request_id="escape", project_id="pipeline-project", mode="whole", project_root="../outside", project_lock="lock.json", premise_index="index.json", semantic_registry="registry.json")


def test_actor_commands_round_trip_paths_with_spaces(tmp_path: Path) -> None:
    root = tmp_path / "root with spaces"
    with PluginService(root) as service:
        command = service._actor_command("research_evolve.plugin.prover_bridge", "space-project", 12)
    parsed = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        parsed = [item[1:-1] if item.startswith('"') and item.endswith('"') else item for item in parsed]
    assert str(root.resolve()) in parsed
    assert parsed[-2:] == ["--timeout", "12"]
