# Windows release artifact validation

Each alpha candidate must be built once from a clean, committed checkout into
an empty version-and-commit-specific directory. `npm run dist` writes the
validated candidate to `collie-ui/dist/release-<version>-<commit>/`. Never use
files from the legacy flat `collie-ui/dist` root or from a `.staging-*`
directory.

The final directory contains exactly the installer, its `.blockmap`,
`alpha.yml`, `collie-build-provenance.json`,
`collie-artifact-provenance.json`, and `SHA256SUMS.txt`. The packaging command
generates and verifies both provenance records and runs the release validator.

For an independent checksum inspection, verify the generated manifest rather
than adding files to the release directory:

```powershell
Get-FileHash -Algorithm SHA256 `
  Collie-Setup-<version>.exe, `
  Collie-Setup-<version>.exe.blockmap, `
  alpha.yml |
  ForEach-Object { "{0}  {1}" -f $_.Hash.ToLowerInvariant(), $_.Path.Split('\')[-1] } |
  ForEach-Object { "{0}  {1}" -f $_.Hash.ToLowerInvariant(), $_.Path.Split('\')[-1] }
```

Run the standard-library validator before drafting a GitHub Release and again
after downloading the release artifact:

```powershell
python tools/validate_release_artifacts.py <release-directory>
```

The command fails when `alpha.yml` is missing required updater fields, its
installer reference, size, or SHA-512 differs from disk, the corresponding
`.blockmap` is absent, or the SHA-256 manifest is missing or disagrees with
the installer, blockmap, or metadata. Metadata file references must be plain
filenames inside the release directory. It deliberately does not build, sign,
upload, or modify artifacts.

Run its focused regression tests with:

```powershell
python -m unittest tools/test_validate_release_artifacts.py
```
