"""Estante temporária: segura arquivos, imagens, links, textos e cores enquanto você troca de janela.

- O que você solta na ilha (ou cola) fica até fechar o Ventude.
- Com o histórico ligado, o que você copia (Ctrl + C) também entra, marcado como "copiado"; ficam os
  20 mais recentes. O que vem de gerenciadores de senha (formatos que pedem para não monitorar) fica de fora.
- Itens fixados sobrevivem ao fechar: ficam no SQLite (e as imagens numa pasta própria).

Imagens soltas sem arquivo (ex.: arrastadas do navegador ou copiadas) viram PNG numa pasta temporária
do Ventude, apagada ao abrir.
"""

from __future__ import annotations

import hashlib
import itertools
import re
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData, QObject, Signal
from PySide6.QtGui import QImage

from ventude.core.paths import app_data_dir

if TYPE_CHECKING:
    from ventude.core.store import Store

CLIP_LIMIT = 20
TEXT_LIMIT = 20_000
HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
# Formatos que gerenciadores de senha usam para ficar fora de históricos (o mesmo que o Win + V respeita).
PRIVATE_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")
HISTORY_FORMAT = "CanIncludeInClipboardHistory"


@dataclass(frozen=True)
class ShelfItem:
    id: int
    kind: str  # file | folder | image | link | text | color
    value: str  # caminho, URL, texto ou #RRGGBB
    name: str
    added: float
    temporary: bool = False  # arquivo criado pelo Ventude (apagado ao remover)
    source: str = "drop"  # drop (soltou/colou) | clip (copiou com Ctrl + C)
    pinned: bool = False  # fixado: continua na estante depois de fechar o Ventude

    @property
    def is_path(self) -> bool:
        return self.kind in ("file", "folder", "image")

    @property
    def is_text(self) -> bool:
        return self.kind in ("text", "link", "color")


def shelf_dir() -> Path:
    folder = app_data_dir() / "estante"
    folder.mkdir(exist_ok=True)
    return folder


def pinned_dir() -> Path:
    folder = app_data_dir() / "estante-fixados"
    folder.mkdir(exist_ok=True)
    return folder


def classify_text(text: str) -> tuple[str, str, str]:
    """(tipo, valor, nome) de um texto: cor HEX, link ou texto comum."""
    text = text.strip()
    if HEX_COLOR.match(text):
        return "color", text.upper(), text.upper()
    if re.match(r"^https?://\S+$", text):
        return "link", text, link_name(text)
    first = text.splitlines()[0][:60] if text else ""
    return "text", text[:TEXT_LIMIT], first


def is_private(mime: QMimeData) -> bool:
    """O app que copiou pediu para não entrar em históricos (ex.: senha do KeePass ou do 1Password)."""
    for fmt in mime.formats():
        if any(name in fmt for name in PRIVATE_FORMATS):
            return True
        if HISTORY_FORMAT in fmt:
            data = bytes(mime.data(fmt))
            if len(data) >= 4 and int.from_bytes(data[:4], "little") == 0:
                return True
    return False


