# nozzle-apps

`nozzle-apps` is a convenience download center for standalone Nozzle applications.

It does **not** contain the application source code. Source code and app-specific issues remain in the individual repositories:

- [`nozzle-viewer`](https://github.com/nozzle-io/nozzle-viewer)
- [`nozzle-mixer`](https://github.com/nozzle-io/nozzle-mixer)
- [`uvc-nozzle`](https://github.com/nozzle-io/uvc-nozzle)
- [`nozzle-tester`](https://github.com/nozzle-io/nozzle-tester)

## Latest downloads

The `latest` release in this repository is a mutable development snapshot. It is not a stable or official versioned release.

The automation fetches each source app's `latest` release by explicit tag lookup (`releases/tags/latest`), verifies source release checksums, and builds platform aggregate zips:

- `nozzle-apps-latest-<shortsha>-macos-universal.zip`
- `nozzle-apps-latest-<shortsha>-windows-x64.zip`
- `nozzle-apps-latest-<shortsha>-linux-x64.zip`

macOS apps may be ad-hoc signed and may not be notarized unless a later signing policy says otherwise.

## Bundle contents

Each platform zip uses this layout:

```text
nozzle-apps/
  manifest.json
  SHA256SUMS
  apps/
    nozzle-viewer/
    nozzle-mixer/
    nozzle-tester/
    uvc-nozzle/        # macOS only today
```

Expected app coverage:

| Platform | Apps |
| --- | --- |
| `macos-universal` | `nozzle-viewer`, `nozzle-mixer`, `uvc-nozzle`, `nozzle-tester` |
| `windows-x64` | `nozzle-viewer`, `nozzle-mixer`, `nozzle-tester` |
| `linux-x64` | `nozzle-viewer`, `nozzle-mixer`, `nozzle-tester` |

`uvc-nozzle` is not included on Windows or Linux until those source assets exist.

## Evidence

The release also publishes:

- `SHA256SUMS` for final aggregate assets
- release-level `manifest.json` with final aggregate zip SHA256 values and source asset evidence

Each aggregate zip contains its own `manifest.json` and `SHA256SUMS` for the actual extracted files included in that platform bundle. After extraction, `sha256sum -c SHA256SUMS` from inside the `nozzle-apps/` directory must verify the bundle-local contents. Original source zip SHA256 evidence remains in `manifest.json`. The final aggregate zip SHA256 is recorded in the release-level `manifest.json`, not inside the zip itself, because a file cannot honestly contain the SHA256 of the zip that already contains that same file.

The aggregate builder preserves Unix permission bits from the source app zips. The publish workflow verifies extracted aggregate bundles, including macOS `.app/Contents/MacOS/<CFBundleExecutable>` files being executable.

For `linux-x64`, that same final-ZIP verifier scans extracted files by ELF magic,
including nested libraries and files without executable bits or conventional
extensions. Each required Linux app in `apps.yml` must be present, and each
included app must contain an ELF payload. ELF files must have a valid 64-bit,
little-endian x86-64 executable/shared-object header. Symlinks are rejected in
Linux bundles rather than silently omitted from the scan.

CI checks out nozzle revision `79637d4b25b13b1d7ab5751ccc7d0a0e14bc808e`
without submodules and invokes its unchanged `scripts/check_x86_64_isa.sh`.
The verifier checks that revision and the script contents, then logs every ELF's
bundle-relative path, SHA256, checker revision/hash, and scanner output. Missing
tools, malformed ELF headers, empty app coverage, or checker failures stop
publication before any latest-tag or release mutation. Hash verification alone
is not ISA evidence. The validation workflow drives this production verifier with
real compiled baseline and AVX ELF fixtures; it does not publish a release.

This is a limited ISA gate, not full CPU or runtime compatibility certification.
The pinned checker detects AVX/EVEX-class and selected x86-64-v3 integer
instructions; it does not detect AES-NI, PCLMUL, SHA, ADX, RDRAND, XSAVE, SSE4a,
AMX or APX, and treats undecodable instructions as warnings. It does not inspect
unbundled dynamic dependencies or native files inside additional nested archives.
No symbol exemptions are applied by the aggregate verifier.

## Development

Validate the manifest:

```bash
python3 scripts/validate-apps-manifest.py apps.yml
```

Collect and build locally (the checker checkout is required for Linux verification):

```bash
python3 scripts/collect-latest-assets.py apps.yml --download-dir build/source --output build/source-assets.json
python3 scripts/build-aggregate-bundles.py build/source-assets.json --source-dir build/source --output-dir dist
git clone --no-checkout https://github.com/nozzle-io/nozzle.git build/isa-checker
git -C build/isa-checker checkout --detach 79637d4b25b13b1d7ab5751ccc7d0a0e14bc808e
python3 scripts/verify-aggregate-bundles.py --isa-checker-root build/isa-checker dist/nozzle-apps-latest-*.zip
```

Run the permission preservation regression test:

```bash
python3 scripts/test-permission-preservation.py
```

The final-archive ISA regression runs on Linux with `cc`, `bash`, `git`, `unzip`
and GNU `objdump` available:

```bash
python3 scripts/test-final-archive-isa.py --isa-checker-root build/isa-checker
```

Do not commit downloaded app zips or generated `dist` contents.
