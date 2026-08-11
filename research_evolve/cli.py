from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .candidates import CandidateDB
from .engine import ResearchEngine
from .graph import ResearchGraph
from .spec import ResearchSpec


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _cmd_init(args: argparse.Namespace) -> int:
    template = {
        "name": "my-research-run",
        "problem": "Describe the mathematical construction or optimization problem.",
        "domain": "generic",
        "mode": "metric_search",
        "objectives": [{"name": "quality", "direction": "maximize", "weight": 1.0}],
        "constraints": [],
        "behavior_dimensions": ["representation"],
        "budget": {"generations": 20, "population_size": 32, "evaluator_timeout_seconds": 30, "seed": 0},
        "metadata": {},
    }
    path = Path(args.output)
    if path.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite {path}; use --force")
    path.write_text(json.dumps(template, indent=2), encoding="utf-8")
    print(path)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    spec = ResearchSpec.from_dict(_read_json(args.spec))
    seeds = _read_json(args.seeds)
    if not isinstance(seeds, list) or not all(isinstance(item, dict) for item in seeds):
        raise SystemExit("seeds must be a JSON list of objects")
    with ResearchEngine(spec, workspace=args.workspace, island_count=args.islands) as engine:
        summary = engine.run(seeds, args.evaluator)
    print(json.dumps(summary.to_dict(), indent=2))
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    db = CandidateDB(Path(args.workspace) / "candidates.sqlite3")
    try:
        rows = [candidate.to_dict() for candidate in db.best(args.limit)]
    finally:
        db.close()
    print(json.dumps(rows, indent=2))
    return 0


def _cmd_graph(args: argparse.Namespace) -> int:
    graph = ResearchGraph(Path(args.workspace) / "research_graph.sqlite3")
    try:
        data = graph.export()
    finally:
        graph.close()
    if args.output:
        Path(args.output).write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(args.output)
    else:
        print(json.dumps(data, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-evolve", description="ResearchEvolve v0.1 research harness")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="write a ResearchSpec JSON template")
    init.add_argument("output", nargs="?", default="research.json")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=_cmd_init)

    run = sub.add_parser("run", help="run evolutionary mathematical search")
    run.add_argument("--spec", required=True)
    run.add_argument("--evaluator", required=True)
    run.add_argument("--seeds", required=True)
    run.add_argument("--workspace", default=".researchevolve/run")
    run.add_argument("--islands", type=int, default=4)
    run.set_defaults(func=_cmd_run)

    inspect = sub.add_parser("inspect", help="show highest-scoring valid candidates")
    inspect.add_argument("--workspace", default=".researchevolve/run")
    inspect.add_argument("--limit", type=int, default=10)
    inspect.set_defaults(func=_cmd_inspect)

    graph = sub.add_parser("graph", help="export the persistent Research Graph")
    graph.add_argument("--workspace", default=".researchevolve/run")
    graph.add_argument("--output")
    graph.set_defaults(func=_cmd_graph)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
