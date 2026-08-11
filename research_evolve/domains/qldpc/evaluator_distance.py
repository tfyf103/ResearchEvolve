from __future__ import annotations

import json
import sys

from research_evolve.domains.qldpc.common import code_parameters, exact_distance_metrics, validate_candidate


def main() -> None:
    candidate = json.load(sys.stdin)
    valid, reason = validate_candidate(candidate)
    if not valid:
        print(json.dumps({"valid": False, "diagnostics": {"reason": reason}}))
        return

    parameters = code_parameters(candidate)
    distance_metrics = exact_distance_metrics(candidate)
    if distance_metrics is None:
        print(
            json.dumps(
                {
                    "valid": False,
                    "metrics": parameters,
                    "diagnostics": {"reason": "candidate encodes no logical qubits or distance could not be resolved"},
                }
            )
        )
        return

    distance = distance_metrics["distance"]
    k = parameters["k"]
    rate = parameters["rate"]
    average_row_weight = (parameters["row_weight_x"] + parameters["row_weight_z"]) / 2.0
    score = distance + 0.15 * k + 2.0 * rate - 0.01 * average_row_weight
    print(
        json.dumps(
            {
                "valid": True,
                "score": score,
                "metrics": distance_metrics,
                "diagnostics": {"distance_method": "exact_weight_enumeration", "benchmark_only": True},
            }
        )
    )


if __name__ == "__main__":
    main()
