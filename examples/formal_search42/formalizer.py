from __future__ import annotations

import json
import sys

request = json.load(sys.stdin)
if request.get("action") != "formalize_lean4":
    raise SystemExit("unexpected action")
print(json.dumps({"proof_term":"by exact 0","helper_source":"","metadata":{"provider":"deterministic-v0.8-demo","purpose":"intentional first-attempt type error before goal-conditioned repair search"}}))
