#!/usr/bin/env python3
"""CLI entry point: ``mmkg``."""

from __future__ import annotations

import sys

from . import __version__, reasoning


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("--version", "version"):
        print(__version__)
        return 0
    return reasoning.main(args)


if __name__ == "__main__":
    sys.exit(main())
