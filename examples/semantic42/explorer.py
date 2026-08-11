"""Deterministic v0.3 Explorer protocol demo.

A real Explorer can replace this script with an OpenAI/Claude/Gemini/local-model
wrapper. ResearchEvolve only depends on the JSON stdin/stdout contract.
"""

from __future__ import annotations

import json
import sys


def _score(item: dict) -> float:
    value = item.get("score")
    return -1e30 if value is None else float(value)


def main() -> int:
    request = json.load(sys.stdin)
    count = max(0, int(request.get("count", 1)))
    context = request.get("context", {})
    candidates = context.get("candidates", [])
    if not candidates or count == 0:
        print(json.dumps({"proposals": []}))
        return 0

    parent = max(candidates, key=_score)
    parent_id = str(parent["id"])
    proposals = []
    targets = [42, 41]
    for target in targets[:count]:
        proposals.append(
            {
                "kind": "semantic_mutation",
                "parent_ids": [parent_id],
                "patch": {
                    "set": {"x": target, "representation": "semantic-guided"},
                    "delete": [],
                    "append": {},
                },
                "genome": {
                    "representation": "direct numeric target",
                    "construction": "goal-directed parameter hypothesis",
                    "mechanisms": ["infer objective target", "replace high-impact parameter"],
                    "invariants": ["candidate remains JSON-evaluable"],
                    "assumptions": ["distance objective is centered near 42 in this demo"],
                    "tags": ["semantic-mutation", "demo"],
                    "traits": {"target": target},
                    "notes": "Demonstrates the same structured contract an LLM Explorer must emit."
                },
                "rationale": f"Move x directly toward the inferred target; test x={target}.",
                "expected_effects": {"distance_to_42": "decrease"},
                "confidence": 0.95,
                "metadata": {"provider": "deterministic-demo", "model": "none"}
            }
        )

    print(json.dumps({"proposals": proposals}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
