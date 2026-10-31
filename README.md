# PE6203 Case 2 · Approval-Gated Agents

[![Offline verification](https://github.com/qicai393-cmyk/6203-group-assignment_Case2/actions/workflows/reproduce.yml/badge.svg)](https://github.com/qicai393-cmyk/6203-group-assignment_Case2/actions/workflows/reproduce.yml)

An AgentDojo experiment comparing unconditional approval, full escalation, and a hybrid authorization policy under prompt-injection attacks.

## Review guide

| Material | Open |
| --- | --- |
| **Project report** | **[PE6203_ClassC_Group1_Report.pdf](PE6203_ClassC_Group1_Report.pdf)** |
| Presentation | [Group 1 slides](docs/PE6203_ClassC_Group1.pptx) |
| Implementation and setup | [`agent/`](agent/) — code, policy, configuration, tests, and [run instructions](agent/README.md) |
| Results and evidence | [`results/`](results/) — [verified results](results/summary_verified.csv), [raw logs](results/raw_logs.zip), and [statistical analysis](results/analysis/01_Statistical_Analysis_Summary.md) |
| Supporting documentation | [`docs/`](docs/) — [architecture](docs/architecture.md), [reproduction guide](docs/reproduction.md), and [metric definitions](docs/results.md) |
| Historical material | [`archive/`](archive/) — earlier handoffs and upstream references |

## Running the project

Start in `agent/` (`cd agent` from this directory), then follow the [offline verification commands](agent/README.md#offline-verification). Commands printed in the report also run from `agent/`; the submitted report and experimental evidence are unchanged.

Based on [AgentDojo](https://github.com/ethz-spylab/agentdojo) 0.1.35. The original [MIT license](agent/LICENSE) and [citation](docs/CITATION.bib) are preserved.
