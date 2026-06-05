#!/usr/bin/env python3
"""Verify aggregate bundle checksums and macOS .app executables."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(f"verify error: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sha256s(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 2:
            fail(f"malformed SHA256SUMS line in {path}: {line}")
        entries.append((parts[0], parts[-1].lstrip("*")))
    return entries


def extract_with_unzip(zip_path: Path, destination: Path) -> None:
    unzip = shutil.which("unzip")
    if unzip is None:
        fail("unzip is required to verify extracted permission bits")
    subprocess.run([unzip, "-q", str(zip_path), "-d", str(destination)], check=True)


def verify_bundle_sha256s(root: Path) -> None:
    sums = root / "SHA256SUMS"
    if not sums.is_file():
        fail(f"missing bundle-local SHA256SUMS: {sums}")
    checked = 0
    for expected, relative in parse_sha256s(sums):
        path = root / relative
        if not path.is_file():
            fail(f"{sums}: referenced path is missing: {relative}")
        actual = sha256(path)
        if actual != expected:
            fail(f"{sums}: checksum mismatch for {relative}: expected={expected} actual={actual}")
        checked += 1
    if checked == 0:
        fail(f"{sums}: no checksum entries")


def verify_macos_apps(root: Path, bundle_name: str) -> None:
    app_count = 0
    for app in root.rglob("*.app"):
        app_count += 1
        plist_path = app / "Contents" / "Info.plist"
        if not plist_path.is_file():
            fail(f"{app}: missing Contents/Info.plist")
        with plist_path.open("rb") as handle:
            info = plistlib.load(handle)
        executable = info.get("CFBundleExecutable")
        if not isinstance(executable, str) or not executable:
            fail(f"{app}: missing CFBundleExecutable")
        exe_path = app / "Contents" / "MacOS" / executable
        if not exe_path.is_file():
            fail(f"{app}: missing executable {exe_path}")
        if not os.access(exe_path, os.X_OK):
            fail(f"{app}: executable is not +x: {exe_path}")
    if "macos" in bundle_name and app_count == 0:
        fail(f"{bundle_name}: expected macOS .app bundles, found none")


def verify_no_redundant_source_roots(root: Path) -> None:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        fail(f"missing bundle manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    apps_root = root / "apps"
    for app in manifest.get("apps", []):
        app_id = app.get("app_id")
        asset_name = app.get("source_asset_name")
        if not isinstance(app_id, str) or not isinstance(asset_name, str):
            fail(f"{manifest_path}: malformed app entry")
        asset_stem = asset_name[:-4] if asset_name.endswith(".zip") else asset_name
        redundant = apps_root / app_id / asset_stem
        if redundant.exists():
            fail(f"{root}: redundant source zip root was not stripped: {redundant.relative_to(root)}")


def verify_zip(zip_path: Path) -> None:
    if not zip_path.is_file():
        fail(f"missing zip: {zip_path}")
    with zipfile.ZipFile(zip_path) as archive:
        if "nozzle-apps/SHA256SUMS" not in archive.namelist():
            fail(f"{zip_path}: missing nozzle-apps/SHA256SUMS")
    with tempfile.TemporaryDirectory(prefix="nozzle-apps-verify-") as tmp:
        extract_with_unzip(zip_path, Path(tmp))
        root = Path(tmp) / "nozzle-apps"
        if not root.is_dir():
            fail(f"{zip_path}: missing nozzle-apps root after extraction")
        verify_bundle_sha256s(root)
        verify_no_redundant_source_roots(root)
        verify_macos_apps(root, zip_path.name)
    print(f"verified {zip_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify nozzle-apps aggregate zip contents")
    parser.add_argument("zips", nargs="+", type=Path)
    args = parser.parse_args()
    for zip_path in args.zips:
        verify_zip(zip_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
