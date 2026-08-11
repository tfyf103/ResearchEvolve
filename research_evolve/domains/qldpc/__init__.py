from __future__ import annotations

from pathlib import Path

from ...domain import DomainPack
from ...mutation import FourLevelMutator
from .mutation import QLDPCMutator


class QLDPCDomainPack(DomainPack):
    """Small pure-Python bicycle/CSS benchmark for exercising ResearchEvolve."""

    name = "qldpc"

    def evaluator_paths(self) -> list[Path]:
        root = Path(__file__).resolve().parent
        return [
            root / "evaluator_constraints.py",
            root / "evaluator_parameters.py",
            root / "evaluator_distance.py",
        ]

    def mutator(self) -> FourLevelMutator:
        return QLDPCMutator()


__all__ = ["QLDPCDomainPack", "QLDPCMutator"]
