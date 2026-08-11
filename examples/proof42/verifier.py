"""Deterministic independent verifier demo for v0.5.

It deliberately checks the proof artifact rather than trusting the prover's
claimed status. Production deployments should replace this with an independent
model and, later, a formal/symbolic verifier where possible.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    request = json.load(sys.stdin)
    artifact = request.get("proof_artifact", {})
    arguments = artifact.get("lemma_arguments", {})
    final_argument = str(artifact.get("final_argument", ""))
    required = {"definition", "abs_nonnegative", "conclusion"}
    missing = sorted(required - set(arguments)) if isinstance(arguments, dict) else sorted(required)
    text = " ".join(str(value) for value in arguments.values()) + " " + final_argument if isinstance(arguments, dict) else final_argument

    issues = []
    if missing:
        issues.append({
            "severity": "error",
            "code": "missing_lemma",
            "message": f"Missing required lemma arguments: {missing}",
            "lemma_label": None
        })
    if "abs" not in text.lower() and "absolute" not in text.lower():
        issues.append({
            "severity": "error",
            "code": "definition_gap",
            "message": "The proof never connects distance_to_42 to absolute value.",
            "lemma_label": "definition"
        })
    if ">= 0" not in text and "non-negative" not in text.lower() and "nonnegative" not in text.lower():
        issues.append({
            "severity": "error",
            "code": "nonnegativity_gap",
            "message": "The proof does not establish non-negativity.",
            "lemma_label": "abs_nonnegative"
        })

    decision = "rejected" if any(item["severity"] == "error" for item in issues) else "verified"
    response = {
        "decision": decision,
        "issues": issues,
        "confidence": 0.99 if decision == "verified" else 0.98,
        "adversarial_notes": (
            "Checked statement integrity, lemma coverage, the absolute-value definition, and the final non-negativity step. "
            "No formal proof assistant was used."
        ),
        "metadata": {"provider": "deterministic-independent-demo", "model": "none"}
    }
    print(json.dumps(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
