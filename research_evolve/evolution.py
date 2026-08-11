from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Iterable

from .candidates import Candidate
from .spec import Objective


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _candidate_novelty(candidate: Candidate) -> float:
    search = candidate.diagnostics.get("search", {}) if isinstance(candidate.diagnostics, dict) else {}
    try:
        return float(search.get("novelty", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _behavior_distance(a: Candidate, b: Candidate, dimensions: list[str]) -> float:
    if not dimensions:
        return 0.0
    distances: list[float] = []
    for dim in dimensions:
        left = a.behavior.get(dim, "unknown")
        right = b.behavior.get(dim, "unknown")
        if isinstance(left, Real) and isinstance(right, Real):
            denom = 1.0 + abs(float(left)) + abs(float(right))
            distances.append(min(1.0, abs(float(left) - float(right)) / denom))
        else:
            distances.append(0.0 if _freeze(left) == _freeze(right) else 1.0)
    return sum(distances) / len(distances)


@dataclass
class MAPElitesArchive:
    """Keep the best canonical-score candidate in each behavior cell."""

    dimensions: list[str]
    cells: dict[tuple[Any, ...], Candidate] = field(default_factory=dict)

    def cell_key(self, candidate: Candidate) -> tuple[Any, ...]:
        if not self.dimensions:
            return ("global",)
        return tuple(_freeze(candidate.behavior.get(dim, "unknown")) for dim in self.dimensions)

    def add(self, candidate: Candidate) -> bool:
        if not candidate.valid or candidate.score is None:
            return False
        key = self.cell_key(candidate)
        incumbent = self.cells.get(key)
        if incumbent is None or incumbent.score is None or candidate.score > incumbent.score:
            self.cells[key] = candidate
            return True
        return False

    def sample(self, rng: random.Random, novelty_probability: float = 0.0) -> Candidate | None:
        if not self.cells:
            return None
        candidates = list(self.cells.values())
        if novelty_probability > 0 and rng.random() < novelty_probability:
            candidates.sort(key=_candidate_novelty, reverse=True)
            top = max(1, math.ceil(len(candidates) * 0.25))
            return rng.choice(candidates[:top])
        return rng.choice(candidates)

    def elites(self) -> list[Candidate]:
        return sorted(self.cells.values(), key=lambda c: c.score if c.score is not None else float("-inf"), reverse=True)


class ParetoArchive:
    """Maintain the non-dominated frontier for the declared ResearchSpec objectives."""

    def __init__(self, objectives: Iterable[Objective]) -> None:
        self.objectives = list(objectives)
        self.members: dict[str, Candidate] = {}

    @staticmethod
    def _value(candidate: Candidate, objective: Objective) -> float | None:
        if objective.name == "score":
            return candidate.score
        value = candidate.metrics.get(objective.name)
        return None if value is None else float(value)

    def eligible(self, candidate: Candidate) -> bool:
        return bool(candidate.valid) and all(self._value(candidate, objective) is not None for objective in self.objectives)

    def dominates(self, left: Candidate, right: Candidate) -> bool:
        if not self.eligible(left) or not self.eligible(right):
            return False
        at_least_as_good = True
        strictly_better = False
        for objective in self.objectives:
            lv = self._value(left, objective)
            rv = self._value(right, objective)
            assert lv is not None and rv is not None
            if objective.direction == "maximize":
                at_least_as_good &= lv >= rv
                strictly_better |= lv > rv
            else:
                at_least_as_good &= lv <= rv
                strictly_better |= lv < rv
        return at_least_as_good and strictly_better

    def add(self, candidate: Candidate) -> bool:
        if not self.objectives or not self.eligible(candidate):
            return False
        if any(self.dominates(member, candidate) for member in self.members.values()):
            return False
        dominated = [candidate_id for candidate_id, member in self.members.items() if self.dominates(candidate, member)]
        for candidate_id in dominated:
            self.members.pop(candidate_id, None)
        was_new = candidate.id not in self.members
        self.members[candidate.id] = candidate
        return was_new or bool(dominated)

    def candidates(self) -> list[Candidate]:
        return sorted(
            self.members.values(),
            key=lambda candidate: candidate.score if candidate.score is not None else float("-inf"),
            reverse=True,
        )

    def restore(self, candidates: Iterable[Candidate]) -> None:
        self.members.clear()
        for candidate in candidates:
            self.add(candidate)


class NoveltyArchive:
    """Behavior-space archive used to estimate how different a candidate is."""

    def __init__(self, dimensions: list[str], k: int = 5, max_size: int = 2000) -> None:
        self.dimensions = list(dimensions)
        self.k = max(1, int(k))
        self.max_size = max(1, int(max_size))
        self.members: dict[str, Candidate] = {}

    def score(self, candidate: Candidate) -> float:
        peers = [member for member in self.members.values() if member.id != candidate.id]
        if not peers:
            return 1.0
        distances = sorted(_behavior_distance(candidate, member, self.dimensions) for member in peers)
        nearest = distances[: min(self.k, len(distances))]
        return sum(nearest) / len(nearest) if nearest else 0.0

    def add(self, candidate: Candidate) -> None:
        if not candidate.valid:
            return
        self.members[candidate.id] = candidate
        if len(self.members) > self.max_size:
            oldest_id = next(iter(self.members))
            self.members.pop(oldest_id, None)

    def candidate_ids(self) -> list[str]:
        return list(self.members)

    def restore(self, candidates: Iterable[Candidate]) -> None:
        self.members.clear()
        for candidate in candidates:
            self.add(candidate)


@dataclass
class Island:
    name: str
    archive: MAPElitesArchive


class IslandPopulation:
    """Independent MAP-Elites archives with periodic elite migration."""

    def __init__(self, count: int, dimensions: list[str]) -> None:
        if count < 1:
            raise ValueError("island count must be positive")
        self.islands = [Island(f"island-{i}", MAPElitesArchive(list(dimensions))) for i in range(count)]

    def add(self, candidate: Candidate, island_index: int) -> bool:
        return self.islands[island_index % len(self.islands)].archive.add(candidate)

    def sample_parent(self, rng: random.Random, novelty_probability: float = 0.0) -> tuple[int, Candidate] | None:
        populated = [(i, island) for i, island in enumerate(self.islands) if island.archive.cells]
        if not populated:
            return None
        index, island = rng.choice(populated)
        parent = island.archive.sample(rng, novelty_probability=novelty_probability)
        return None if parent is None else (index, parent)

    def migrate(self, migrants_per_island: int = 1) -> int:
        if len(self.islands) < 2:
            return 0
        moved = 0
        snapshots = [island.archive.elites()[:migrants_per_island] for island in self.islands]
        for index, migrants in enumerate(snapshots):
            target = self.islands[(index + 1) % len(self.islands)].archive
            for candidate in migrants:
                moved += int(target.add(candidate))
        return moved

    def global_elites(self) -> list[Candidate]:
        unique: dict[str, Candidate] = {}
        for island in self.islands:
            for candidate in island.archive.elites():
                unique[candidate.id] = candidate
        return sorted(unique.values(), key=lambda c: c.score if c.score is not None else float("-inf"), reverse=True)

    def candidate_ids_by_island(self) -> list[list[str]]:
        return [[candidate.id for candidate in island.archive.elites()] for island in self.islands]

    def restore(self, candidates_by_island: Iterable[Iterable[Candidate]]) -> None:
        for island in self.islands:
            island.archive.cells.clear()
        for index, candidates in enumerate(candidates_by_island):
            if index >= len(self.islands):
                break
            for candidate in candidates:
                self.add(candidate, index)
