from __future__ import annotations

import math
import random
from typing import Any

from ...mutation import FourLevelMutator
from .common import MAX_BENCHMARK_SIZE


class QLDPCMutator(FourLevelMutator):
    """Four mutation scales for the small circulant bicycle benchmark."""

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        size = int(payload.get("size", 5))
        size = max(3, min(MAX_BENCHMARK_SIZE, size))
        payload["size"] = size
        payload["family"] = "bicycle"
        payload["representation"] = str(payload.get("representation", "circulant"))
        if payload["representation"] not in {"circulant", "polynomial"}:
            payload["representation"] = "circulant"
        for key in ("a_shifts", "b_shifts"):
            raw = payload.get(key, [0])
            shifts = sorted({int(value) % size for value in raw})
            payload[key] = shifts or [0]
        return payload

    def local(self, payload: dict[str, Any], rng: random.Random) -> dict[str, Any]:
        payload = self._normalize(payload)
        key = rng.choice(["a_shifts", "b_shifts"])
        shifts = list(payload[key])
        index = rng.randrange(len(shifts))
        step = rng.choice([-1, 1])
        shifts[index] = (shifts[index] + step) % payload["size"]
        payload[key] = sorted(set(shifts)) or [0]
        return self._normalize(payload)

    def structural(self, payload: dict[str, Any], rng: random.Random) -> dict[str, Any]:
        payload = self._normalize(payload)
        if rng.random() < 0.30:
            choices = [size for size in range(3, MAX_BENCHMARK_SIZE + 1) if size != payload["size"]]
            payload["size"] = rng.choice(choices)
            return self._normalize(payload)

        key = rng.choice(["a_shifts", "b_shifts"])
        shifts = list(payload[key])
        size = payload["size"]
        if len(shifts) > 1 and rng.random() < 0.5:
            del shifts[rng.randrange(len(shifts))]
        else:
            available = [shift for shift in range(size) if shift not in shifts]
            if available:
                shifts.append(rng.choice(available))
        payload[key] = sorted(shifts)
        return self._normalize(payload)

    def algebraic(self, payload: dict[str, Any], rng: random.Random) -> dict[str, Any]:
        payload = self._normalize(payload)
        size = payload["size"]
        units = [value for value in range(1, size) if math.gcd(value, size) == 1]
        multiplier = rng.choice(units)
        translation = rng.randrange(size)
        for key in ("a_shifts", "b_shifts"):
            payload[key] = sorted({(multiplier * shift + translation) % size for shift in payload[key]})
        payload["algebraic_transform"] = {"multiplier": multiplier, "translation": translation}
        return self._normalize(payload)

    def representation(self, payload: dict[str, Any], rng: random.Random) -> dict[str, Any]:
        payload = self._normalize(payload)
        payload["representation"] = "polynomial" if payload["representation"] == "circulant" else "circulant"
        return payload
