"""Independent proof-planning process boundary for the v1.2 actor gate."""

from .actor_bridge import run

ROLE = "proof-planner"

if __name__ == "__main__":
    raise SystemExit(run(ROLE))
