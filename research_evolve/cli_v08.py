from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cli as legacy_cli
from .formal_agents import CommandFormalRepairer, CommandFormalizer
from .formal_corpus import FormalCorpus
from .formal_project import LeanProjectEnvironment, LeanProjectLock
from .formal_search import FormalSearchMemory, FormalSearchPipeline, FormalSearchPolicy
from .goal_retrieval import GoalPremiseSelector, GoalRetrievalMemory
from .project_kernel import ProjectLeanKernel


V08_COMMANDS = {
    "formal-corpus-index",
    "goal-premise-search",
    "formal-search",
    "goal-premise-selections",
    "formal-search-events",
}


def _project(args: argparse.Namespace) -> LeanProjectEnvironment:
    lock = LeanProjectLock.read(args.project_lock)
    return LeanProjectEnvironment.create(
        args.project_root,
        lock,
        lake_command=args.lake_command,
        build_targets=args.project_build_target or [],
        copy_dependency_cache=not args.no_copy_dependency_cache,
    )


def _cmd_corpus_index(args: argparse.Namespace) -> int:
    try:
        lock = LeanProjectLock.read(args.project_lock)
        info = FormalCorpus.build(args.project_root, lock, args.output)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"output": args.output, **info.to_dict()}, indent=2, ensure_ascii=False))
    return 0


def _cmd_goal_search(args: argparse.Namespace) -> int:
    try:
        with FormalCorpus(args.formal_corpus) as corpus:
            selector = GoalPremiseSelector(corpus, limit=args.limit, candidate_limit=args.candidate_limit)
            selection = selector.select(
                formal_spec_id="cli-preview",
                query=args.query,
                root_imports=args.import_module or [],
                diagnostics=args.diagnostics or "",
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(selection.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _cmd_formal_search(args: argparse.Namespace) -> int:
    if not args.repairer_command:
        raise SystemExit("v0.8 formal-search requires --repairer-command")
    try:
        project = _project(args)
        corpus = FormalCorpus(args.formal_corpus)
        if corpus.project_fingerprint != project.fingerprint:
            corpus.close()
            raise ValueError(
                "formal corpus project fingerprint does not match configured project lock: "
                f"corpus={corpus.project_fingerprint}, project={project.fingerprint}"
            )
        selector = GoalPremiseSelector(corpus, limit=args.premise_limit, candidate_limit=args.premise_candidate_limit)
        formalizer = CommandFormalizer(args.formalizer_command, timeout_seconds=args.actor_timeout)
        repairer = CommandFormalRepairer(args.repairer_command, timeout_seconds=args.actor_timeout)
        kernel = ProjectLeanKernel(
            project,
            timeout_seconds=args.kernel_timeout,
            fresh_checker_timeout_seconds=args.fresh_checker_timeout,
        )
        policy = FormalSearchPolicy(
            beam_width=args.beam_width,
            branching_factor=args.branching_factor,
            max_rounds=args.max_rounds,
            max_kernel_attempts=args.max_kernel_attempts,
        )
        with FormalSearchPipeline(
            args.workspace,
            formalizer,
            kernel,
            repairer,
            premise_selector=selector,
            search_policy=policy,
            max_targets=args.max_targets,
            evidence_context=args.evidence_context,
        ) as pipeline:
            summary = pipeline.run()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _cmd_goal_memory(args: argparse.Namespace) -> int:
    memory = GoalRetrievalMemory(Path(args.workspace) / "formal_goal_retrieval.sqlite3")
    try:
        data = memory.list(args.limit)
    finally:
        memory.close()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def _cmd_search_memory(args: argparse.Namespace) -> int:
    memory = FormalSearchMemory(Path(args.workspace) / "formal_search.sqlite3")
    try:
        data = memory.list(args.limit)
    finally:
        memory.close()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def build_v08_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-evolve", description="ResearchEvolve v0.8 formal retrieval/search commands")
    sub = parser.add_subparsers(dest="command", required=True)

    corpus = sub.add_parser("formal-corpus-index", help="build a frozen SQLite corpus from project and dependency Lean sources")
    corpus.add_argument("--project-root", required=True)
    corpus.add_argument("--project-lock", required=True)
    corpus.add_argument("--output", required=True)
    corpus.set_defaults(func=_cmd_corpus_index)

    goal = sub.add_parser("goal-premise-search", help="preview v0.8 goal-conditioned retrieval over frozen import closure")
    goal.add_argument("--formal-corpus", required=True)
    goal.add_argument("--query", required=True)
    goal.add_argument("--diagnostics", default="")
    goal.add_argument("--import", dest="import_module", action="append", help="frozen root import; repeat as needed")
    goal.add_argument("--limit", type=int, default=16)
    goal.add_argument("--candidate-limit", type=int, default=512)
    goal.set_defaults(func=_cmd_goal_search)

    search = sub.add_parser("formal-search", help="run v0.8 goal-conditioned beam formal proof search")
    search.add_argument("--workspace", default=".researchevolve/run")
    search.add_argument("--formalizer-command", required=True)
    search.add_argument("--repairer-command", required=True)
    search.add_argument("--project-root", required=True)
    search.add_argument("--project-lock", required=True)
    search.add_argument("--formal-corpus", required=True)
    search.add_argument("--lake-command", default="lake")
    search.add_argument("--project-build-target", action="append")
    search.add_argument("--no-copy-dependency-cache", action="store_true")
    search.add_argument("--premise-limit", type=int, default=16)
    search.add_argument("--premise-candidate-limit", type=int, default=512)
    search.add_argument("--beam-width", type=int, default=3)
    search.add_argument("--branching-factor", type=int, default=2)
    search.add_argument("--max-rounds", type=int, default=3)
    search.add_argument("--max-kernel-attempts", type=int, default=12)
    search.add_argument("--actor-timeout", type=float, default=60.0)
    search.add_argument("--kernel-timeout", type=float, default=60.0)
    search.add_argument("--fresh-checker-timeout", type=float, default=300.0)
    search.add_argument("--max-targets", type=int, default=4)
    search.add_argument("--evidence-context", type=int, default=24)
    search.set_defaults(func=_cmd_formal_search)

    goal_memory = sub.add_parser("goal-premise-selections", help="show v0.8 goal-conditioned retrieval decisions")
    goal_memory.add_argument("--workspace", default=".researchevolve/run")
    goal_memory.add_argument("--limit", type=int, default=20)
    goal_memory.set_defaults(func=_cmd_goal_memory)

    search_memory = sub.add_parser("formal-search-events", help="show v0.8 formal beam-search events")
    search_memory.add_argument("--workspace", default=".researchevolve/run")
    search_memory.add_argument("--limit", type=int, default=20)
    search_memory.set_defaults(func=_cmd_search_memory)

    return parser


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in V08_COMMANDS:
        parser = build_v08_parser()
        args = parser.parse_args()
        return int(args.func(args))
    return legacy_cli.main()


if __name__ == "__main__":
    raise SystemExit(main())
