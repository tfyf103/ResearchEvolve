"""Deterministic v0.5 proof-planner demo for the supported distance conjecture."""

from __future__ import annotations

import json
import sys


def main() -> int:
    request = json.load(sys.stdin)
    statement = str(request.get("proof_spec", {}).get("statement", ""))
    plan = {
        "strategy": "Unfold the absolute-distance definition, prove absolute values are non-negative, then conclude the target.",
        "lemmas": [
            {
                "label": "definition",
                "statement": "The evaluator metric distance_to_42 is the absolute value |x-42|.",
                "depends_on": [],
                "role": "supporting"
            },
            {
                "label": "abs_nonnegative",
                "statement": "For every real y, |y| is greater than or equal to 0.",
                "depends_on": [],
                "role": "supporting"
            },
            {
                "label": "conclusion",
                "statement": statement,
                "depends_on": ["definition", "abs_nonnegative"],
                "role": "final"
            }
        ],
        "metadata": {"provider": "deterministic-demo", "model": "none"}
    }
    print(json.dumps(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
