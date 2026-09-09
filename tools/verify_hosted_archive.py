"""Verify a rolled-back Supabase fixture through the real Windows archive path."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collie-core"))
from collie_core.collaboration.archive import ArchiveManager  # noqa: E402


def main() -> None:
    log = Path(sys.argv[1]).read_text(encoding="utf-8-sig")
    document, _ = json.JSONDecoder().raw_decode(log[log.index("{"):])
    result = document["rows"][0]["hosted_collaboration_validation"]
    assert result["ok"] is True
    fixture = result["archive_fixture"]
    with tempfile.TemporaryDirectory(prefix="collie-hosted-archive-") as directory:
        root = Path(directory)
        writer = ArchiveManager(root / "source")
        writer.bind_account("hosted-fixture-owner")
        receipt = writer.write(fixture["manifest_json"], fixture["digest"])
        assert receipt["byte_length"] == fixture["byte_length"]
        assert receipt["final_seq"] == fixture["final_seq"]
        archive = Path(receipt["path"])
        manifest = writer.verify(archive)
        assert len(manifest["events"]) == fixture["final_seq"]
        assert any(event.get("content") == "bounded completion while closing" for event in manifest["events"])
        bundle = writer.export(archive, root / "roundtrip.zip")
        reader = ArchiveManager(root / "destination")
        reader.bind_account("hosted-fixture-recipient")
        imported = reader.import_bundle(bundle)
        assert imported["digest"] == fixture["digest"]
        assert reader.verify(Path(imported["path"])) == manifest
        print(json.dumps({"ok": True, "events": len(manifest["events"]), "digest": fixture["digest"], "durable_roundtrip": True}))


if __name__ == "__main__":
    main()
