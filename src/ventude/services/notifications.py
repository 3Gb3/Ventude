"""Central de notificações do Ventude: guarda os avisos (ilha, bandeja, lembretes) para ver depois.

Os avisos ficam numa lista curta (os 60 mais novos) salva no SQLite, então sobrevivem a reiniciar o app.
Música e volume não entram: são passageiros por natureza.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

from PySide6.QtCore import QObject, Signal

from ventude.core.store import Store

MAX_ITEMS = 60
DEDUPE_S = 8.0  # o mesmo aviso chegando pela ilha e pela bandeja conta uma vez só
META_KEY = "notifications_json"
SKIP_KINDS = {"music", "volume", "tool"}  # confirmações rápidas (cor copiada...) não ficam

# Grupo (o "app" de cada aviso na tela de Notificações): rótulo e ícone
GROUPS: dict[str, tuple[str, str]] = {
    "limits": ("Limites das IAs", "gauge"),
    "agenda": ("Agenda", "calendar"),
    "timer": ("Timer e Foco", "clock"),
    "hardware": ("Sistema", "cpu"),
    "tool": ("Ferramentas", "pipette"),
    "error": ("Ventude", "alert"),
    "alert": ("Ventude", "alert"),
    "info": ("Ventude", "bell"),
}


def group_of(kind: str) -> tuple[str, str]:
    return GROUPS.get(kind, GROUPS["info"])


@dataclass
class Notice:
    id: int
    title: str
    text: str = ""
    kind: str = "info"
    icon: str = "bell"
    color: str = ""
    at: float = 0.0  # time.time()
    read: bool = False


class NotificationCenter(QObject):
    changed = Signal()

    def __init__(self, store: Store | None = None) -> None:
        super().__init__()
        self.store = store
        self.items: list[Notice] = []
        self._next_id = 1
        self._load()

    # ---------- leitura ----------
    @property
    def unread(self) -> int:
        return sum(1 for n in self.items if not n.read)

    def newest_first(self) -> list[Notice]:
        return sorted(self.items, key=lambda n: (n.at, n.id), reverse=True)

    # ---------- escrita ----------
    def add(
        self,
        title: str,
        text: str = "",
        *,
        kind: str = "info",
        icon: str = "",
        color: str = "",
        now: float | None = None,
    ) -> Notice | None:
        title, text = (title or "").strip(), (text or "").strip()
        if not title or kind in SKIP_KINDS:
            return None
        now = time.time() if now is None else now
        for old in self.items:
            if old.title == title and old.text == text and now - old.at < DEDUPE_S:
                return None
        notice = Notice(self._next_id, title, text, kind, icon or group_of(kind)[1], color, now, False)
        self._next_id += 1
        self.items.append(notice)
        if len(self.items) > MAX_ITEMS:
            self.items = self.newest_first()[:MAX_ITEMS]
        self._save()
        self.changed.emit()
        return notice

    def dismiss(self, notice_id: int) -> None:
        before = len(self.items)
        self.items = [n for n in self.items if n.id != notice_id]
        if len(self.items) != before:
            self._save()
            self.changed.emit()

    def dismiss_group(self, kind: str) -> None:
        label = group_of(kind)[0]
        self.items = [n for n in self.items if group_of(n.kind)[0] != label]
        self._save()
        self.changed.emit()

    def clear(self) -> None:
        if self.items:
            self.items = []
            self._save()
            self.changed.emit()

    def mark_all_read(self) -> None:
        if any(not n.read for n in self.items):
            for n in self.items:
                n.read = True
            self._save()
            self.changed.emit()

    # ---------- SQLite ----------
    def _load(self) -> None:
        if self.store is None:
            return
        try:
            data = json.loads(self.store.meta(META_KEY) or "[]")
        except ValueError:
            data = []
        items: list[Notice] = []
        for raw in data if isinstance(data, list) else []:
            try:
                items.append(Notice(**{k: raw[k] for k in Notice.__dataclass_fields__ if k in raw}))
            except (TypeError, KeyError):
                continue
        self.items = items[-MAX_ITEMS:]
        self._next_id = max((n.id for n in self.items), default=0) + 1

    def _save(self) -> None:
        if self.store is not None:
            self.store.set_meta(META_KEY, json.dumps([asdict(n) for n in self.items], ensure_ascii=False))


__all__ = ["GROUPS", "MAX_ITEMS", "NotificationCenter", "Notice", "group_of"]
