from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(prog="research-evolve-job-runner")
    parser.add_argument("--phase", required=True, choices=("discovery", "proof", "formalize"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--islands", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--explorer-command")
    parser.add_argument("--conjecturer-command")
    parser.add_argument("--planner-command")
    parser.add_argument("--prover-command")
    parser.add_argument("--reviewer-command")
    parser.add_argument("--formal-actor-command")
    parser.add_argument("--formal-mode", choices=("interactive", "whole"))
    parser.add_argument("--project-root")
    parser.add_argument("--project-lock")
    parser.add_argument("--premise-index")
    parser.add_argument("--semantic-registry")
    parser.add_argument("--build-target", action="append")
    parser.add_argument("--timeout-seconds", type=float, default=300)
    args = parser.parse_args()
    project = Path(args.project).resolve()
    result = Path(args.result).resolve()
    if project not in result.parents:
        parser.error("result marker must be inside the project")
    workspace = str(project / "workspace")
    if args.phase == "discovery":
        command = [sys.executable, "-m", "research_evolve.cli", "run", "--spec", str(project / "research.json"), "--evaluator", str(project / "evaluator.py"), "--seeds", str(project / "seeds.json"), "--workspace", workspace, "--islands", str(args.islands)]
        if args.resume:
            command.append("--resume")
        if args.explorer_command:
            command.extend(["--explorer-command", args.explorer_command])
        if args.conjecturer_command:
            command.extend(["--conjecturer-command", args.conjecturer_command])
    elif args.phase == "proof":
        if not all((args.planner_command, args.prover_command, args.reviewer_command)):
            parser.error("proof requires planner, prover, and reviewer commands")
        command = [sys.executable, "-m", "research_evolve.cli", "prove", "--workspace", workspace, "--planner-command", args.planner_command, "--prover-command", args.prover_command, "--verifier-command", args.reviewer_command, "--timeout", str(args.timeout_seconds)]
    else:
        required = (args.formal_actor_command, args.formal_mode, args.project_root, args.project_lock, args.premise_index, args.semantic_registry)
        if not all(required):
            parser.error("formalize requires an actor mode, frozen project, lock, index, and registry")
        actor_flag = "--tactic-generator-command" if args.formal_mode == "interactive" else "--formalizer-command"
        command = [sys.executable, "-m", "research_evolve.cli", "formalize", "--workspace", workspace, actor_flag, args.formal_actor_command, "--project-root", args.project_root, "--project-lock", args.project_lock, "--premise-index", args.premise_index, "--semantic-registry", args.semantic_registry, "--actor-timeout", str(args.timeout_seconds)]
        for target in args.build_target or []:
            command.extend(["--project-build-target", target])
    try:
        completed = subprocess.run(command, cwd=project, check=False)
        payload = {"exit_code": completed.returncode}
    except Exception as exc:
        payload = {"exit_code": 1, "error": str(exc)}
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return int(payload["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
