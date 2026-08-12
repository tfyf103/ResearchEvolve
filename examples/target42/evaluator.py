"""Demo evaluator.

Reads one candidate JSON object from stdin and prints one EvaluationResult JSON object.
A real deployment should place this file outside the agent-visible filesystem.
"""

from __future__ import annotations

import json
import math
import sys


def main() -> int:
    candidate = json.load(sys.stdin)
    x = candidate.get("x")
    if not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(float(x)):
        print(json.dumps({"valid": False, "score": None, "diagnostics": {"reason": "x must be finite numeric"}}))
        return 0

    x = float(x)
    distance = abs(x - 42.0)
    score = -distance
    magnitude_bucket = "small" if abs(x) < 20 else "medium" if abs(x) < 80 else "large"
    # Preserve the exact Nat codomain promised by the v1.0 semantic registry.
    # Non-integral search points remain valid but are deliberately unsupported
    # for formal certification and will fail the independent semantic audit.
    certified_distance = int(distance) if distance.is_integer() else distance
    result = {
        "valid": True,
        "score": score,
        "metrics": {"distance_to_42": certified_distance, "x": x},
        "behavior": {
            "representation": str(candidate.get("representation", "direct")),
            "magnitude_bucket": magnitude_bucket
        },
        "diagnostics": {}
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
