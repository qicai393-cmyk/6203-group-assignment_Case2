"""Recompute archived CSV results without API calls; check source and table integrity."""

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = {
    "team_policy.py": "team_policy.py",
    "hybrid_policy.txt": "prompts/hybrid_policy.txt",
    "approval.py": "src/agentdojo/approval.py",
    "approval_reviewer.py": "src/agentdojo/approval_reviewer.py",
    "approval_usage.py": "src/agentdojo/approval_usage.py",
    "authorization_reviewer.txt": "src/agentdojo/prompts/authorization_reviewer.txt",
    "run_batch.py": "run_batch.py",
}
TABLES = (
    "summary.csv", "per_task.csv", "summary_verified.csv",
    "per_task_verified.csv", "ground_truth_reference.csv",
)


def read_table(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = tuple(reader.fieldnames or ())
        rows = list(reader)
    # Preserve duplicate rows, ignore line endings, BOM, and row/column ordering.
    values = Counter(tuple(row[field] for field in sorted(fields)) for row in rows)
    return set(fields), values, len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="runs/reproduction",
                        help="New output directory, relative to the repository root")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if output == ROOT or output.is_relative_to(ROOT / "results"):
        parser.error("Choose a new output directory outside the delivered results folder")
    if output.exists():
        parser.error("Output already exists; use --output runs/reproduction-2")

    with (ROOT / "results/SOURCE_PROVENANCE.csv").open(encoding="utf-8-sig", newline="") as stream:
        sources = list(csv.DictReader(stream))
    source_checks = {}
    for row in sources:
        path = ROOT / SOURCE_PATHS[row["File"]]
        source_checks[row["File"]] = hashlib.sha256(path.read_bytes()).hexdigest() == row["SHA256"]
    if not all(source_checks.values()):
        raise SystemExit("Core source hashes differ from SOURCE_PROVENANCE.csv")
    print(f"Source checks: {len(source_checks)}/{len(source_checks)} match")

    output.mkdir(parents=True)
    archive = output / "raw"
    with zipfile.ZipFile(ROOT / "results/raw_logs.zip") as package:
        for info in package.infolist():
            name = PurePosixPath(info.filename.replace("\\", "/"))
            target = (archive / name).resolve()
            if (name.is_absolute() or ":" in name.as_posix()
                    or not target.is_relative_to(archive.resolve())
                    or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise SystemExit("Unsafe archive path")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(package.read(info))

    tables = output / "tables"
    tables.mkdir()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
    for script in ("analyze_results.py", "verify_results.py"):
        log = output / (Path(script).stem + ".txt")
        with log.open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                [sys.executable, str(ROOT / script), str(archive / "batches")],
                cwd=tables, env=environment, stdout=stream, stderr=subprocess.STDOUT, check=False,
            )
        if result.returncode:
            raise SystemExit(f"{script} failed; inspect {log}")

    table_checks = {}
    for name in TABLES:
        fields, rows, count = read_table(tables / name)
        expected_fields, expected_rows, expected_count = read_table(ROOT / "results" / name)
        matches = fields == expected_fields and rows == expected_rows
        table_checks[name] = {"matches": matches, "rows": count, "expected_rows": expected_count}
        print(f"{'PASS' if matches else 'FAIL'} {name}: {count} rows")
    report = {"source_checks": source_checks, "table_checks": table_checks,
              "api_calls": 0, "comparison": "CSV cell values and row multiplicity; ignores ordering/BOM/newlines"}
    (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report: {output / 'verification.json'}")
    if not all(check["matches"] for check in table_checks.values()):
        raise SystemExit("Recomputed table values differ from the delivered tables")


if __name__ == "__main__":
    main()
