from __future__ import annotations

import base64
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("validate_release_artifacts.py")
SPEC = importlib.util.spec_from_file_location("release_artifacts", MODULE_PATH)
assert SPEC and SPEC.loader
release_artifacts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_artifacts)


class ReleaseArtifactValidationTests(unittest.TestCase):
    def write_release(self, directory: Path) -> None:
        installer = directory / "Collie-Setup-0.1.0-alpha.1.exe"
        installer.write_bytes(b"installer")
        (directory / f"{installer.name}.blockmap").write_bytes(b"blockmap")
        sha512 = base64.b64encode(hashlib.sha512(installer.read_bytes()).digest()).decode()
        (directory / "alpha.yml").write_text(
            "\n".join(
                [
                    "version: 0.1.0-alpha.1",
                    "files:",
                    f"  - url: {installer.name}",
                    f"    sha512: {sha512}",
                    f"    size: {installer.stat().st_size}",
                    f"path: {installer.name}",
                    f"sha512: {sha512}",
                ]
            ),
            encoding="utf-8",
        )
        manifest_entries = []
        for name in (installer.name, f"{installer.name}.blockmap", "alpha.yml"):
            path = directory / name
            manifest_entries.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}")
        (directory / "SHA256SUMS.txt").write_text("\n".join(manifest_entries), encoding="utf-8")

    def test_accepts_consistent_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_release(directory)
            self.assertEqual(release_artifacts.validate(directory, "alpha.yml", "SHA256SUMS.txt"), [])

    def test_accepts_powershell_utf8_bom_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_release(directory)
            manifest = (directory / "SHA256SUMS.txt").read_text(encoding="utf-8")
            (directory / "SHA256SUMS.txt").write_text(manifest, encoding="utf-8-sig")
            self.assertEqual(release_artifacts.validate(directory, "alpha.yml", "SHA256SUMS.txt"), [])

    def test_reports_missing_blockmap_and_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_release(directory)
            (directory / "Collie-Setup-0.1.0-alpha.1.exe.blockmap").unlink()
            (directory / "SHA256SUMS.txt").write_text("0" * 64 + "  alpha.yml\n", encoding="utf-8")
            errors = release_artifacts.validate(directory, "alpha.yml", "SHA256SUMS.txt")
            self.assertIn("missing required artifact: Collie-Setup-0.1.0-alpha.1.exe.blockmap", errors)
            self.assertIn("SHA-256 mismatch for alpha.yml", errors)

    def test_rejects_metadata_path_outside_release_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_release(directory)
            metadata = (directory / "alpha.yml").read_text(encoding="utf-8")
            (directory / "alpha.yml").write_text(
                metadata.replace("path: Collie-Setup-0.1.0-alpha.1.exe", "path: ../outside.exe"),
                encoding="utf-8",
            )
            errors = release_artifacts.validate(directory, "alpha.yml", "SHA256SUMS.txt")
            self.assertIn("metadata path must be a plain release-directory filename: ../outside.exe", errors)

    def test_rejects_url_with_directory_or_absolute_path(self) -> None:
        for invalid_url in ("releases/installer.exe", "..\\installer.exe", "C:\\installer.exe"):
            with self.subTest(invalid_url=invalid_url), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                self.write_release(directory)
                metadata = (directory / "alpha.yml").read_text(encoding="utf-8")
                (directory / "alpha.yml").write_text(
                    metadata.replace("Collie-Setup-0.1.0-alpha.1.exe", invalid_url, 1),
                    encoding="utf-8",
                )
                errors = release_artifacts.validate(directory, "alpha.yml", "SHA256SUMS.txt")
                self.assertIn(
                    f"metadata files URL must be a plain release-directory filename: {invalid_url}", errors
                )
