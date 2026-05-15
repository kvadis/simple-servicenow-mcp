# Contributing

Thanks for considering a contribution! This project is small and pragmatic — patches that make it more useful in real ServiceNow workflows are welcome.

## Before opening a PR

- **For bugs**: open an issue first with a minimal reproduction, including the tool call (anonymised), the response you got, and the response you expected.
- **For features**: open an issue describing the use case before writing code. New tools should have a clear ServiceNow operation they map to and a reason the LLM is the right caller.
- **For docs**: PRs welcome directly.

## Development setup

```bash
git clone https://github.com/kostya-kozachuk/simple-servicenow-mcp.git
cd simple-servicenow-mcp
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

Confirm the test suite passes:

```bash
pytest -q              # default — skips WIP test files (CI-equivalent)
pytest -q --run-wip    # include red-state TDD tests for features in flight
```

The `--run-wip` flag includes test files for features being developed in
parallel sessions. Those tests are expected to fail until the feature lands —
they're the work signal for whoever's implementing them. CI runs without
`--run-wip` so a red WIP test doesn't block the main branch. See
`tests/conftest.py` for the gating logic.

Optional but recommended: interactively test changes via the MCP Inspector against a [PDI](https://developer.servicenow.com) (personal developer instance):

```bash
mcp dev src/simple_servicenow_mcp/server.py
```

## Style

- **Format / lint**: `ruff format` + `ruff check` (run via `pre-commit`).
- **Types**: stay close to ours — `from __future__ import annotations`, PEP 604 union syntax (`str | None`).
- **Tests**: every new tool needs at least one behavioural test in `tests/`. Use the existing `FakeServiceNowClient` (`tests/conftest.py`) — no real ServiceNow instance required.
- **Comments**: only the *why*. Don't paraphrase the code.

## Adding a new tool

1. Place it in the right `tools/<module>.py` (or create a new one + add to `packages.py`).
2. Use `@mcp.tool(annotations=_a.READ)` (or `CREATE`/`UPDATE`/`DELETE`/`APPEND` from `tools/_annotations.py`).
3. Add `instance: str | None = None` to the signature; resolve via `app.client_for(instance)` (or `app.ensure_writable(op) → app.client_for(...)` for mutations).
4. Return native types — FastMCP auto-derives `outputSchema`.
5. Wrap ServiceNow calls in `try/except` and re-raise as `ToolError(str(e))`.
6. Register the module in `server.py` *if* you created a new tools module.
7. Document it in the README's tool reference table and in `CHANGELOG.md` under `[Unreleased]`.

## Commit messages

Conventional Commits-ish, but pragmatic:

- `feat: add aggregate_records tool`
- `fix: handle empty sys_dictionary response in validation`
- `docs: clarify multi-instance fallback behaviour`
- `test: cover read-only refusal for attachment uploads`

Keep the body focused on the *why*. The diff already shows the *what*.

## Reporting security issues

See [`SECURITY.md`](SECURITY.md) — please don't open public issues for vulnerabilities.
