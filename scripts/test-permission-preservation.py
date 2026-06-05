#!/usr/bin/env python3
"""Regression test for preserving executable modes through aggregate builds."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(f"permission test error: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_build_module():
    module_path = Path(__file__).with_name("build-aggregate-bundles.py")
    spec = importlib.util.spec_from_file_location("build_aggregate_bundles", module_path)
    if spec is None or spec.loader is None:
        fail(f"failed to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add_zip_member(archive: zipfile.ZipFile, name: str, data: bytes, mode: int) -> None:
    info = zipfile.ZipInfo(name)
    info.external_attr = (mode & 0o7777) << 16
    archive.writestr(info, data)


def make_source_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        add_zip_member(archive, "synthetic-tool/bin/tool", b"#!/bin/sh\nexit 0\n", 0o755)
        add_zip_member(archive, "synthetic-tool/share/readme.txt", b"readme\n", 0o644)


def write_source_assets(path: Path, source_zip: Path) -> None:
    digest = sha256(source_zip)
    data = {
        "schema_version": "1.0",
        "generated_at": "2026-06-05T00:00:00Z",
        "platforms": ["linux-x64"],
        "apps": [
            {
                "id": "synthetic",
                "repo": "nozzle-io/synthetic",
                "required": True,
                "release": {
                    "tag": "latest",
                    "url": "https://github.com/nozzle-io/synthetic/releases/tag/latest",
                    "api_url": "https://api.github.com/repos/nozzle-io/synthetic/releases/tags/latest",
                    "prerelease": True,
                    "target_commitish": "main",
                    "source_manifest_path": "synthetic/source-manifest.json",
                    "source_sha256s_path": "synthetic/SHA256SUMS",
                    "source_commit_sha": "a" * 40,
                    "nozzle_core_sha": "b" * 40,
                },
                "platforms": {
                    "linux-x64": {
                        "status": "present",
                        "required": True,
                        "asset_name": source_zip.name,
                        "asset_path": str(source_zip),
                        "download_url": "https://example.invalid/synthetic.zip",
                        "api_url": "https://api.example.invalid/synthetic.zip",
                        "sha256": digest,
                    }
                },
            }
        ],
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def extract_with_unzip(zip_path: Path, destination: Path) -> None:
    unzip = shutil.which("unzip")
    if unzip is None:
        fail("unzip is required for permission preservation regression test")
    subprocess.run([unzip, "-q", str(zip_path), "-d", str(destination)], check=True)


def main() -> int:
    build_module = load_build_module()
    with tempfile.TemporaryDirectory(prefix="nozzle-apps-permission-test-") as tmp:
        root = Path(tmp)
        source_zip = root / "synthetic-source.zip"
        source_assets = root / "source-assets.json"
        output_dir = root / "dist"
        make_source_zip(source_zip)
        write_source_assets(source_assets, source_zip)
        build_module.build(source_assets, output_dir, "nozzle-apps-test")

        aggregate_zip = output_dir / "nozzle-apps-test-linux-x64.zip"
        member_name = "nozzle-apps/apps/synthetic/synthetic-tool/bin/tool"
        with zipfile.ZipFile(aggregate_zip) as archive:
            try:
                member = archive.getinfo(member_name)
            except KeyError as exc:
                raise SystemExit(f"missing aggregate member: {member_name}") from exc
            mode = (member.external_attr >> 16) & 0o7777
            if mode & 0o111 == 0:
                fail(f"{member_name}: aggregate zip member is not executable, mode={mode:o}")

        extracted = root / "extracted"
        extract_with_unzip(aggregate_zip, extracted)
        tool = extracted / member_name
        if not tool.is_file():
            fail(f"missing extracted executable: {tool}")
        if not os.access(tool, os.X_OK):
            fail(f"extracted executable is not +x: {tool}")
        print(f"permission preservation OK: {member_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
