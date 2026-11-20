"""Estante temporária: segura arquivos, imagens, links e textos enquanto você troca de janela.

Os itens somem ao fechar o Ventude. Imagens soltas sem arquivo (ex.: arrastadas do navegador)
viram PNG numa pasta temporária do Ventude, apagada junto.
"""

from __future__ import annotations

import itertools
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from ventude.core.paths import app_data_dir


@dataclass(frozen=True)
class ShelfItem:
    id: int
    kind: str  # file | folder | image | link | text
    value: str  # caminho, URL ou texto
    name: str
    added: float
    temporary: bool = False  # arquivo criado pelo Ventude (apagado ao remover)

    @property
    def is_path(self) -> bool:
        return self.kind in ("file", "folder", "image")


def shelf_dir() -> Path:
    folder = app_data_dir() / "estante"
    folder.mkdir(exist_ok=True)
    return folder


class ShelfService(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.items: list[ShelfItem] = []
        self._ids = itertools.count(1)
        self.clear_temp()

    @staticmethod
    def clear_temp() -> None:
        shutil.rmtree(shelf_dir(), ignore_errors=True)

    def _add(self, kind: str, value: str, name: str, temporary: bool = False) -> ShelfItem | None:
        if any(i.value == value for i in self.items):
            return None
        item = ShelfItem(next(self._ids), kind, value, name, time.time(), temporary)
        self.items.insert(0, item)
        return item

    def add_paths(self, paths: list[str]) -> int:
        added = 0
        for raw in paths:
            raw = (raw or "").strip()
            if raw.startswith(("http://", "https://")):
                added += self._add("link", raw, link_name(raw)) is not None
                continue
            path = Path(raw)
            if raw and path.exists():
                kind = (
                    "folder"
                    if path.is_dir()
                    else "image"
                    if path.suffix.lower() in IMAGE_SUFFIXES
                    else "file"
                )
                added += self._add(kind, str(path), path.name or str(path)) is not None
        if added:
            self.changed.emit()
        return added

    def add_text(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        first = text.splitlines()[0][:60]
        ok = self._add("text", text, first) is not None
        if ok:
            self.changed.emit()
        return ok

    def add_image(self, image: QImage) -> bool:
        if image.isNull():
            return False
        stamp = time.strftime("%H%M%S")
        path = shelf_dir() / f"imagem-{stamp}-{next(self._ids)}.png"
        if not image.save(str(path), "PNG"):
            return False
        ok = self._add("image", str(path), path.name, temporary=True) is not None
        if ok:
            self.changed.emit()
        return ok

    def remove(self, item_id: int) -> None:
        for item in [i for i in self.items if i.id == item_id]:
            self.items.remove(item)
            if item.temporary:
                Path(item.value).unlink(missing_ok=True)
        self.changed.emit()

    def clear(self) -> None:
        for item in self.items:
            if item.temporary:
                Path(item.value).unlink(missing_ok=True)
        self.items.clear()
        self.changed.emit()


def link_name(url: str) -> str:
    """'https://github.com/a/b' -> 'github.com · b' (curto para caber no bloco)."""
    rest = url.split("//", 1)[-1].rstrip("/")
    host, _, path = rest.partition("/")
    host = host.removeprefix("www.")
    tail = path.rsplit("/", 1)[-1] if path else ""
    return f"{host} · {tail[:30]}" if tail else host


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
