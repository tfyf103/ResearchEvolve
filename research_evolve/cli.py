from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any

from .candidates import CandidateDB
from .conjecturer import CommandConjecturer
from .conjectures import ConjectureMemory
from .domain import DomainPack, load_domain_pack
from .engine import ResearchEngine
from .explorer import CommandExplorer
from .graph import ResearchGraph
from .ideas import IdeaMemory
from .mutation import FourLevelMutator
from .spec import ResearchSpec


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_mutator(path: str | None) -> FourLevelMutator | None:
    if not path:
        return None
    if ":" not in path:
        raise SystemExit("--mutator must use module:Class syntax")
    module_name, class_name = path.split(":", 1)
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    instance = cls()
    if not hasattr(instance, "mutate") or not hasattr(instance, "sample_level"):
        raise SystemExit("custom mutator must implement mutate() and sample_level()")
    return instance


def _load_pack(path: str | None) -> DomainPack | None:
    if not path:
        return None
    try:
        return load_domain_pack(path)
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise SystemExit(f"could not load domain pack: {exc}") from exc


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
        "search": {
            "novelty_probability": 0.25,
            "novelty_k": 5,
            "migration_interval": 5,
            "migrants_per_island": 1,
            "checkpoint_interval": 1
        },
        "explorer": {
            "enabled": False,
            "interval": 1,
            "proposals_per_interval": 2,
            "context_candidates": 8,
            "feedback_items": 12,
            "timeout_seconds": 60
        },
        "conjecture": {
            "enabled": False,
            "interval": 1,
            "observations_per_interval": 12,
            "conjectures_per_interval": 2,
            "context_candidates": 24,
            "context_conjectures": 12,
            "counterexample_trials": 8,
            "min_evidence": 3,
            "timeout_seconds": 60
        },
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

    pack = _load_pack(args.domain_pack)
    if pack is not None:
        seeds = [pack.prepare_seed(item) for item in seeds]
    mutator = _load_mutator(args.mutator) or (pack.mutator() if pack is not None else None)
    evaluator_paths = list(args.evaluator or [])
    if not evaluator_paths and pack is not None:
        evaluator_paths = [str(path) for path in pack.evaluator_paths()]
    if not evaluator_paths:
        raise SystemExit("provide at least one --evaluator or use --domain-pack")

    explorer = None
    if args.explorer_command:
        if not spec.explorer.enabled:
            raise SystemExit("--explorer-command requires explorer.enabled=true in the ResearchSpec")
        explorer = CommandExplorer(args.explorer_command, timeout_seconds=spec.explorer.timeout_seconds)
    elif spec.explorer.enabled:
        raise SystemExit("ResearchSpec enables explorer proposals; provide --explorer-command")

    conjecturer = None
    if args.conjecturer_command:
        if not spec.conjecture.enabled:
            raise SystemExit("--conjecturer-command requires conjecture.enabled=true in the ResearchSpec")
        conjecturer = CommandConjecturer(args.conjecturer_command, timeout_seconds=spec.conjecture.timeout_seconds)
    elif spec.conjecture.enabled:
        raise SystemExit("ResearchSpec enables conjecture generation; provide --conjecturer-command")

    with ResearchEngine(
        spec,
        workspace=args.workspace,
        island_count=args.islands,
        mutator=mutator,
        explorer=explorer,
        conjecturer=conjecturer,
    ) as engine:
        summary = engine.run(
            seeds,
            evaluator_paths,
            resume=args.resume,
            domain_pack=pack.name if pack is not None else None,
        )
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


