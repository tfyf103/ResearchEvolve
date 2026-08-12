from __future__ import annotations

import json
import sys

request = json.load(sys.stdin)
assert request.get("action") == "formalize_lean4"
formal_spec = request["formal_spec"]
assert formal_spec["metadata"]["project_fingerprint"]
json.dump(
    {
        "proof_term": "by exact 0",
        "helper_source": "",
        "metadata": {
            "provider": "deterministic-demo",
            "purpose": "intentional first-attempt type error before premise-driven repair",
        },
    },
    sys.stdout,
)
