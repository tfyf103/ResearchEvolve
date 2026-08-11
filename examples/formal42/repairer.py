"""Deterministic v0.6 Lean repairer demo.

It consumes the failed kernel result and returns a replacement proof term while
leaving the frozen theorem target untouched.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    request = json.load(sys.stdin)
    result = request.get("kernel_result", {})
    if result.get("passed"):
        raise SystemExit("repairer should only be called after a failed kernel run")
    print(
        json.dumps(
            {
                "proof_term": "by exact Nat.zero_le _",
                "helper_source": "",
                "metadata": {
                    "provider": "deterministic-demo",
                    "repair": "replace reflexivity with Nat.zero_le"
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
