"""Deterministic v0.5 prover demo.

This script demonstrates the protocol only. A real prover can wrap an LLM or a
symbolic prover, but still has to emit the same structured artifact.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    request = json.load(sys.stdin)
    plan = request.get("proof_plan", {})
    labels = [str(item.get("label")) for item in plan.get("lemmas", [])]
    arguments = {
        "definition": "By the evaluator definition, distance_to_42 = abs(x - 42).",
        "abs_nonnegative": "For any real number y, abs(y) >= 0 by the defining property of absolute value.",
        "conclusion": "Substitute y = x - 42. Then distance_to_42 = abs(x - 42) >= 0 for every evaluated real x."
    }
    response = {
        "lemma_arguments": {label: arguments.get(label, f"Argument for {label}.") for label in labels},
        "final_argument": (
            "The metric is distance_to_42 = abs(x - 42). Absolute value is non-negative for every real input, "
            "therefore distance_to_42 >= 0."
        ),
        "assumptions_used": [],
        "metadata": {"provider": "deterministic-demo", "model": "none"}
    }
    print(json.dumps(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
