from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Direction = Literal["maximize", "minimize"]


@dataclass(slots=True)
class Objective:
    name: str
    direction: Direction = "maximize"
    weight: float = 1.0


@dataclass(slots=True)
class Constraint:
    name: str
    description: str
    hard: bool = True


@dataclass(slots=True)
class Budget:
    generations: int = 20
    population_size: int = 32
    evaluator_timeout_seconds: float = 30.0
    seed: int = 0


@dataclass(slots=True)
class SearchPolicy:
    """Search-level controls that stay independent from domain mathematics."""

    novelty_probability: float = 0.25
    novelty_k: int = 5
    migration_interval: int = 5
    migrants_per_island: int = 1
    checkpoint_interval: int = 1


@dataclass(slots=True)
class ExplorerPolicy:
    """Controls for optional LLM/research-explorer proposal generation."""

    enabled: bool = False
    interval: int = 1
    proposals_per_interval: int = 2
    context_candidates: int = 8
    feedback_items: int = 12
    timeout_seconds: float = 60.0


@dataclass(slots=True)
class ResearchSpec:
    """Machine-readable contract for one mathematical research run."""

    name: str
    problem: str
    domain: str = "generic"
    mode: Literal["metric_search", "proof_search", "hybrid"] = "metric_search"
    objectives: list[Objective] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    behavior_dimensions: list[str] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    search: SearchPolicy = field(default_factory=SearchPolicy)
    explorer: ExplorerPolicy = field(default_factory=ExplorerPolicy)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("ResearchSpec.name must not be empty")
        if not self.problem.strip():
            raise ValueError("ResearchSpec.problem must not be empty")
        if self.mode in {"metric_search", "hybrid"} and not self.objectives:
            raise ValueError("metric_search/hybrid requires at least one objective")
        if self.budget.generations < 1 or self.budget.population_size < 1:
            raise ValueError("budget values must be positive")
        if self.budget.evaluator_timeout_seconds <= 0:
            raise ValueError("evaluator timeout must be positive")
        if not 0.0 <= self.search.novelty_probability <= 1.0:
            raise ValueError("novelty_probability must be in [0, 1]")
        if self.search.novelty_k < 1:
            raise ValueError("novelty_k must be positive")
        if self.search.migration_interval < 1 or self.search.migrants_per_island < 1:
            raise ValueError("migration settings must be positive")
        if self.search.checkpoint_interval < 1:
            raise ValueError("checkpoint_interval must be positive")
        if self.explorer.interval < 1:
            raise ValueError("explorer.interval must be positive")
        if self.explorer.proposals_per_interval < 1:
            raise ValueError("explorer.proposals_per_interval must be positive")
        if self.explorer.context_candidates < 1 or self.explorer.feedback_items < 1:
            raise ValueError("explorer context/feedback sizes must be positive")
        if self.explorer.timeout_seconds <= 0:
            raise ValueError("explorer.timeout_seconds must be positive")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchSpec":
        spec = cls(
            name=data["name"],
            problem=data["problem"],
            domain=data.get("domain", "generic"),
            mode=data.get("mode", "metric_search"),
            objectives=[Objective(**item) for item in data.get("objectives", [])],
            constraints=[Constraint(**item) for item in data.get("constraints", [])],
            behavior_dimensions=list(data.get("behavior_dimensions", [])),
            budget=Budget(**data.get("budget", {})),
            search=SearchPolicy(**data.get("search", {})),
            explorer=ExplorerPolicy(**data.get("explorer", {})),
            metadata=dict(data.get("metadata", {})),
        )
        spec.validate()
        return spec
