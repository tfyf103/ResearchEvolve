from __future__ import annotations

import argparse
import json
import sys
import sqlite3
import time

from .actors import ROLE_REQUIRED
from .service import PluginService


def run(fixed_role: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-evolve-actor-bridge")
    parser.add_argument("--root", required=True)
    parser.add_argument("--project-id", required=True)
    if fixed_role is None:
        parser.add_argument("--role", required=True, choices=sorted(ROLE_REQUIRED))
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    role = fixed_role or args.role
    request = json.load(sys.stdin)
    if not isinstance(request, dict) or not isinstance(request.get("response_contract"), dict):
        parser.error("actor request must contain a response_contract object")
    deadline = time.monotonic() + args.timeout
    with PluginService(args.root) as service:
        service._project(args.project_id)
        task = service.state.create_actor_task(args.project_id, role, request)
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
