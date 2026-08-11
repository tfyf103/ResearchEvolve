from __future__ import annotations

import json
import sys

from research_evolve.domains.qldpc.common import code_parameters, validate_candidate


def main() -> None:
    candidate = json.load(sys.stdin)
    valid, reason = validate_candidate(candidate)
    if not valid:
        print(json.dumps({"valid": False, "diagnostics": {"reason": reason}}))
        return
    metrics = code_parameters(candidate)
    print(
        json.dumps(
            {
                "valid": True,
                "metrics": metrics,
                "behavior": {
                    "family": "bicycle",
                    "representation": candidate.get("representation", "circulant"),
                    "size": candidate["size"],
                    "density_bucket": round((metrics["row_weight_x"] + metrics["row_weight_z"]) / 2.0),
                },
                "diagnostics": {
                    "rank_hx": metrics["rank_hx"],
                    "rank_hz": metrics["rank_hz"],
                },
            }
        )
    )


if __name__ == "__main__":
    main()
