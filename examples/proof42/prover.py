"""Deterministic v0.5 prover demo.

This script demonstrates the protocol only. A real prover can wrap an LLM or a
symbolic prover, but still has to emit the same structured artifact.
"""

from __future__ import annotations

import json
import sys


DISTANCE_DEFINITION = "For every evaluated numeric candidate x, distance_to_42 is defined as abs(x - 42)."


def main() -> int:
    request = json.load(sys.stdin)
    plan = request.get("proof_plan", {})
    proof_spec = request.get("proof_spec", {})
    labels = [str(item.get("label")) for item in plan.get("lemmas", [])]
    assumptions = [str(item) for item in proof_spec.get("assumptions", [])]
    if DISTANCE_DEFINITION not in assumptions:
        print(json.dumps({
            "lemma_arguments": {},
            "final_argument": "",
            "assumptions_used": [DISTANCE_DEFINITION],
            "metadata": {"error": "required frozen distance definition is missing"}
        }))
        return 0

    arguments = {
        "definition": "By the frozen ProofSpec assumption, distance_to_42 = abs(x - 42) for every evaluated numeric candidate x.",
        "abs_nonnegative": "For any real number y, abs(y) >= 0 by the defining property of absolute value.",
        "conclusion": "Substitute y = x - 42. Then distance_to_42 = abs(x - 42) >= 0 for every evaluated numeric x."
    }
    response = {
        "lemma_arguments": {label: arguments.get(label, f"Argument for {label}.") for label in labels},
        "final_argument": (
            "Using the frozen distance definition, distance_to_42 = abs(x - 42). "
            "Absolute value is non-negative for every real input, therefore distance_to_42 >= 0."
        ),
        "assumptions_used": [DISTANCE_DEFINITION],
        "metadata": {"provider": "deterministic-demo", "model": "none"}
    }
    print(json.dumps(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
