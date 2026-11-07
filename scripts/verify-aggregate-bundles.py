#!/usr/bin/env python3
"""Verify final aggregate checksums, macOS permissions, and Linux ELF ISA."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

from app_manifest import load_manifest


CHECKER_REVISION = "79637d4b25b13b1d7ab5751ccc7d0a0e14bc808e"
CHECKER_PATH = "scripts/check_x86_64_isa.sh"
REPO_ROOT = Path(__file__).resolve().parent.parent


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


def verify_macos_app_roots(root: Path) -> None:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        fail(f"missing bundle manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    platform = manifest.get("platform")
    if not isinstance(platform, str) or "macos" not in platform:
        return

    apps_root = root / "apps"
    for app in manifest.get("apps", []):
        app_id = app.get("app_id")
        if not isinstance(app_id, str):
            fail(f"{manifest_path}: malformed app entry")
        app_root = apps_root / app_id
        if (app_root / "Contents").exists():
            fail(f"{root}: macOS app bundle root was stripped: {app_root.relative_to(root)}/Contents")
        if not any(app_root.rglob("*.app")):
            fail(f"{root}: expected a .app bundle under {app_root.relative_to(root)}")


def pinned_checker(checker_root: Path) -> tuple[Path, str]:
    checker = checker_root.resolve() / CHECKER_PATH
    if not checker.is_file():
        fail(f"ISA checker unavailable: {checker}")
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(checker_root), "rev-parse", "HEAD"], text=True, timeout=10,
        ).strip()
        if revision != CHECKER_REVISION:
            fail(f"ISA checker revision mismatch: expected={CHECKER_REVISION} actual={revision}")
        committed = subprocess.check_output(
            ["git", "-C", str(checker_root), "show", f"{CHECKER_REVISION}:{CHECKER_PATH}"], timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        fail(f"ISA checker unavailable: {exc}")
    digest = sha256(checker)
    if digest != hashlib.sha256(committed).hexdigest():
        fail("ISA checker differs from its pinned committed source")
    return checker, digest


def verify_linux_elfs(root: Path, zip_path: Path, checker_root: Path, apps_manifest: Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    platform = manifest.get("platform")
    named_linux = zip_path.name.endswith("-linux-x64.zip")
    if not named_linux and platform != "linux-x64":
        return
    if not named_linux or platform != "linux-x64":
        fail("Linux archive filename/platform mismatch")

    config = load_manifest(apps_manifest)
    required = {app["id"] for app in config["apps"]
                if app["required"] and "linux-x64" in app["assets"]}
    entries = manifest.get("apps")
    if not isinstance(entries, list) or not entries:
        fail("Linux bundle has no app entries")
    counts: dict[str, int] = {}
    for app in entries:
        app_id = app.get("app_id") if isinstance(app, dict) else None
        if (not isinstance(app_id, str) or not app_id or app_id in {".", ".."}
                or "/" in app_id or "\\" in app_id or app_id in counts):
            fail(f"invalid or duplicate Linux app id: {app_id!r}")
        counts[app_id] = 0
    if not required.issubset(counts):
        fail(f"missing required Linux apps: {sorted(required - counts.keys())}")

    checker, checker_hash = pinned_checker(checker_root)
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            fail(f"Linux bundle symlink is not supported by the ELF gate: {relative}")
        if not path.is_file():
            continue
        with path.open("rb") as handle:
            header = handle.read(64)
        if not header.startswith(b"\x7fELF"):
            continue
        if len(header) != 64 or header[4:7] != b"\x02\x01\x01":
            fail(f"invalid ELF64 little-endian header: {relative}")
        # ELF64_Ehdr: type, machine, version, and declared header size.
        fields = struct.unpack("<16sHHIQQQIHHHHHH", header)
        if fields[1] not in (2, 3) or fields[2] != 62 or fields[3] != 1 or fields[8] != 64:
            fail(f"unsupported or invalid x86-64 ELF header: {relative}")
        print(f"ISA payload={relative.as_posix()} sha256={sha256(path)} "
              f"checker_revision={CHECKER_REVISION} checker_sha256={checker_hash}", flush=True)
        try:
            subprocess.run(["bash", str(checker), "--", str(path)], check=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            fail(f"ISA checker failed for {relative}: {exc}")
        if len(relative.parts) >= 3 and relative.parts[0] == "apps" and relative.parts[1] in counts:
            counts[relative.parts[1]] += 1
    missing = sorted(app_id for app_id, count in counts.items() if count == 0)
    if missing:
        fail(f"Linux apps missing ELF payloads: {missing}")
    print(f"Linux ELF gate passed: {counts}")


def verify_zip(zip_path: Path, checker_root: Path, apps_manifest: Path) -> None:
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
        verify_macos_app_roots(root)
        verify_macos_apps(root, zip_path.name)
        verify_linux_elfs(root, zip_path, checker_root, apps_manifest)
    print(f"verified {zip_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify nozzle-apps aggregate zip contents")
    parser.add_argument("zips", nargs="+", type=Path)
    parser.add_argument("--isa-checker-root", type=Path, default=REPO_ROOT / "build/isa-checker")
    parser.add_argument("--apps-manifest", type=Path, default=REPO_ROOT / "apps.yml")
    args = parser.parse_args()
    for zip_path in args.zips:
        verify_zip(zip_path, args.isa_checker_root, args.apps_manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
