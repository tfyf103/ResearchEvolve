from __future__ import annotations

import json
import sys

from research_evolve.domains.qldpc.common import build_css_rows, css_commutes, validate_candidate


def main() -> None:
    candidate = json.load(sys.stdin)
    valid, reason = validate_candidate(candidate)
    if not valid:
        print(json.dumps({"valid": False, "diagnostics": {"reason": reason}}))
        return
    hx, hz, _ = build_css_rows(candidate)
    commutes = css_commutes(hx, hz)
    print(
        json.dumps(
            {
                "valid": commutes,
                "metrics": {"css_commutes": 1.0 if commutes else 0.0},
                "behavior": {
                    "family": "bicycle",
                    "representation": candidate.get("representation", "circulant"),
                    "size": candidate["size"],
                },
                "diagnostics": {} if commutes else {"reason": "Hx Hz^T != 0 over GF(2)"},
            }
        )
    )


if __name__ == "__main__":
    main()
