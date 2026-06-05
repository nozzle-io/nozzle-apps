#!/usr/bin/env python3
"""Build nozzle-apps platform aggregate bundles from collected source assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


def fail(message: str) -> None:
    raise SystemExit(f"bundle error: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_sha() -> str | None:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        return None


def workflow_run_url() -> str | None:
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return None


def archive_strip_prefix(archive: zipfile.ZipFile) -> tuple[str, ...]:
    """Return a single top-level directory prefix to strip, or empty tuple.

    Standalone app zips are packaged as:

        app-latest-<sha>-<platform>/<payload>

    The aggregate bundle already places each source under apps/<app_id>/, so
    preserving that release-name directory creates a useless extra nesting
    level. Only strip when every non-empty member is under one common top-level
    directory; multi-root zips are extracted as-is.
    """
    top_levels: set[str] = set()
    for member in archive.infolist():
        member_path = PurePosixPath(member.filename)
        if member_path.is_absolute() or not member_path.parts or ".." in member_path.parts:
            fail(f"unsafe zip member: {member.filename}")
        top_levels.add(member_path.parts[0])
    if len(top_levels) != 1:
        return ()
    only = next(iter(top_levels))
    for member in archive.infolist():
        member_path = PurePosixPath(member.filename)
        if len(member_path.parts) == 1 and not member.is_dir():
            return ()
    return (only,)


def safe_extract(zip_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        strip_prefix = archive_strip_prefix(archive)
        for member in archive.infolist():
            member_path = PurePosixPath(member.filename)
            if member_path.is_absolute() or not member_path.parts or ".." in member_path.parts:
                fail(f"unsafe zip member in {zip_path}: {member.filename}")
            target_parts = member_path.parts
            if strip_prefix and target_parts[:len(strip_prefix)] == strip_prefix:
                target_parts = target_parts[len(strip_prefix):]
            if not target_parts:
                continue
            target = destination.joinpath(*target_parts)
            mode = (member.external_attr >> 16) & 0o7777
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                if mode != 0:
                    target.chmod(mode)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            if mode != 0:
                target.chmod(mode)


def zip_dir(source_dir: Path, output_zip: Path) -> None:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source_dir.parent).as_posix())


def write_bundle_sums(root: Path) -> None:
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "SHA256SUMS":
            continue
        lines.append(f"{sha256(path)}  {path.relative_to(root).as_posix()}")
    if not lines:
        fail(f"no bundle files to checksum under {root}")
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def platform_entries(collected: dict[str, Any], platform: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for app in collected["apps"]:
        platform_info = app["platforms"].get(platform)
        if not platform_info:
            if app["required"]:
                fail(f"missing platform record for required app {app['id']}/{platform}")
            continue
        if platform_info["status"] == "optional_missing":
            continue
        if platform_info["status"] != "present":
            fail(f"unexpected missing source asset for {app['id']}/{platform}: {platform_info['status']}")
        entries.append({
            "app_id": app["id"],
            "source_repo": app["repo"],
            "source_app_required": app["required"],
            "source_release_tag": app["release"]["tag"],
            "source_release_url": app["release"]["url"],
            "source_release_api_url": app["release"]["api_url"],
            "source_release_prerelease": app["release"]["prerelease"],
            "source_release_target_commit": app["release"].get("target_commitish"),
            "source_commit_sha": app["release"].get("source_commit_sha"),
            "nozzle_core_sha": app["release"].get("nozzle_core_sha"),
            "source_asset_name": platform_info["asset_name"],
            "source_asset_path": platform_info["asset_path"],
            "source_asset_download_url": platform_info["download_url"],
            "source_asset_api_url": platform_info["api_url"],
            "source_asset_sha256": platform_info["sha256"],
        })
    return entries


def build(collected_path: Path, output_dir: Path, asset_prefix: str) -> None:
    collected = json.loads(collected_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "work"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    aggregate_sha = git_sha()
    run_url = workflow_run_url()
    release_manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "release_type": "latest",
        "aggregate_repo": os.environ.get("GITHUB_REPOSITORY", "nozzle-io/nozzle-apps"),
        "aggregate_commit_sha": aggregate_sha,
        "workflow_run_url": run_url,
        "generated_at": generated_at,
        "source_collection": collected,
        "bundles": [],
    }

    final_sums: list[str] = []
    for platform in collected["platforms"]:
        entries = platform_entries(collected, platform)
        if not entries:
            fail(f"no source entries for platform {platform}")
        bundle_name = f"{asset_prefix}-{platform}.zip"
        root = work_dir / platform / "nozzle-apps"
        apps_dir = root / "apps"
        apps_dir.mkdir(parents=True)

        for entry in entries:
            app_dir = apps_dir / entry["app_id"]
            app_dir.mkdir(parents=True)
            asset_path = Path(entry["source_asset_path"])
            if not asset_path.is_file():
                fail(f"missing downloaded source asset: {asset_path}")
            if sha256(asset_path) != entry["source_asset_sha256"]:
                fail(f"source asset changed after collection: {asset_path}")
            safe_extract(asset_path, app_dir)

        bundle_manifest = {
            "schema_version": "1.0",
            "release_type": "latest",
            "aggregate_repo": os.environ.get("GITHUB_REPOSITORY", "nozzle-io/nozzle-apps"),
            "aggregate_commit_sha": aggregate_sha,
            "workflow_run_url": run_url,
            "generated_at": generated_at,
            "platform": platform,
            "platform_bundle_name": bundle_name,
            "platform_bundle_sha256": None,
            "platform_bundle_sha256_note": "The final zip SHA256 is recorded in the release-level manifest.json beside the aggregate release assets.",
            "apps": entries,
        }
        (root / "manifest.json").write_text(json.dumps(bundle_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_bundle_sums(root)

        bundle_path = output_dir / bundle_name
        zip_dir(root, bundle_path)
        bundle_sha = sha256(bundle_path)
        final_sums.append(f"{bundle_sha}  {bundle_name}")
        release_manifest["bundles"].append({
            "platform": platform,
            "name": bundle_name,
            "sha256": bundle_sha,
            "apps": entries,
        })
        print(f"built {bundle_path} {bundle_sha}")

    (output_dir / "manifest.json").write_text(json.dumps(release_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_sha = sha256(output_dir / "manifest.json")
    final_sums.append(f"{manifest_sha}  manifest.json")
    (output_dir / "SHA256SUMS").write_text("\n".join(final_sums) + "\n", encoding="utf-8")
    print(f"wrote {output_dir / 'manifest.json'}")
    print(f"wrote {output_dir / 'SHA256SUMS'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build nozzle-apps aggregate bundles")
    parser.add_argument("source_assets", nargs="?", default="build/source-assets.json")
    parser.add_argument("--source-dir", default="build/source", help="Reserved for diagnostics; source paths are read from source_assets")
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--asset-prefix", default=None)
    args = parser.parse_args()
    short_sha = (git_sha() or "unknown")[:7]
    asset_prefix = args.asset_prefix or f"nozzle-apps-latest-{short_sha}"
    build(Path(args.source_assets), Path(args.output_dir), asset_prefix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
