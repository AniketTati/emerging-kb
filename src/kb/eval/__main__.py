"""Entry point — `python -m kb.eval` delegates to kb.eval.cli."""

from kb.eval.cli import main


if __name__ == "__main__":
    import sys
    sys.exit(main())
