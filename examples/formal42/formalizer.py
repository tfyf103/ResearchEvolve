"""Deterministic v0.6 Formalizer demo.

The first proof term is deliberately ill-typed so CI exercises the real Lean
diagnostic and repair loop. The theorem signature itself is supplied by the
frozen formal contract and cannot be changed here.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    request = json.load(sys.stdin)
    formal_spec = request.get("formal_spec", {})
    if formal_spec.get("theorem_name") != "distance_to_42_nonnegative":
        raise SystemExit("unexpected formal target")
    print(
        json.dumps(
            {
                "proof_term": "by exact 0",
                "helper_source": "",
                "metadata": {
                    "provider": "deterministic-demo",
                    "purpose": "intentional first-attempt type error"
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
