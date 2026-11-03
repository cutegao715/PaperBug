"""Minimal .env loader (no external dependency).

Loads ``.env`` from the current working directory into ``os.environ``
at startup. Existing environment variables always win, so real env
vars / CI secrets are never shadowed by the file.
"""
from __future__ import annotations

import os
from pathlib import Path
import warnings

_migration_warned = False


def load_env_file(path: str | Path = ".env") -> dict[str, str]:
    global _migration_warned
    env_path = Path(path)
    if not env_path.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if not key:
            continue
        if key.endswith("API_KEY") and value and not _migration_warned:
            warnings.warn("Plaintext credential found in .env; migrate with paperagent secrets set.", UserWarning)
            _migration_warned = True
        if key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded
