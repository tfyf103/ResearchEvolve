from __future__ import annotations

import json
import sys

request = json.load(sys.stdin)
assert request.get("action") == "repair_lean4"
premises = request.get("context", {}).get("retrieved_premises", [])
names = {str(item.get("name", "")) for item in premises}
required = "FormalProject42.distance_nonnegative"
if required not in names:
    raise SystemExit(f"required retrieved premise missing: {required}; got={sorted(names)}")
json.dump(
    {
        "proof_term": "by exact FormalProject42.distance_nonnegative d",
        "helper_source": "",
        "metadata": {
            "provider": "deterministic-demo",
            "repair": "use retrieved frozen-project premise FormalProject42.distance_nonnegative",
        },
    },
    sys.stdout,
)
