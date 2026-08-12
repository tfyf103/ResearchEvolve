from __future__ import annotations

from typing import Any


ROLE_REQUIRED: dict[str, dict[str, type]] = {
    "explorer": {"proposals": list},
    "conjecturer": {"conjectures": list},
    "proof-planner": {"strategy": str, "lemmas": list},
    "prover": {"lemma_arguments": dict, "final_argument": str, "assumptions_used": list},
    "proof-reviewer": {"decision": str, "issues": list, "confidence": (int, float), "adversarial_notes": str},
    "formalizer": {"proof_term": str},
    "formal-repairer": {"proof_term": str},
    "tactic-generator": {"candidates": list},
}


def validate_actor_response(role: str, response: Any) -> dict[str, Any]:
    if role not in ROLE_REQUIRED:
        raise ValueError(f"unsupported actor role: {role}")
    if not isinstance(response, dict):
        raise ValueError("actor response must be a JSON object")
    for field, expected in ROLE_REQUIRED[role].items():
        if field not in response or not isinstance(response[field], expected):
            names = expected.__name__ if isinstance(expected, type) else " or ".join(item.__name__ for item in expected)
            raise ValueError(f"{role} response field {field!r} must be {names}")
    if role == "proof-reviewer" and response["decision"] not in {"verified", "rejected", "inconclusive"}:
        raise ValueError("proof-reviewer decision must be verified, rejected, or inconclusive")
    if role == "proof-reviewer" and not 0 <= float(response["confidence"]) <= 1:
        raise ValueError("proof-reviewer confidence must be between 0 and 1")
    return response
