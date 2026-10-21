"""Verify every delivered file against the local manifest, without API access."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def included(path):
    return path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc" and path != ROOT / "FILE_MANIFEST.json"


def main():
    manifest = json.loads((ROOT / "FILE_MANIFEST.json").read_text(encoding="utf-8"))
    expected = manifest["files"]
    actual = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if included(p)}
    if actual != set(expected):
        raise SystemExit("Delivery file inventory differs from manifest")
    for name, digest in expected.items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT):
            raise SystemExit("Invalid manifest path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit("Checksum mismatch: " + name)
    print(f"Verified {len(expected)} delivered files; SHA-256 inventory matches.")


if __name__ == "__main__":
    main()
