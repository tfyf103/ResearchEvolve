from __future__ import annotations

import json
import sys


request = json.load(sys.stdin)
assert request.get("action") == "propose_lean_tactics"
state = request["proof_state"]
premises = {item["name"] for item in request["retrieved_premises"]}
target = state["goals"][0]["target"]

candidates = []
if "FormalProject42.distance_nonnegative" in premises:
    candidates.append({
        "tactic": "exact FormalProject42.distance_nonnegative d",
        "confidence": 1.0,
        "premise_names": ["FormalProject42.distance_nonnegative"],
        "rationale": f"The frozen premise directly closes {target}.",
    })
json.dump({"candidates": candidates}, sys.stdout)

