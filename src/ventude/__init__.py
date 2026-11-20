"""Ventude: uma Dynamic Island leve para Windows."""

try:  # gerado pelo build da main (scripts/stamp_version.py)
    from ventude._build import VERSION as __version__
except ImportError:  # rodando do código-fonte
    __version__ = "1.1.0"


def main() -> int:
    from ventude.app import run

    return run()
