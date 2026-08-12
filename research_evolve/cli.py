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
from .formal import FormalMemory
from .formal_agents import CommandFormalizer, CommandFormalRepairer
from .formal_pipeline import FormalPipeline
from .formal_project import LeanProjectEnvironment, LeanProjectLock
from .formal_retrieval import FormalRetrievalMemory, PremiseIndex, PremiseSelector, ProofSearchBudget
from .formal_retrieval_pipeline import RetrievalFormalPipeline
from .formal_search import (
    CommandTacticGenerator,
    FrozenLeanProofWorker,
    InteractiveProofSearchBudget,
    ProofSearchFormalizer,
)
from .graph import ResearchGraph
from .ideas import IdeaMemory
from .lean_kernel import LeanKernel
from .mutation import FourLevelMutator
from .project_kernel import ProjectCheckMemory, ProjectLeanKernel
from .proof_agents import CommandProofPlanner, CommandProofVerifier, CommandProver
from .proof_pipeline import ProofPipeline
from .proofs import ProofMemory
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
            "checkpoint_interval": 1,
        },
        "explorer": {
            "enabled": False,
            "interval": 1,
            "proposals_per_interval": 2,
            "context_candidates": 8,
            "feedback_items": 12,
            "timeout_seconds": 60,
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
            "timeout_seconds": 60,
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
    with ResearchEngine(spec, workspace=args.workspace, island_count=args.islands, mutator=mutator, explorer=explorer, conjecturer=conjecturer) as engine:
        summary = engine.run(seeds, evaluator_paths, resume=args.resume, domain_pack=pack.name if pack is not None else None)
    print(json.dumps(summary.to_dict(), indent=2))
    return 0


def _cmd_prove(args: argparse.Namespace) -> int:
    planner = CommandProofPlanner(args.planner_command, timeout_seconds=args.timeout)
    prover = CommandProver(args.prover_command, timeout_seconds=args.timeout)
    verifier = CommandProofVerifier(args.verifier_command, timeout_seconds=args.timeout)
    try:
        with ProofPipeline(args.workspace, planner, prover, verifier, max_conjectures=args.max_conjectures, max_lemmas=args.max_lemmas, evidence_context=args.evidence_context, min_verifier_confidence=args.min_verifier_confidence) as pipeline:
            summary = pipeline.run()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _project_environment(args: argparse.Namespace) -> LeanProjectEnvironment | None:
    if not args.project_root and not args.project_lock:
        return None
    if not args.project_root or not args.project_lock:
        raise SystemExit("v0.7 project mode requires both --project-root and --project-lock")
    try:
        lock = LeanProjectLock.read(args.project_lock)
        return LeanProjectEnvironment.create(args.project_root, lock, lake_command=args.lake_command, build_targets=args.project_build_target or [], copy_dependency_cache=not args.no_copy_dependency_cache)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _cmd_formalize(args: argparse.Namespace) -> int:
    project = _project_environment(args)
    kernel = ProjectLeanKernel(project, timeout_seconds=args.kernel_timeout) if project is not None else LeanKernel(args.lean_command, timeout_seconds=args.kernel_timeout)
    selector = None
    if args.premise_index:
        if project is None:
            raise SystemExit("--premise-index requires v0.7 project mode")
        try:
            index = PremiseIndex.read(args.premise_index)
            if index.project_fingerprint != project.fingerprint:
                raise ValueError(f"premise index project fingerprint does not match configured project lock: index={index.project_fingerprint}, project={project.fingerprint}")
            budget = ProofSearchBudget(
                max_candidates=args.premise_candidate_budget,
                max_results=args.premise_limit,
                max_dependency_expansions=args.premise_dependency_budget,
                max_context_chars=args.premise_context_budget,
            )
            selector = PremiseSelector(index, limit=args.premise_limit, budget=budget)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    if bool(args.formalizer_command) == bool(args.tactic_generator_command):
        raise SystemExit("choose exactly one of --formalizer-command or --tactic-generator-command")
    if args.tactic_generator_command:
        if project is None or selector is None:
            raise SystemExit("v0.9 interactive proof search requires frozen project mode and --premise-index")
        if args.repairer_command:
            raise SystemExit("--repairer-command cannot be combined with v0.9 interactive proof search")
        generator = CommandTacticGenerator(args.tactic_generator_command, timeout_seconds=args.actor_timeout)
        worker = FrozenLeanProofWorker(project, timeout_seconds=args.interaction_timeout)
        search_budget = InteractiveProofSearchBudget(
            max_states=args.search_max_states,
            max_depth=args.search_max_depth,
            max_tactics_per_state=args.search_tactics_per_state,
            max_lean_calls=args.search_max_lean_calls,
            max_model_calls=args.search_max_model_calls,
            max_retrieval_calls=args.search_max_retrieval_calls,
            max_wall_seconds=args.search_wall_seconds,
            beam_width=args.search_beam_width,
        )
        formalizer = ProofSearchFormalizer(
            args.workspace, worker, generator, premise_selector=selector,
            budget=search_budget, resume=args.search_resume,
        )
        repairer = None
        pipeline_cls = FormalPipeline
    else:
        formalizer = CommandFormalizer(args.formalizer_command, timeout_seconds=args.actor_timeout)
        repairer = CommandFormalRepairer(args.repairer_command, timeout_seconds=args.actor_timeout) if args.repairer_command else None
        pipeline_cls = RetrievalFormalPipeline if selector is not None else FormalPipeline
    kwargs: dict[str, Any] = {"max_targets": args.max_targets, "max_repairs": args.max_repairs, "evidence_context": args.evidence_context}
    if selector is not None and pipeline_cls is RetrievalFormalPipeline:
        kwargs["premise_selector"] = selector
    try:
        with pipeline_cls(args.workspace, formalizer, kernel, repairer, **kwargs) as pipeline:
            summary = pipeline.run()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _cmd_project_lock(args: argparse.Namespace) -> int:
    try:
        lock = LeanProjectLock.capture(args.project_root, source_roots=args.source_root or ["."], extra_paths=args.extra_path or [], allow_unlocked_dependencies=args.allow_unlocked_dependencies)
        lock.write(args.output)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"output": args.output, "fingerprint": lock.fingerprint, "toolchain": lock.toolchain}, indent=2))
    return 0


