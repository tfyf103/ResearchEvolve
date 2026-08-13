from __future__ import annotations

import argparse
import json
import sys
import sqlite3
import time

from .actors import ROLE_REQUIRED
from .native_actors import CodexNativeActorRunner, actor_policy, project_actor_request
from .state import stable_hash
from .service import PluginService


def run(fixed_role: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-evolve-actor-bridge")
    parser.add_argument("--root", required=True)
    parser.add_argument("--project-id", required=True)
    if fixed_role is None:
        parser.add_argument("--role", required=True, choices=sorted(ROLE_REQUIRED))
    parser.add_argument("--backend", choices=("codex-native", "manual"), default="codex-native")
    parser.add_argument("--codex-executable", default="codex")
    parser.add_argument("--identity-file", help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    role = fixed_role or args.role
    request = json.load(sys.stdin)
    if not isinstance(request, dict) or not isinstance(request.get("response_contract"), dict):
        parser.error("actor request must contain a response_contract object")
    try:
        projected = project_actor_request(role, request, backend=args.backend)
    except ValueError as exc:
        parser.error(str(exc))
    deadline = time.monotonic() + args.timeout
    with PluginService(args.root) as service:
        service._project(args.project_id)
        task = service.state.create_actor_task(args.project_id, role, projected)
        if args.backend == "codex-native" and task["status"] == "pending":
            runner = CodexNativeActorRunner(
                service.control, service.state, codex_executable=args.codex_executable
            )
            try:
                result = runner.run(task, args.timeout)
                task = service.state.update_actor_task(
                    task["id"], task["revision"], response=result.response
                )
            except Exception as exc:
                task = service.state.update_actor_task(
                    task["id"], task["revision"], rejection_reason=str(exc)[:4000]
                )
        if args.backend == "codex-native":
            if task["status"] == "submitted":
                audit = service.state.latest_actor_run(task["id"])
                policy = actor_policy(role)
                if (
                    audit is None
                    or audit["status"] != "completed"
                    or audit["policy_fingerprint"] != policy["fingerprint"]
                    or audit["output_fingerprint"] != stable_hash(task["response"])
                ):
                    print("native actor response has no matching completed isolation audit", file=sys.stderr)
                    return 4
                json.dump(task["response"], sys.stdout, ensure_ascii=False)
                return 0
            print(task["rejection_reason"] or "isolated Codex actor failed", file=sys.stderr)
            return 2
    while time.monotonic() < deadline:
        try:
            with PluginService(args.root) as service:
                task = service.state.get_actor_task(task["id"])
        except sqlite3.OperationalError:
            time.sleep(0.1)
            continue
        if task["status"] == "submitted":
            json.dump(task["response"], sys.stdout, ensure_ascii=False)
            return 0
        if task["status"] == "rejected":
            print(task["rejection_reason"] or "actor task rejected", file=sys.stderr)
            return 2
        time.sleep(0.1)
    print(f"actor task timed out: {task['id']}", file=sys.stderr)
    return 3


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
