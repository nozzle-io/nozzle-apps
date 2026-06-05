#!/usr/bin/env python3
"""Validate nozzle-apps apps.yml without contacting GitHub releases."""

from __future__ import annotations

import argparse
from pathlib import Path

from app_manifest import load_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate nozzle-apps apps.yml")
    parser.add_argument("manifest", nargs="?", default="apps.yml")
    args = parser.parse_args()
    data = load_manifest(Path(args.manifest))
    print(f"OK: {len(data['apps'])} apps, {len(data['platforms'])} platforms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
