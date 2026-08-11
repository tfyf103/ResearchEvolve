"""Deterministic v0.4 Conjecturer protocol demo.

A real conjecturer may wrap an LLM or symbolic system. This script intentionally
emits one false and one durable empirical conjecture so CI exercises both
refutation and empirical-support paths without an API key.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    request = json.load(sys.stdin)
    count = max(0, int(request.get("count", 1)))
    context = request.get("context", {})
    observations = context.get("observations", [])
    candidates = context.get("candidates", [])
    observation_ids = [str(item["id"]) for item in observations[:2] if "id" in item]
    evidence_ids = [str(item["id"]) for item in candidates[:2] if "id" in item]

    proposals = [
        {
            "statement": "Every observed candidate has strictly negative canonical score.",
            "predicate": {
                "left": {"source": "score"},
                "operator": "lt",
                "right_constant": 0
            },
            "observation_ids": observation_ids,
            "evidence_candidate_ids": evidence_ids,
            "parent_conjecture_ids": [],
            "rationale": "A deliberately over-strong empirical generalization used to demonstrate counterexample refutation.",
            "confidence": 0.6,
            "metadata": {"demo_kind": "expected-refutation"}
        },
        {
            "statement": "Distance to 42 is always non-negative for evaluated candidates.",
            "predicate": {
                "left": {"source": "metrics", "key": "distance_to_42"},
                "operator": "ge",
                "right_constant": 0
            },
            "observation_ids": observation_ids,
            "evidence_candidate_ids": evidence_ids,
            "parent_conjecture_ids": [],
            "rationale": "The evaluator reports an absolute distance, so the observed metric should remain non-negative.",
            "confidence": 0.95,
            "metadata": {"demo_kind": "expected-support"}
        }
    ]
    print(json.dumps({"conjectures": proposals[:count]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
