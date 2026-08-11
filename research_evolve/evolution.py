from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .candidates import Candidate


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


@dataclass
class MAPElitesArchive:
    """Keep the best candidate in each behavior cell."""

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

    def sample(self, rng: random.Random) -> Candidate | None:
        if not self.cells:
            return None
        return rng.choice(list(self.cells.values()))

    def elites(self) -> list[Candidate]:
        return sorted(self.cells.values(), key=lambda c: c.score if c.score is not None else float("-inf"), reverse=True)


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

    def sample_parent(self, rng: random.Random) -> tuple[int, Candidate] | None:
        populated = [(i, island) for i, island in enumerate(self.islands) if island.archive.cells]
        if not populated:
            return None
        index, island = rng.choice(populated)
        parent = island.archive.sample(rng)
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