class ShelfService(QObject):
    changed = Signal()

    def __init__(self, store: Store | None = None) -> None:
        super().__init__()
        self.store = store
        self.items: list[ShelfItem] = []
        self._ids = itertools.count(1)
        self._clipboard = None
        self._image_hashes: dict[str, str] = {}  # md5 da imagem -> caminho (não salva a mesma duas vezes)
        self.clear_temp()
        self._load_pins()

    @staticmethod
    def clear_temp() -> None:
        shutil.rmtree(shelf_dir(), ignore_errors=True)

    def _load_pins(self) -> None:
        if self.store is None:
            return
        for kind, value, name, added in self.store.shelf_pins():
            if kind in ("file", "folder", "image") and not Path(value).exists():
                self.store.remove_shelf_pin(value)
                continue
            item = ShelfItem(next(self._ids), kind, value, name, added, source="drop", pinned=True)
            self.items.append(item)

    @property
    def dropped_count(self) -> int:
        """Itens que você soltou/colou ou fixou (o histórico de cópias não conta na pílula)."""
        return sum(1 for i in self.items if i.source != "clip" or i.pinned)

    def _add(
        self, kind: str, value: str, name: str, temporary: bool = False, source: str = "drop"
    ) -> ShelfItem | None:
        existing = next((i for i in self.items if i.value == value), None)
        if existing is not None:
            if source == "clip" and self.items[0] is not existing:
                self.items.remove(existing)  # copiou de novo: volta para o topo
                self.items.insert(0, existing)
                return existing
            return None
        item = ShelfItem(next(self._ids), kind, value, name, time.time(), temporary, source)
        self.items.insert(0, item)
        if source == "clip":
            self._trim()
        return item

    def _trim(self) -> None:
        copied = [i for i in self.items if i.source == "clip" and not i.pinned]
        for item in copied[CLIP_LIMIT:]:
            self.items.remove(item)
            self._delete_file(item)

    def add_paths(self, paths: list[str], source: str = "drop") -> int:
        added = 0
        for raw in paths:
            raw = (raw or "").strip()
            if raw.startswith(("http://", "https://")):
                added += self._add("link", raw, link_name(raw), source=source) is not None
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
                added += self._add(kind, str(path), path.name or str(path), source=source) is not None
        if added:
            self.changed.emit()
        return added

    def add_text(self, text: str, source: str = "drop") -> bool:
        if not text.strip():
            return False
        kind, value, name = classify_text(text)
        ok = self._add(kind, value, name, source=source) is not None
        if ok:
            self.changed.emit()
        return ok

    def add_image(self, image: QImage, source: str = "drop") -> bool:
        if image.isNull():
            return False
        digest = hashlib.md5(bytes(image.constBits())[: image.sizeInBytes()]).hexdigest()
        known = self._image_hashes.get(digest)
        if known and any(i.value == known for i in self.items):
            ok = self._add("image", known, Path(known).name, temporary=True, source=source) is not None
            if ok:
                self.changed.emit()
            return ok
        stamp = time.strftime("%H%M%S")
        path = shelf_dir() / f"imagem-{stamp}-{next(self._ids)}.png"
        if not image.save(str(path), "PNG"):
            return False
        self._image_hashes[digest] = str(path)
        ok = self._add("image", str(path), path.name, temporary=True, source=source) is not None
        if ok:
            self.changed.emit()
        return ok

    # ---------- histórico da área de transferência ----------
    @property
    def clipboard_history(self) -> bool:
        return self._clipboard is not None

    def set_clipboard_history(self, on: bool) -> None:
        from PySide6.QtGui import QGuiApplication

        if on == self.clipboard_history:
            return
        if on:
            self._clipboard = QGuiApplication.clipboard()
            self._clipboard.dataChanged.connect(self._clipboard_changed)
        else:
            try:
                self._clipboard.dataChanged.disconnect(self._clipboard_changed)  # type: ignore[union-attr]
            except (RuntimeError, TypeError):
                pass
            self._clipboard = None

    def _clipboard_changed(self) -> None:
        clipboard = self._clipboard
        mime = clipboard.mimeData() if clipboard is not None else None
        if mime is not None:
            self.capture(mime, clipboard.image() if mime.hasImage() else None)  # type: ignore[union-attr]

    def capture(self, mime: QMimeData, image: QImage | None = None) -> bool:
        """Guarda o que acabou de ser copiado (texto, link, cor, imagem ou arquivos)."""
        if is_private(mime):
            return False
        if mime.hasUrls() and any(u.isLocalFile() for u in mime.urls()):
            return self.add_paths([u.toLocalFile() for u in mime.urls() if u.isLocalFile()], "clip") > 0
        if mime.hasImage():
            image = image if image is not None else QImage(mime.imageData())
            return self.add_image(image, "clip")
        if mime.hasText():
            return self.add_text(mime.text(), "clip")
        return False

    # ---------- fixar ----------
    def pin(self, item_id: int, on: bool = True) -> ShelfItem | None:
        index = next((n for n, i in enumerate(self.items) if i.id == item_id), -1)
        if index < 0:
            return None
        item = self.items[index]
        if on and not item.pinned:
            if item.temporary:  # a imagem sai da pasta temporária para não sumir ao abrir de novo
                target = pinned_dir() / Path(item.value).name
                try:
                    shutil.copy2(item.value, target)
                except OSError:
                    return None
                Path(item.value).unlink(missing_ok=True)
                item = replace(item, value=str(target), temporary=False)
            item = replace(item, pinned=True)
            if self.store is not None:
                self.store.add_shelf_pin(item.kind, item.value, item.name, item.added)
        elif not on and item.pinned:
            if self.store is not None:
                self.store.remove_shelf_pin(item.value)
            owned = Path(item.value).parent == pinned_dir()
            item = replace(item, pinned=False, temporary=item.temporary or owned)
        self.items[index] = item
        self.changed.emit()
        return item

    # ---------- remover ----------
    @staticmethod
    def _delete_file(item: ShelfItem) -> None:
        if item.temporary:
            Path(item.value).unlink(missing_ok=True)

    def remove(self, item_id: int) -> None:
        for item in [i for i in self.items if i.id == item_id]:
            self.items.remove(item)
            if item.pinned and self.store is not None:
                self.store.remove_shelf_pin(item.value)
                if Path(item.value).parent == pinned_dir():
                    Path(item.value).unlink(missing_ok=True)
            self._delete_file(item)
        self.changed.emit()

    def clear(self) -> None:
        """Limpa o que não está fixado."""
        for item in [i for i in self.items if not i.pinned]:
            self.items.remove(item)
            self._delete_file(item)
        self.changed.emit()


def link_name(url: str) -> str:
    """'https://github.com/a/b' -> 'github.com · b' (curto para caber no bloco)."""
    rest = url.split("//", 1)[-1].rstrip("/")
    host, _, path = rest.partition("/")
    host = host.removeprefix("www.")
    tail = path.rsplit("/", 1)[-1] if path else ""
    return f"{host} · {tail[:30]}" if tail else host


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
