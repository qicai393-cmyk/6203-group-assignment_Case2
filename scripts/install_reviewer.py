"""Hash-guarded local patch installer. Default is a read-only preview."""
from __future__ import annotations

import argparse
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
BASE_HASHES = {
    "src/agentdojo/approval.py": "819de3b9789737c04447d8a1cded182d0764d94b3966bfa14128f11af3781bd0",
    "src/agentdojo/approval_usage.py": "bad9377e6d02ccb5b0bfec3d2e2e9946280232da1948f87345df4386fed4b309",
}
FILES = [*BASE_HASHES, "src/agentdojo/approval_reviewer.py",
         "src/agentdojo/prompts/authorization_reviewer.txt"]
# These are the bytes in the delivered PE6203_Reviewer_v1 archive. Accept the
# complete release only: per-file allowlists would also accept mixed installs.
V1_HASHES = {
    "src/agentdojo/approval.py": "46f4cddf208a5b96b4697014ece4a3088422d7df52e03eb4325d1583a21787ae",
    "src/agentdojo/approval_usage.py": "8a0127292b468984768f40a422a071cf627b2defaf5276985316353c0857d51b",
    "src/agentdojo/approval_reviewer.py": "f66f71b7e218f535570b29de8b595954258720f481304908d8ef19377c368f78",
    "src/agentdojo/prompts/authorization_reviewer.txt": "94cf37fd3e5fb524fefe2f865eda2fde9e07208409c4d4d5a2764a2bbcad02c2",
}
# v1.1, v1.2 and v1.2.1 shipped this same core before the prompt fix.
# Accept the complete set, never independent known hashes from mixed releases.
PRIOR_RELEASE_HASHES = {
    "src/agentdojo/approval.py": "46f4cddf208a5b96b4697014ece4a3088422d7df52e03eb4325d1583a21787ae",
    "src/agentdojo/approval_usage.py": "e4f5c66ccdd6dd9d9e1802dcc601d50889aada90bc52640a67267fcd2f927225",
    "src/agentdojo/approval_reviewer.py": "8ba673242994c4639e3c5241f58961098a74018bd915738a4e898ecdd92ba893",
    "src/agentdojo/prompts/authorization_reviewer.txt": "94cf37fd3e5fb524fefe2f865eda2fde9e07208409c4d4d5a2764a2bbcad02c2",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprints(project):
    delivery, installed = {}, {}
    for relative in FILES:
        source, target = PACKAGE / relative, project / relative
        if not source.is_file():
            raise ValueError(f"Delivery file is missing: {relative}")
        if not target.resolve().is_relative_to(project):
            raise ValueError(f"Target escapes project: {relative}")
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError(f"Unknown local version; target is not a regular file: {relative}")
        delivery[relative] = sha(source)
        installed[relative] = sha(target) if target.is_file() else None
    return delivery, installed


def plan(project):
    project = project.resolve(strict=True)
    delivery, installed = _fingerprints(project)
    baseline = {relative: BASE_HASHES.get(relative) for relative in FILES}
    if installed not in (baseline, V1_HASHES, PRIOR_RELEASE_HASHES, delivery):
        raise ValueError("Unknown local version or mixed installation; only the complete "
                         "Group A baseline, exact reviewer v1, the prior v1.1/v1.2/v1.2.1 "
                         "release, or this delivery is accepted. "
                         "Merge patches/reviewer.patch manually.")
    return [(relative, PACKAGE / relative, project / relative) for relative in FILES
            if installed[relative] != delivery[relative]]


def apply_plan(project, changes):
    """Revalidate the whole installation and back up every original before writes."""
    project = project.resolve(strict=True)
    if plan(project) != changes:
        raise ValueError("Installation changed after preview; no files were written.")
    if not changes:
        return None
    expected_delivery, original = _fingerprints(project)
    backup = project / ("reviewer-backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    backup.mkdir(exist_ok=False)
    # Even unchanged files are backed up, so the full previous release is kept.
    # A backup failure cannot leave a partially upgraded installation.
    for relative, digest in original.items():
        if digest is not None:
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project / relative, saved)
            if sha(saved) != digest:
                raise OSError("Backup checksum mismatch; no delivery files were written")
    if _fingerprints(project) != (expected_delivery, original):
        raise ValueError("Installation or delivery changed during backup; no delivery files were written.")
    written = []
    try:
        for relative, source, target in changes:
            target.parent.mkdir(parents=True, exist_ok=True)
            written.append((relative, target))
            shutil.copy2(source, target)
            if sha(target) != expected_delivery[relative]:
                raise OSError("Copy checksum mismatch")
    except Exception as error:
        rollback_errors = []
        for relative, target in reversed(written):
            try:
                if original[relative] is not None:
                    shutil.copy2(backup / relative, target)
                    if sha(target) != original[relative]:
                        raise OSError("Rollback checksum mismatch")
                elif target.is_file():
                    target.unlink()
            except Exception:
                rollback_errors.append(relative)
        if rollback_errors:
            raise RuntimeError(f"Installation failed and rollback was incomplete; originals retained at {backup}") from error
        raise
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="Apply only after every target passes hash checks")
    args = parser.parse_args()
    project = args.project.resolve(strict=True)
    try:
        changes = plan(project)
    except ValueError as error:
        parser.exit(2, str(error) + "\n")
    if not changes:
        print("Already installed: all four delivery files match.")
        return
    for relative, _, _ in changes:
        print(("APPLY " if args.apply else "PREVIEW ") + relative)
    if not args.apply:
        print("No files changed. Add --apply to install this exact reviewed plan.")
        return
    try:
        backup = apply_plan(project, changes)
    except ValueError as error:
        parser.exit(2, str(error) + "\n")
    print(f"Installed {len(changes)} files; originals backed up at {backup}")


if __name__ == "__main__":
    main()
