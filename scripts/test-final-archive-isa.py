#!/usr/bin/env python3
"""CI regression through the production final-ZIP verifier, using real ELF code."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_IDS = ("nozzle-viewer", "nozzle-mixer", "nozzle-tester")


def write_bundle(path: Path, payloads: dict[str, bytes], *, app_ids=APP_IDS,
                 platform: str = "linux-x64") -> None:
    members = dict(payloads)
    members["manifest.json"] = json.dumps({
        "platform": platform,
        "apps": [{"app_id": app_id, "source_asset_name": f"{app_id}-source.zip"}
                 for app_id in app_ids],
    }).encode("utf-8")
    members["SHA256SUMS"] = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(members.items())
    ).encode("utf-8")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo(f"nozzle-apps/{name}")
            info.create_system = 3
            # Detection must not depend on executable permission or a suffix.
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isa-checker-root", required=True, type=Path)
    args = parser.parse_args()
    checker_root = args.isa_checker_root.resolve()
    compiler = shutil.which("cc")
    false_command = shutil.which("false")
    if compiler is None or false_command is None:
        raise SystemExit("CI fixtures require cc and false")

    with tempfile.TemporaryDirectory(prefix="nozzle-final-isa-") as tmp:
        root = Path(tmp)
        baseline_c = root / "baseline.c"
        baseline_c.write_text("int main(void) { return 0; }\n", encoding="utf-8")
        baseline = root / "baseline"
        subprocess.run([compiler, "-O2", "-march=x86-64-v2", "-mtune=generic",
                        str(baseline_c), "-o", str(baseline)], check=True, timeout=60)
        avx_c = root / "avx.c"
        avx_c.write_text(
            "#include <immintrin.h>\n"
            "void isa_probe(float *p) {\n"
            "  _mm256_storeu_ps(p, _mm256_add_ps(_mm256_loadu_ps(p), _mm256_set1_ps(1.0f)));\n"
            "}\n", encoding="utf-8",
        )
        avx = root / "avx.so"
        subprocess.run([compiler, "-O2", "-march=x86-64-v2", "-mavx", "-shared", "-fPIC",
                        str(avx_c), "-o", str(avx)], check=True, timeout=60)
        baseline_bytes = baseline.read_bytes()
        payloads = {f"apps/{app_id}/bin/app": baseline_bytes for app_id in APP_IDS}
        nested = "apps/nozzle-viewer/lib/deeper/manual.txt"
        payloads[nested] = baseline_bytes
        bundle = root / "nozzle-apps-fixture-linux-x64.zip"

        def check(label: str, expected: str, *, success: bool = False,
                  checker: Path = checker_root, extra_env: dict[str, str] | None = None) -> str:
            env = os.environ.copy()
            env.pop("OBJDUMP", None)
            if extra_env:
                env.update(extra_env)
            result = subprocess.run(
                [sys.executable, str(REPO_ROOT / "scripts/verify-aggregate-bundles.py"),
                 "--isa-checker-root", str(checker), str(bundle)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                env=env, timeout=60,
            )
            print(result.stdout, end="", flush=True)
            if (result.returncode == 0) != success or expected not in result.stdout:
                raise SystemExit(f"FAIL {label}: exit={result.returncode}, expected={expected!r}")
            print(f"PASS {label}", flush=True)
            return result.stdout

        write_bundle(bundle, payloads)
        check("compiled baseline ELF accepted", "Linux ELF gate passed", success=True)

        bad = dict(payloads)
        bad[nested] = avx.read_bytes()
        write_bundle(bundle, bad)
        avx_output = check("nested AVX ELF under misleading filename rejected", "ISA checker failed for " + nested)
        if "are AVX/EVEX-class or v3 integer instructions" not in avx_output:
            raise SystemExit("AVX fixture failed without the checker's forbidden-instruction diagnostic")

        bad = {name: data for name, data in payloads.items() if not name.startswith("apps/nozzle-mixer/")}
        write_bundle(bundle, bad)
        check("one app without ELF rejected", "Linux apps missing ELF payloads: ['nozzle-mixer']")
        write_bundle(bundle, bad, app_ids=("nozzle-viewer", "nozzle-tester"))
        check("omitted required app rejected", "missing required Linux apps: ['nozzle-mixer']")

        for label, offset, value, diagnostic in (
            ("wrong class", 4, b"\x01", "invalid ELF64 little-endian header"),
            ("wrong endian", 5, b"\x02", "invalid ELF64 little-endian header"),
            ("wrong machine", 18, b"\xb7\x00", "unsupported or invalid x86-64 ELF header"),
        ):
            modified = bytearray(baseline_bytes)
            modified[offset:offset + len(value)] = value
            bad = dict(payloads)
            bad[nested] = bytes(modified)
            write_bundle(bundle, bad)
            check(label + " rejected", diagnostic)
        bad = dict(payloads)
        bad[nested] = b"\x7fELF\x02\x01\x01"
        write_bundle(bundle, bad)
        check("truncated ELF rejected", "invalid ELF64 little-endian header")

        write_bundle(bundle, payloads, platform="windows-x64")
        check("Linux filename cannot bypass gate via manifest", "Linux archive filename/platform mismatch")
        write_bundle(bundle, payloads)
        check("missing checker rejected", "ISA checker unavailable", checker=root / "absent")
        check("missing disassembler rejected", "ISA checker failed for",
              extra_env={"OBJDUMP": str(root / "missing-objdump")})
        check("checker tool failure propagated", "ISA checker failed for",
              extra_env={"OBJDUMP": false_command})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
