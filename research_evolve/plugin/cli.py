from __future__ import annotations

import argparse
import json

from .mcp_server import main as mcp_main
from .service import PluginError, PluginService


def main() -> int:
    parser = argparse.ArgumentParser(prog="research-evolve-plugin")
    parser.add_argument("--root", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--project-id")
    create = sub.add_parser("create")
    create.add_argument("project_id")
    create.add_argument("objective")
    create.add_argument("--directory")
    create.add_argument("--request-id", default="cli-project-create")
    sub.add_parser("serve")
    args = parser.parse_args()
    if args.command == "serve":
        import sys
        sys.argv = ["research-evolve-mcp", "--root", args.root]
        return mcp_main()
    try:
        with PluginService(args.root) as service:
            result = service.doctor(project_id=args.project_id) if args.command == "doctor" else service.project_create(request_id=args.request_id, project_id=args.project_id, objective=args.objective, directory=args.directory)
    except PluginError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
