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

## Development

Validate the manifest:

```bash
python3 scripts/validate-apps-manifest.py apps.yml
```

Collect and build locally:

```bash
python3 scripts/collect-latest-assets.py apps.yml --download-dir build/source --output build/source-assets.json
python3 scripts/build-aggregate-bundles.py build/source-assets.json --source-dir build/source --output-dir dist
```

Do not commit downloaded app zips or generated `dist` contents.
