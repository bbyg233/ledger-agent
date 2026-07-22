# Contributing

Thanks for contributing to Ledger Agent.

## Development setup

```bash
mamba env create -f environment.yml
mamba activate financial-agent
PYTHONPATH=src pytest -q
ruff check src tests
node --check web/app.js
```

The standard test suite uses temporary SQLite databases and must not use a contributor's real `.financial_agent/ledger.db`. Tests marked `integration` may call a real model provider and are excluded by default.

## Contribution boundaries

- Keep the service local-only by default.
- Do not add features that make payments, investments, borrowing, tax filings, or bank actions.
- LLM output is untrusted: new write paths need Pydantic validation, a user confirmation boundary, SQLite transaction handling, and focused tests.
- Do not add real keys, ledger data, screenshots, backups, logs, or personal paths to commits.
- Prefer the existing module boundaries: `ledger/` owns financial data, `agent/` owns tool schemas and runtime behavior, and `web_app.py` adapts HTTP.

## Pull requests

Keep pull requests focused. Describe the user-visible change, the safety implications, and the test commands you ran. Add regression coverage for changes to calculations, liability handling, account balances, imports, or Agent tool behavior.
