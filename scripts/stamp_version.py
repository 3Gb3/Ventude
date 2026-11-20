"""Carimba a versão do build da main.

    python scripts/stamp_version.py [commit]

A versão base vem do pyproject (ex.: 1.1.0). O primeiro build de uma linha X.Y
publica exatamente a versão do pyproject; os seguintes sobem só o último número,
olhando as tags vX.Y.* que já existem (1.1.0, 1.1.1, 1.1.2...).
Escreve src/ventude/_build.py e imprime a versão.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def base_version() -> tuple[int, int, int]:
    raw = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    parts = [int(p) for p in re.findall(r"\d+", raw)[:3]]
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def existing_tags() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "tag", "--list", "v*"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def next_version(base: tuple[int, int, int], tags: list[str]) -> str:
    major, minor, patch = base
    pattern = re.compile(rf"^v{major}\.{minor}\.(\d+)$")
    used = [int(m.group(1)) for tag in tags if (m := pattern.match(tag))]
    if used:
        patch = max(max(used) + 1, patch)
    return f"{major}.{minor}.{patch}"


def main() -> None:
    commit = sys.argv[1] if len(sys.argv) > 1 else ""
    version = next_version(base_version(), existing_tags())
    (ROOT / "src" / "ventude" / "_build.py").write_text(
        f'VERSION = "{version}"\nCOMMIT = "{commit}"\n', encoding="utf-8"
    )
    print(version)


if __name__ == "__main__":
    main()