def _cmd_json_artifact(args: argparse.Namespace) -> int:
    path = Path(args.workspace) / args.artifact
    if not path.is_file():
        raise SystemExit(f"artifact does not exist: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if args.limit is not None and isinstance(data, list):
        data = data[: args.limit]
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def _cmd_idea_memory(args: argparse.Namespace) -> int:
    memory = IdeaMemory(Path(args.workspace) / "ideas.sqlite3")
    try:
        data = memory.list_ideas(args.limit) if args.memory_kind == "ideas" else memory.list_proposals(args.limit)
    finally:
        memory.close()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def _cmd_conjecture_memory(args: argparse.Namespace) -> int:
    memory = ConjectureMemory(Path(args.workspace) / "conjectures.sqlite3")
    try:
        if args.memory_kind == "observations":
            data = memory.recent_observations(args.limit)
        elif args.memory_kind == "conjectures":
            data = memory.recent_conjectures(args.limit)
        else:
            data = memory.list_counterexamples(args.limit)
    finally:
        memory.close()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-evolve", description="ResearchEvolve v0.4 research harness")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="write a ResearchSpec JSON template")
    init.add_argument("output", nargs="?", default="research.json")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=_cmd_init)

    run = sub.add_parser("run", help="run evolutionary mathematical search")
    run.add_argument("--spec", required=True)
    run.add_argument("--evaluator", action="append", help="evaluator stage path; repeat to form a cheap-to-expensive cascade")
    run.add_argument("--domain-pack", help="built-in name such as qldpc, or module:Class")
    run.add_argument("--seeds", required=True)
    run.add_argument("--workspace", default=".researchevolve/run")
    run.add_argument("--islands", type=int, default=4)
    run.add_argument("--mutator", help="optional custom mutator as module:Class")
    run.add_argument("--explorer-command", help="external Explorer command using the v0.3 JSON stdin/stdout protocol")
    run.add_argument("--conjecturer-command", help="external Conjecturer command using the v0.4 JSON stdin/stdout protocol")
    run.add_argument("--resume", action="store_true", help="resume from the workspace generation checkpoint")
    run.set_defaults(func=_cmd_run)

    inspect = sub.add_parser("inspect", help="show highest canonical-score valid candidates")
    inspect.add_argument("--workspace", default=".researchevolve/run")
    inspect.add_argument("--limit", type=int, default=10)
    inspect.set_defaults(func=_cmd_inspect)

    graph = sub.add_parser("graph", help="export the persistent Research Graph")
    graph.add_argument("--workspace", default=".researchevolve/run")
    graph.add_argument("--output")
    graph.set_defaults(func=_cmd_graph)

    pareto = sub.add_parser("pareto", help="show the latest multi-objective Pareto frontier")
    pareto.add_argument("--workspace", default=".researchevolve/run")
    pareto.add_argument("--limit", type=int)
    pareto.set_defaults(func=_cmd_json_artifact, artifact="pareto.json")

    manifest = sub.add_parser("manifest", help="show the reproducibility manifest for a run")
    manifest.add_argument("--workspace", default=".researchevolve/run")
    manifest.set_defaults(func=_cmd_json_artifact, artifact="manifest.json", limit=None)

    ideas = sub.add_parser("ideas", help="show recent structured Idea Genomes")
    ideas.add_argument("--workspace", default=".researchevolve/run")
    ideas.add_argument("--limit", type=int, default=20)
    ideas.set_defaults(func=_cmd_idea_memory, memory_kind="ideas")

    proposals = sub.add_parser("proposals", help="show Explorer proposals and evaluator outcomes")
    proposals.add_argument("--workspace", default=".researchevolve/run")
    proposals.add_argument("--limit", type=int, default=20)
    proposals.set_defaults(func=_cmd_idea_memory, memory_kind="proposals")

    observations = sub.add_parser("observations", help="show deterministic empirical observations")
    observations.add_argument("--workspace", default=".researchevolve/run")
    observations.add_argument("--limit", type=int, default=20)
    observations.set_defaults(func=_cmd_conjecture_memory, memory_kind="observations")

    conjectures = sub.add_parser("conjectures", help="show conjectures and empirical support/refutation status")
    conjectures.add_argument("--workspace", default=".researchevolve/run")
    conjectures.add_argument("--limit", type=int, default=20)
    conjectures.set_defaults(func=_cmd_conjecture_memory, memory_kind="conjectures")

    counterexamples = sub.add_parser("counterexamples", help="show verified empirical counterexamples")
    counterexamples.add_argument("--workspace", default=".researchevolve/run")
    counterexamples.add_argument("--limit", type=int, default=20)
    counterexamples.set_defaults(func=_cmd_conjecture_memory, memory_kind="counterexamples")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
