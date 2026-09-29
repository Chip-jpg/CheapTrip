"""
Reading and updating the .env settings file (B38: the app's Settings screen).

Updates keep every other line (comments, settings the app doesn't manage)
untouched: a key already present is rewritten in place, a new key is
appended, and None removes the key. Values are written unquoted unless they
contain spaces, quotes or '#'.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Mapping, Optional

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _quote(value: str) -> str:
    if value == "" or not re.search(r"[\s\"'#]", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def update_env_file(path: Path, updates: Mapping[str, Optional[str]]) -> Dict[str, Optional[str]]:
    """Set (or with None, remove) KEY=value lines; returns the updates applied. Keys are written upper-case."""
    wanted = {key.upper(): value for key, value in updates.items()}
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    written = set()
    out = []
    for line in lines:
        match = _LINE.match(line)
        key = match.group(1).upper() if match else None
        if key in wanted:
            if key in written or wanted[key] is None:
                continue  # drop duplicates, and removed keys
            out.append(f"{key}={_quote(wanted[key])}")
            written.add(key)
        else:
            out.append(line)
    for key, value in wanted.items():
        if key not in written and value is not None:
            out.append(f"{key}={_quote(value)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return wanted
