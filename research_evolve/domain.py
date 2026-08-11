from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .mutation import FourLevelMutator


class DomainPack(ABC):
    """Domain adapter that supplies mathematically meaningful search components."""

    name: str = "generic"

    @abstractmethod
    def evaluator_paths(self) -> list[Path]:
        """Return evaluator stages ordered from cheapest to most expensive."""

    @abstractmethod
    def mutator(self) -> FourLevelMutator:
        """Return the domain-specific four-level mutator."""

    def prepare_seed(self, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(payload)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "evaluators": [str(path) for path in self.evaluator_paths()]}


def load_domain_pack(specifier: str) -> DomainPack:
    """Load a built-in pack by short name or a custom ``module:Class`` pack."""

    if specifier == "qldpc":
        from .domains.qldpc import QLDPCDomainPack

        return QLDPCDomainPack()
    if ":" not in specifier:
        raise ValueError("domain pack must be a built-in name (e.g. qldpc) or module:Class")
    module_name, class_name = specifier.split(":", 1)
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    instance = cls()
    if not isinstance(instance, DomainPack):
        raise TypeError("custom domain pack must subclass research_evolve.domain.DomainPack")
    return instance
