"""Carimba a versão do build da main: X.Y do pyproject + número do build.

    python scripts/stamp_version.py <numero_do_build> [commit]
Escreve src/ventude/_build.py e imprime a versão (ex.: 1.0.12).
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    build = int(sys.argv[1])
    commit = sys.argv[2] if len(sys.argv) > 2 else ""
    base = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    major, minor = base.split(".")[:2]
    version = f"{major}.{minor}.{build}"
    (ROOT / "src" / "ventude" / "_build.py").write_text(
        f'VERSION = "{version}"\nCOMMIT = "{commit}"\n', encoding="utf-8"
    )
    print(version)


if __name__ == "__main__":
    main()
