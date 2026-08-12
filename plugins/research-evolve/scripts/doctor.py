import sys

from research_evolve.plugin.cli import main

if __name__ == "__main__":
    sys.argv.insert(3 if len(sys.argv) >= 3 and sys.argv[1] == "--root" else 1, "doctor")
    raise SystemExit(main())
