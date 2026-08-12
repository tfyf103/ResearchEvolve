import sys

from research_evolve.plugin.cli import main

if __name__ == "__main__":
    root_args: list[str] = []
    if len(sys.argv) >= 3 and sys.argv[1] == "--root":
        root_args = sys.argv[1:3]
        del sys.argv[1:3]
    sys.argv[1:1] = [*root_args, "create"]
    raise SystemExit(main())
