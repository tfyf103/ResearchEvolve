from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _tuple_tree(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_tuple_tree(item) for item in value)
    return value


@dataclass(slots=True)
class SearchCheckpoint:
    generation: int
    evaluated: int
    valid: int
    rng_state: Any
    island_candidate_ids: list[list[str]] = field(default_factory=list)
    pareto_candidate_ids: list[str] = field(default_factory=list)
    novelty_candidate_ids: list[str] = field(default_factory=list)
    manifest_fingerprint: str = ""
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchCheckpoint":
        return cls(
            generation=int(data["generation"]),
            evaluated=int(data["evaluated"]),
            valid=int(data["valid"]),
            rng_state=_tuple_tree(data["rng_state"]),
            island_candidate_ids=[list(items) for items in data.get("island_candidate_ids", [])],
            pareto_candidate_ids=list(data.get("pareto_candidate_ids", [])),
            novelty_candidate_ids=list(data.get("novelty_candidate_ids", [])),
            manifest_fingerprint=str(data.get("manifest_fingerprint", "")),
            version=int(data.get("version", 1)),
        )


class CheckpointStore:
    """Atomic JSON checkpoints written at generation boundaries."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> SearchCheckpoint:
        return SearchCheckpoint.from_dict(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, checkpoint: SearchCheckpoint) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(checkpoint.to_dict(), indent=2), encoding="utf-8")
        temporary.replace(self.path)
