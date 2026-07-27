# Project Agent Instructions

## Default Environment

- This project uses the mamba environment `financial-agent` by default.
- Prefer one-off commands in this form:

  ```bash
  mamba run -n financial-agent <command>
  ```

- When an activated shell is needed, use:

  ```bash
  mamba activate financial-agent
  ```

- Do not use the system Python, a project-local `.venv`, or another Conda environment unless the user explicitly requests it.
- Python, `pytest`, `ruff`, `uvicorn`, and the Linux `gh` CLI are installed in this environment. When activation is unavailable, use their absolute paths under `/home/bbyg233/miniforge3/envs/financial-agent/bin/`.

## Common Checks

```bash
PYTHONPATH=src mamba run -n financial-agent python -m pytest -q
mamba run -n financial-agent ruff check src tests
mamba run -n financial-agent gh auth status
```
