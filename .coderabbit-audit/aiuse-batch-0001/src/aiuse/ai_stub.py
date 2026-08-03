"""Compatibility stub: ``ai`` invokes the same entrypoint as ``aiuse``."""

from __future__ import annotations


def main() -> int:
    from aiuse.cli import main as aiuse_main

    return aiuse_main()


if __name__ == "__main__":
    raise SystemExit(main())
