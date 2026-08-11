from __future__ import annotations

import copy
import math
import random
from enum import Enum
from typing import Any


class MutationLevel(str, Enum):
    LOCAL = "local"
    STRUCTURAL = "structural"
    ALGEBRAIC = "algebraic"
    REPRESENTATION = "representation"


class FourLevelMutator:
    """Generic fallback mutator.

    Domain packs should subclass/replace these methods with mathematically
    meaningful transformations while preserving the four-level interface.
    """

    def mutate(self, payload: dict[str, Any], level: MutationLevel, rng: random.Random) -> dict[str, Any]:
        child = copy.deepcopy(payload)
        if level is MutationLevel.LOCAL:
            return self.local(child, rng)
        if level is MutationLevel.STRUCTURAL:
            return self.structural(child, rng)
        if level is MutationLevel.ALGEBRAIC:
            return self.algebraic(child, rng)
        return self.representation(child, rng)

    def local(self, payload: dict[str, Any], rng: random.Random) -> dict[str, Any]:
        numeric = [k for k, v in payload.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not numeric:
            payload["local_step"] = int(payload.get("local_step", 0)) + 1
            return payload
        key = rng.choice(numeric)
        value = float(payload[key])
        scale = max(1.0, abs(value) * 0.1)
        mutated = value + rng.gauss(0.0, scale)
        payload[key] = int(round(mutated)) if isinstance(payload[key], int) else mutated
        return payload

    def structural(self, payload: dict[str, Any], rng: random.Random) -> dict[str, Any]:
        lists = [k for k, v in payload.items() if isinstance(v, list)]
        if lists:
            key = rng.choice(lists)
            values = payload[key]
            if values and rng.random() < 0.5:
                del values[rng.randrange(len(values))]
            else:
                values.append(rng.randint(-3, 3))
        else:
            payload["structure"] = list(payload.get("structure", [])) + [rng.randint(-3, 3)]
        return payload

    def algebraic(self, payload: dict[str, Any], rng: random.Random) -> dict[str, Any]:
        numeric = [k for k, v in payload.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not numeric:
            payload["algebraic_tag"] = rng.choice(["negate", "double", "affine"])
            return payload
        key = rng.choice(numeric)
        value = float(payload[key])
        transform = rng.choice(["negate", "double", "half", "affine"])
        if transform == "negate":
            value = -value
        elif transform == "double":
            value *= 2.0
        elif transform == "half":
            value *= 0.5
        else:
            value = 2.0 * value + rng.choice([-1.0, 1.0])
        value = max(-1e9, min(1e9, value if math.isfinite(value) else 0.0))
        payload[key] = int(round(value)) if isinstance(payload[key], int) else value
        payload["last_algebraic_transform"] = transform
        return payload

    def representation(self, payload: dict[str, Any], rng: random.Random) -> dict[str, Any]:
        current = str(payload.get("representation", "direct"))
        choices = ["direct", "factorized", "graph", "polynomial"]
        alternatives = [x for x in choices if x != current]
        payload["representation"] = rng.choice(alternatives)
        return payload

    @staticmethod
    def sample_level(rng: random.Random) -> MutationLevel:
        # Favor cheap/local exploration while keeping non-zero probability for leaps.
        return rng.choices(
            list(MutationLevel),
            weights=[0.50, 0.25, 0.15, 0.10],
            k=1,
        )[0]
