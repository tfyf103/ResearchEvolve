from __future__ import annotations

import json
import sys

request = json.load(sys.stdin)
if request.get("action") != "repair_lean4":
    raise SystemExit("unexpected action")
context = request.get("context", {})
metadata = context.get("metadata", {})
variant = int(metadata.get("search_variant", 0))
premises = context.get("retrieved_premises", [])
names = {str(item.get("name", "")) for item in premises}
if "FormalSearch42.distance_nonnegative" not in names:
    response = {"proof_term":"by exact 0","helper_source":"","metadata":{"provider":"deterministic-v0.8-demo","repair":"required transitive premise not retrieved"}}
elif variant == 0:
    response = {"proof_term":"by exact FormalSearch42.nat_reflexive d","helper_source":"","metadata":{"provider":"deterministic-v0.8-demo","repair":"deliberately wrong beam branch"}}
else:
    response = {"proof_term":"by exact FormalSearch42.distance_nonnegative d","helper_source":"","metadata":{"provider":"deterministic-v0.8-demo","repair":"successful transitive-premise branch"}}
print(json.dumps(response))
