### Python

- System Python 3 with `python`, `pip`, `venv`.
- Always work in a virtual environment: `python -m venv .venv && . .venv/bin/activate`;
  system-wide `pip install` is blocked (PEP 668).
- `PIP_CACHE_DIR` / `UV_CACHE_DIR` point at the project build cache.
- Use the project's tool if it has one (`uv`, `poetry`, `hatch`): check `pyproject.toml` first.
