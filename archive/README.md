# Historical reference

This directory preserves material from the supplied package without mixing it with the
current project entry points. Start with the [project README](../README.md) for supported
commands and the current directory layout.

| Directory | Preserved material |
| --- | --- |
| [`handoffs/`](handoffs/) | Earlier group handoff notes, baseline integration logs, and the original delivery-manifest helper |
| [`upstream/`](upstream/) | Original AgentDojo README, documentation, examples, notebooks, development setup, and workflows |

The handoff documents describe earlier partial deliveries and can contain outdated test
counts, pending tasks, or paths. The old `verify_delivery.py` requires a `FILE_MANIFEST.json`
that was not in the supplied package. For current verification, enter `agent/` from
the repository root, then run `uv run --frozen python -m scripts.reproduce_results`.
The helper reads the repository-root `results/` and writes new outputs under
`agent/runs/reproduction/`. See the [reproduction guide](../docs/reproduction.md)
for setup; historical files in this directory retain their original contents and paths.

Upstream lint, documentation deployment, and PyPI publication workflows are retained
under `upstream/workflows/` as reference files. They are not active GitHub Actions for
this coursework repository. The active workflow runs offline tests, validates experiment
plans, and recomputes archived tables.
