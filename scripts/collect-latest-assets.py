#!/usr/bin/env python3
"""Collect latest source app release assets for nozzle-apps aggregation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app_manifest import load_manifest

API_ROOT = "https://api.github.com"


def fail(message: str) -> None:
    raise SystemExit(f"collect error: {message}")


def request_json(url: str, token: str | None) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "nozzle-apps-aggregator",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        fail(f"GitHub API request failed: {url}: HTTP {exc.code}: {body}")


def download(url: str, output: Path, token: str | None) -> None:
    headers = {"User-Agent": "nozzle-apps-aggregator"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    output.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers=headers)
    with urlopen(request) as response, output.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asset_by_name(assets: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [asset for asset in assets if asset.get("name") == name]
    if len(matches) != 1:
        fail(f"expected exactly one release asset named {name}, found {len(matches)}")
    return matches[0]


def parse_sha256s(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 2:
            fail(f"malformed SHA256SUMS line in {path}: {line}")
        digest = parts[0]
        name = parts[-1].lstrip("*")
        values[Path(name).name] = digest
    return values


def source_manifest_asset(source_manifest: dict[str, Any], asset_name: str) -> dict[str, Any]:
    assets = source_manifest.get("assets")
    if not isinstance(assets, list):
        fail("source manifest missing assets array")
    matches = [item for item in assets if isinstance(item, dict) and item.get("name") == asset_name]
    if len(matches) != 1:
        fail(f"source manifest expected exactly one asset named {asset_name}, found {len(matches)}")
    return matches[0]


def verify_source_asset(asset_path: Path, source_manifest: dict[str, Any], source_sums: dict[str, str]) -> str:
    name = asset_path.name
    actual = sha256(asset_path)
    manifest_asset = source_manifest_asset(source_manifest, name)
    manifest_sha = manifest_asset.get("sha256")
    if manifest_sha != actual:
        fail(f"{name} SHA256 mismatch against source manifest: actual={actual} manifest={manifest_sha}")
    sums_sha = source_sums.get(name)
    if sums_sha != actual:
        fail(f"{name} SHA256 mismatch against source SHA256SUMS: actual={actual} sums={sums_sha}")
    return actual


def collect(manifest_path: Path, download_dir: Path, output_path: Path, token: str | None) -> None:
    config = load_manifest(manifest_path)
    platforms = config["platforms"]
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "platforms": platforms,
        "apps": [],
    }

    for app in config["apps"]:
        app_id = app["id"]
        repo = app["repo"]
        release = request_json(f"{API_ROOT}/repos/{repo}/releases/tags/latest", token)
        if release.get("tag_name") != "latest":
            fail(f"{app_id}: expected latest tag release, got {release.get('tag_name')}")
        if release.get("prerelease") is not True:
            fail(f"{app_id}: latest release must be prerelease")
        assets = release.get("assets")
        if not isinstance(assets, list):
            fail(f"{app_id}: release assets must be an array")

        manifest_release_asset = asset_by_name(assets, "manifest.json")
        sums_release_asset = asset_by_name(assets, "SHA256SUMS")
        app_dir = download_dir / app_id
        source_manifest_path = app_dir / "source-manifest.json"
        source_sums_path = app_dir / "source-SHA256SUMS"
        download(manifest_release_asset["browser_download_url"], source_manifest_path, token)
        download(sums_release_asset["browser_download_url"], source_sums_path, token)
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        source_sums = parse_sha256s(source_sums_path)

        app_result: dict[str, Any] = {
            "id": app_id,
            "repo": repo,
            "required": app["required"],
            "release": {
                "tag": release["tag_name"],
                "url": release["html_url"],
                "api_url": release["url"],
                "prerelease": release["prerelease"],
                "target_commitish": release.get("target_commitish"),
                "source_manifest_path": str(source_manifest_path),
                "source_sha256s_path": str(source_sums_path),
                "source_commit_sha": source_manifest.get("source_commit_sha"),
                "nozzle_core_sha": source_manifest.get("nozzle_core_sha"),
            },
            "platforms": {},
        }

        for platform in platforms:
            pattern = app["assets"].get(platform)
            if not pattern:
                app_result["platforms"][platform] = {
                    "status": "optional_missing" if not app["required"] else "missing_rule",
                    "required": bool(app["required"]),
                }
                if app["required"]:
                    fail(f"{app_id}: required app has no asset rule for {platform}")
                continue
            regex = re.compile(pattern)
            matched = [asset for asset in assets if isinstance(asset.get("name"), str) and regex.fullmatch(asset["name"])]
            if len(matched) != 1:
                fail(f"{app_id}/{platform}: expected exactly one asset matching {pattern}, found {len(matched)}")
            release_asset = matched[0]
            asset_name = release_asset["name"]
            asset_path = app_dir / platform / asset_name
            download(release_asset["browser_download_url"], asset_path, token)
            digest = verify_source_asset(asset_path, source_manifest, source_sums)
            manifest_asset = source_manifest_asset(source_manifest, asset_name)
            app_result["platforms"][platform] = {
                "status": "present",
                "required": True,
                "asset_name": asset_name,
                "asset_path": str(asset_path),
                "download_url": release_asset["browser_download_url"],
                "api_url": release_asset["url"],
                "sha256": digest,
                "source_manifest_asset": manifest_asset,
            }
        result["apps"].append(app_result)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect source app latest release assets")
    parser.add_argument("manifest", nargs="?", default="apps.yml")
    parser.add_argument("--download-dir", default="build/source")
    parser.add_argument("--output", default="build/source-assets.json")
    parser.add_argument("--token", default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    args = parser.parse_args()
    collect(Path(args.manifest), Path(args.download_dir), Path(args.output), args.token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
