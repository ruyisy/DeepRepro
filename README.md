![DeepRepro logo](assets/fig_main.png)

**DeepRepro is a paper-to-code reproduction framework for automatic ML reproducibility via deep subplanning.**
It combines paper understanding, round-level subplanning, agentic implementation, memory management, and repair into one observable workflow.

## Highlights

- Multi-agent collaboration across planning, execution, memory, and diagnostics.
- Deep subplanning for round-level file selection and repair guidance.
- Automatic issue repair for interface mismatches and implementation gaps.
- Efficient memory management for long-running reproduction runs.

## Quick Start

```bash
python ./deeprepro.py --local
```

This starts the local backend and frontend.

## Requirements

- Python 3.9+
- Node.js and npm
- Local model configuration in `mcp_agent.config.yaml` and `mcp_agent.secrets.yaml`

## Workflow Modes

DeepRepro provides four paper-to-code modes:

| Mode | Planning | Reference indexing | Description |
| --- | --- | --- | --- |
| `raw_fast` | Lightweight | No | Fast generation without reference-code indexing. |
| `infer_fast` | Lightweight | Yes | Fast generation with reference-code indexing. |
| `raw_deepplan` | Deep | No | Deep-planning generation without reference-code indexing. |
| `infer_deepplan` | Deep | Yes | Deep-planning generation with reference-code indexing. |

Fast modes keep the execution loop lightweight. DeepPlan modes add a subplan agent that writes round-level instructions for the execute agent and coordinates repairs and diagnostics.

## Repository Layout

- `deeprepro.py`: local launcher.
- `ui/`: frontend and backend services.
- `workflows/`: orchestration logic.
- `tools/`: PDF processing, indexing, and utility helpers.
- `prompts/`: agent prompt templates.
- `assets/`: public logos and illustration assets.
- `uploads/`: local task uploads and intermediate files.
- `docs/`: evaluation notes and benchmark guidance.

## Configuration

1. Copy `mcp_agent.secrets.yaml.example` to `mcp_agent.secrets.yaml`.
2. Fill in the provider API key(s) you want to use locally.
3. Keep the secrets file local; do not commit it to a public repository.

## Notes

- The supported local entrypoint is `python ./deeprepro.py --local`.
- Generated reproduction artifacts live in the task workspace and should not be edited manually.
- Some internal names may still appear during the migration phase, but the public project name is DeepRepro.

## Acknowledgements

DeepRepro is built on top of [HKUDS/DeepCode](https://github.com/HKUDS/DeepCode) and also takes inspiration from [going-doer/Paper2Code](https://github.com/going-doer/Paper2Code). We thank these projects for their valuable contributions to open scientific code generation and paper-to-code reproduction.

## License

See `LICENSE` for details.

