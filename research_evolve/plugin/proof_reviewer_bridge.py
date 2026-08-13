"""Independent adversarial-review process boundary for the v1.2 actor gate."""

from .actor_bridge import run

ROLE = "proof-reviewer"

if __name__ == "__main__":
    raise SystemExit(run(ROLE))
