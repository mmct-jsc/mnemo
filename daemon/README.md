# mnemo daemon

Python service powering the mnemo knowledge memory system.

## Layout

```
daemon/
├── pyproject.toml
├── mnemo/
│   ├── __init__.py
│   ├── paths.py         runtime directories under ~/.claude/mnemo/
│   └── store.py         SQLite + dataclasses for nodes, edges, sources, queries
└── tests/
    ├── conftest.py
    └── unit/
        ├── test_paths.py
        └── test_store.py
```

## Running tests

```bash
cd daemon
uv sync --extra dev
uv run pytest
```

## Dev tools

```bash
uv run ruff check .
uv run ruff format .
```
