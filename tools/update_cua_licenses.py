"""Record the pinned Windows CUA normal/build dependency closure and notices.

Input is Cargo metadata from the exact upstream tag, resolved with --locked and
--filter-platform x86_64-pc-windows-msvc. Never compiles or runs the driver.
Missing archive notices must be supplied from each crate's recorded VCS commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import tomllib


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--extra-notices", type=Path, required=True)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    target = repository / "collie-core/collie_core/computer"
    metadata = json.loads(args.metadata.read_text(encoding="utf-8-sig"))
    packages = {p["id"]: p for p in metadata["packages"]}
    nodes = {p["id"]: p for p in metadata["resolve"]["nodes"]}
    roots = {"cua-driver", "cua-driver-sdk", "cua-driver-uia"}
    todo = [p["id"] for p in packages.values() if p["name"] in roots]
    assert len(todo) == len(roots), "Missing bundled binary roots"
    seen = set()
    while todo:
        key = todo.pop()
        if key in seen:
            continue
        seen.add(key)
        for dependency in nodes[key]["deps"]:
            if any(kind["kind"] != "dev" for kind in dependency["dep_kinds"]):
                todo.append(dependency["pkg"])
    rust = args.upstream / "libs/cua-driver/rust"
    lock_bytes = (rust / "Cargo.lock").read_bytes()
    lock = tomllib.loads(lock_bytes.decode())
    checksums = {(p["name"], p["version"]): p.get("checksum") for p in lock["package"]}
    pattern = re.compile(
        r"^(licen[sc]e|notice|copying|copyright|authors)(?:[._-]|$)", re.IGNORECASE
    )
    records = []
    texts: dict[str, dict] = {}
    for package in sorted(
        (packages[i] for i in seen), key=lambda p: (p["name"], p["version"])
    ):
        name, version = package["name"], package["version"]
        root = Path(package["manifest_path"]).parent
        license_id = package["license"]
        assert license_id, f"Unclassified license: {name}"
        assert not re.search(r"(?<![A-Z])(?:A?GPL|LGPL)-", license_id), license_id
        documents = []
        for path in sorted(root.rglob("*")):
            if path.is_file() and pattern.match(path.name):
                documents.append(
                    {
                        "name": path.relative_to(root).as_posix(),
                        "text": path.read_text(encoding="utf-8-sig", errors="strict"),
                    }
                )
        if package["source"] and not documents:
            documents = json.loads(
                (args.extra_notices / f"{name}-{version}.json").read_text()
            )
        refs = []
        for document in documents:
            text = document["text"].replace("\r\n", "\n").strip() + "\n"
            key = digest(text.encode())
            texts.setdefault(key, {"text": text, "files": []})
            texts[key]["files"].append(f"{name} {version}: {document['name']}")
            refs.append(key)
        source = (
            f"https://crates.io/api/v1/crates/{name}/{version}/download"
            if package["source"]
            else "https://github.com/trycua/cua/tree/2d2f99228d4d2508dc6fcc84c176b296e146714a"
        )
        records.append(
            {
                "name": name,
                "version": version,
                "license": license_id,
                "source": source,
                "repository": package["repository"],
                "crate_sha256": checksums.get((name, version)),
                "notice_sha256": refs,
            }
        )
    binary_manifest = json.loads((target / "runtime-artifacts.json").read_text())
    inventory = {
        "schema_version": 1,
        "upstream_commit": "2d2f99228d4d2508dc6fcc84c176b296e146714a",
        "upstream_tag": "cua-driver-rs-v0.37.0",
        "target": "x86_64-pc-windows-msvc",
        "scope": "Conservative locked normal/build closure of all three bundled binaries; dev edges excluded. Cargo feature unification can include additional packages.",
        "cargo_lock_sha256": digest(lock_bytes),
        "binaries": binary_manifest["files"],
        "packages": records,
    }
    (target / "runtime-sbom.json").write_text(
        json.dumps(inventory, indent=2) + "\n", encoding="utf-8"
    )
    intro = """# Bundled CUA Windows dependency notices

This distribution includes CUA Driver 0.37.0 and the dependencies inventoried
in `runtime-sbom.json`. License choices and copyright notices are retained below.
The inventory conservatively includes normal and build dependencies of the main
executable, UIA helper and SDK DLL for Windows x64, excluding development edges.
It may include crates not linked into an individual binary.

Source form for every registry package is available at the exact-version source
URL in `runtime-sbom.json` (the publicly downloadable crates.io source archive).
This includes the Mozilla Public License 2.0 UniFFI crates. Their covered source
is available under MPL-2.0 at those URLs; no modifications to those crates are
made by Collie. CUA's own MIT license and derivative notices are in
`LICENSE-CUA.md` and `CUA-THIRD-PARTY-NOTICES.md`. Collie's license does not
replace any bundled component's license. Source availability imposes no charge.

## Packages

| Package | Version | Declared license |
| --- | --- | --- |
"""
    lines = [intro]
    for row in records:
        lines.append(
            f"| [{row['name']}]({row['source']}) | {row['version']} | {row['license']} |\n"
        )
    lines.append("\n## License and notice texts\n")
    for key, value in sorted(texts.items()):
        lines.append(f"\n### SHA-256 {key}\n\nApplies to:\n\n")
        lines.extend(f"- {name}\n" for name in value["files"])
        lines.append("\n````text\n" + value["text"] + "````\n")
    (target / "DEPENDENCY-NOTICES.md").write_text("".join(lines), encoding="utf-8")
    print(f"Recorded {len(records)} packages and {len(texts)} distinct notice texts.")


if __name__ == "__main__":
    main()
