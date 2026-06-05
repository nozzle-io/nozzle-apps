#!/usr/bin/env python3
"""Shared manifest loading/validation for nozzle-apps."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover - exercised by CI environment setup.
    raise SystemExit("PyYAML is required. Install with: python3 -m pip install pyyaml") from exc

ALLOWED_PLATFORMS = {"macos-universal", "windows-x64", "linux-x64"}


def fail(message: str) -> None:
    raise SystemExit(f"apps.yml error: {message}")


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"failed to read YAML from {path}: {exc}")
    if not isinstance(data, dict):
        fail("root must be a mapping")
    return data


def validate_manifest(data: dict[str, Any]) -> dict[str, Any]:
    platforms = data.get("platforms")
    if not isinstance(platforms, list) or not platforms or not all(isinstance(item, str) for item in platforms):
        fail("platforms must be a non-empty string array")
    platform_set = set(platforms)
    unknown_platforms = sorted(platform_set - ALLOWED_PLATFORMS)
    if unknown_platforms:
        fail(f"unknown platforms: {', '.join(unknown_platforms)}")
    if len(platforms) != len(platform_set):
        fail("duplicate platforms are not allowed")

    apps = data.get("apps")
    if not isinstance(apps, list) or not apps:
        fail("apps must be a non-empty array")

    seen_ids: set[str] = set()
    for index, app in enumerate(apps):
        if not isinstance(app, dict):
            fail(f"apps[{index}] must be a mapping")
        app_id = app.get("id")
        if not isinstance(app_id, str) or not app_id:
            fail(f"apps[{index}].id must be a non-empty string")
        if app_id in seen_ids:
            fail(f"duplicate app id: {app_id}")
        seen_ids.add(app_id)

        repo = app.get("repo")
        if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            fail(f"{app_id}.repo must be owner/repo")

        required = app.get("required")
        if not isinstance(required, bool):
            fail(f"{app_id}.required must be boolean")

        assets = app.get("assets")
        if not isinstance(assets, dict) or not assets:
            fail(f"{app_id}.assets must be a non-empty mapping")
        asset_platforms = set(assets)
        unknown_assets = sorted(asset_platforms - platform_set)
        if unknown_assets:
            fail(f"{app_id}.assets contains platforms not declared in platforms: {', '.join(unknown_assets)}")
        unknown_allowed = sorted(asset_platforms - ALLOWED_PLATFORMS)
        if unknown_allowed:
            fail(f"{app_id}.assets contains unsupported platforms: {', '.join(unknown_allowed)}")

        if required:
            missing = sorted(platform_set - asset_platforms)
            if missing:
                fail(f"required app {app_id} is missing asset rules for: {', '.join(missing)}")

        for platform, pattern in assets.items():
            if not isinstance(pattern, str) or not pattern:
                fail(f"{app_id}.assets.{platform} must be a non-empty regex string")
            try:
                re.compile(pattern)
            except re.error as exc:
                fail(f"{app_id}.assets.{platform} regex does not compile: {exc}")

    return data


def load_manifest(path: str | Path) -> dict[str, Any]:
    return validate_manifest(load_yaml(Path(path)))