def _cmd_premise_index(args: argparse.Namespace) -> int:
    try:
        lock = LeanProjectLock.read(args.project_lock)
        index = PremiseIndex.build_from_project(args.project_root, lock)
        index.write(args.output)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"output": args.output, "fingerprint": index.fingerprint, "project_fingerprint": index.project_fingerprint, "premises": len(index.premises)}, indent=2))
    return 0


def _cmd_premise_search(args: argparse.Namespace) -> int:
    try:
        index = PremiseIndex.read(args.premise_index)
        budget = ProofSearchBudget(
            max_candidates=args.candidate_budget,
            max_results=args.limit,
            max_dependency_expansions=args.dependency_budget,
            max_context_chars=args.context_budget,
        )
        selector = PremiseSelector(index, limit=args.limit, budget=budget)
        selection = selector.select(
            formal_spec_id="cli-preview", query=args.query, goal_state=args.goal or args.query,
            allowed_modules=args.module or None,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(selection.to_dict(), indent=2, ensure_ascii=False))
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


def _cmd_proof_memory(args: argparse.Namespace) -> int:
    memory = ProofMemory(Path(args.workspace) / "proofs.sqlite3")
    try:
        if args.memory_kind == "specs":
            data = memory.list_specs(args.limit)
        elif args.memory_kind == "plans":
            data = memory.list_plans(args.limit)
        elif args.memory_kind == "artifacts":
            data = memory.list_artifacts(args.limit)
        else:
            data = memory.list_reviews(args.limit)
    finally:
        memory.close()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def _cmd_formal_memory(args: argparse.Namespace) -> int:
    memory = FormalMemory(Path(args.workspace) / "formal.sqlite3")
    try:
        if args.memory_kind == "specs":
            data = memory.list_specs(args.limit)
        elif args.memory_kind == "artifacts":
            data = memory.list_artifacts(args.limit)
        else:
            data = memory.list_kernel_runs(args.limit)
    finally:
        memory.close()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def _cmd_retrieval_memory(args: argparse.Namespace) -> int:
    memory = FormalRetrievalMemory(Path(args.workspace) / "formal_retrieval.sqlite3")
    try:
        data = memory.list(args.limit)
    finally:
        memory.close()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def _cmd_project_checks(args: argparse.Namespace) -> int:
    memory = ProjectCheckMemory(Path(args.workspace) / "formal_project.sqlite3")
    try:
        data = memory.list(args.limit)
    finally:
        memory.close()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-evolve", description="ResearchEvolve v0.8 research harness")
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
    prove = sub.add_parser("prove", help="run the v0.5 proof planner/prover/independent-verifier pipeline")
    prove.add_argument("--workspace", default=".researchevolve/run")
    prove.add_argument("--planner-command", required=True)
    prove.add_argument("--prover-command", required=True)
    prove.add_argument("--verifier-command", required=True)
    prove.add_argument("--timeout", type=float, default=60.0)
    prove.add_argument("--max-conjectures", type=int, default=4)
    prove.add_argument("--max-lemmas", type=int, default=24)
    prove.add_argument("--evidence-context", type=int, default=24)
    prove.add_argument("--min-verifier-confidence", type=float, default=0.7)
    prove.set_defaults(func=_cmd_prove)
    formalize = sub.add_parser("formalize", help="run Lean certification or v0.9 interactive proof-state search")
    formalize.add_argument("--workspace", default=".researchevolve/run")
    formalize.add_argument("--formalizer-command", help="v0.6-v0.8 whole-proof actor command")
    formalize.add_argument("--tactic-generator-command", help="v0.9 proof-state-conditioned tactic actor command")
    formalize.add_argument("--repairer-command")
    formalize.add_argument("--lean-command", default="lean", help="v0.6 standalone Lean command")
    formalize.add_argument("--project-root", help="v0.7 frozen Lake project root")
    formalize.add_argument("--project-lock", help="v0.7 Lean project lock JSON")
    formalize.add_argument("--lake-command", default="lake")
    formalize.add_argument("--project-build-target", action="append", help="Lake build target; repeat as needed")
    formalize.add_argument("--no-copy-dependency-cache", action="store_true", help="refuse copying .lake/packages into the isolated project")
    formalize.add_argument("--premise-index", help="v0.8 content-addressed declaration database (schema 1 indexes remain readable)")
    formalize.add_argument("--premise-limit", type=int, default=12)
    formalize.add_argument("--premise-candidate-budget", type=int, default=5000)
    formalize.add_argument("--premise-dependency-budget", type=int, default=8)
    formalize.add_argument("--premise-context-budget", type=int, default=24000)
    formalize.add_argument("--actor-timeout", type=float, default=60.0)
    formalize.add_argument("--kernel-timeout", type=float, default=60.0)
    formalize.add_argument("--interaction-timeout", type=float, default=60.0)
    formalize.add_argument("--search-max-states", type=int, default=500)
    formalize.add_argument("--search-max-depth", type=int, default=24)
    formalize.add_argument("--search-tactics-per-state", type=int, default=8)
    formalize.add_argument("--search-max-lean-calls", type=int, default=2000)
    formalize.add_argument("--search-max-model-calls", type=int, default=100)
    formalize.add_argument("--search-max-retrieval-calls", type=int, default=500)
    formalize.add_argument("--search-wall-seconds", type=float, default=900.0)
    formalize.add_argument("--search-beam-width", type=int, default=64)
    formalize.add_argument("--search-resume", action="store_true", help="resume the exact active v0.9 search journal")
    formalize.add_argument("--max-targets", type=int, default=4)
    formalize.add_argument("--max-repairs", type=int, default=2)
    formalize.add_argument("--evidence-context", type=int, default=24)
    formalize.set_defaults(func=_cmd_formalize)
    project_lock = sub.add_parser("lean-project-lock", help="freeze a Lean/Lake project into a content-addressed v0.7 lock")
    project_lock.add_argument("--project-root", required=True)
    project_lock.add_argument("--source-root", action="append", help="relative source root to hash; repeat as needed")
    project_lock.add_argument("--extra-path", action="append", help="additional relative project file to hash")
    project_lock.add_argument("--allow-unlocked-dependencies", action="store_true")
    project_lock.add_argument("--output", required=True)
    project_lock.set_defaults(func=_cmd_project_lock)
    premise_index = sub.add_parser("premise-index", help="build a v0.8 typed declaration/dependency database from a frozen Lean project")
    premise_index.add_argument("--project-root", required=True)
    premise_index.add_argument("--project-lock", required=True)
    premise_index.add_argument("--output", required=True)
    premise_index.set_defaults(func=_cmd_premise_index)
    premise_search = sub.add_parser("premise-search", help="preview deterministic v0.8 goal-conditioned premise retrieval")
    premise_search.add_argument("--premise-index", required=True)
    premise_search.add_argument("--query", required=True)
    premise_search.add_argument("--goal", help="current Lean goal/proof state for type-aware ranking")
    premise_search.add_argument("--module", action="append", help="restrict to a frozen import module")
    premise_search.add_argument("--limit", type=int, default=12)
    premise_search.add_argument("--candidate-budget", type=int, default=5000)
    premise_search.add_argument("--dependency-budget", type=int, default=8)
    premise_search.add_argument("--context-budget", type=int, default=24000)
    premise_search.set_defaults(func=_cmd_premise_search)
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
    proof_manifest = sub.add_parser("proof-manifest", help="show the v0.5 proof pipeline manifest")
    proof_manifest.add_argument("--workspace", default=".researchevolve/run")
    proof_manifest.set_defaults(func=_cmd_json_artifact, artifact="proof_manifest.json", limit=None)
    formal_manifest = sub.add_parser("formal-manifest", help="show the formal verification manifest")
    formal_manifest.add_argument("--workspace", default=".researchevolve/run")
    formal_manifest.set_defaults(func=_cmd_json_artifact, artifact="formal_manifest.json", limit=None)
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
    for command, help_text, memory_kind in [("proof-specs", "show frozen proof target specifications", "specs"), ("proof-plans", "show lemma decomposition plans", "plans"), ("proof-artifacts", "show natural-language proof artifacts", "artifacts"), ("proof-reviews", "show independent adversarial proof reviews", "reviews")]:
        proof_memory = sub.add_parser(command, help=help_text)
        proof_memory.add_argument("--workspace", default=".researchevolve/run")
        proof_memory.add_argument("--limit", type=int, default=20)
        proof_memory.set_defaults(func=_cmd_proof_memory, memory_kind=memory_kind)
    for command, help_text, memory_kind in [("formal-specs", "show frozen Lean formalization specifications", "specs"), ("formal-artifacts", "show generated/repaired Lean proof sources", "artifacts"), ("kernel-runs", "show Lean compiler/kernel results", "kernel_runs")]:
        formal_memory = sub.add_parser(command, help=help_text)
        formal_memory.add_argument("--workspace", default=".researchevolve/run")
        formal_memory.add_argument("--limit", type=int, default=20)
        formal_memory.set_defaults(func=_cmd_formal_memory, memory_kind=memory_kind)
    premise_selections = sub.add_parser("premise-selections", help="show v0.8 goal-conditioned retrieval/search decisions")
    premise_selections.add_argument("--workspace", default=".researchevolve/run")
    premise_selections.add_argument("--limit", type=int, default=20)
    premise_selections.set_defaults(func=_cmd_retrieval_memory)
    project_checks = sub.add_parser("project-checks", help="show v0.7 Lake build / Lean / leanchecker --fresh audit records")
    project_checks.add_argument("--workspace", default=".researchevolve/run")
    project_checks.add_argument("--limit", type=int, default=20)
    project_checks.set_defaults(func=_cmd_project_checks)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

