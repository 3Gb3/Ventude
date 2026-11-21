"""Modelo puro (sem Qt) da Doca: layout, overflow, esquiva (dodge) e borda de revelação."""

from __future__ import annotations

from dataclasses import dataclass

from ventude.services.taskbar import Rect
from ventude.ui.shell.items import TaskItem

DOCK_H = 46
DOCK_RADIUS = 23
BOTTOM_MARGIN = 10
LABEL_BAND = 48
WINDOW_H = LABEL_BAND + DOCK_H + BOTTOM_MARGIN  # 104
RESERVE_H = 58

PAD_X = 12
ICON_MAX = 38
ICON_MIN = 30
GAP = 4
DIVIDER_W = 13
UNPIN_DRAG_OFFSET = 48


@dataclass(frozen=True)
class SlotRect:
    key: str
    index: int
    x: float
    y: float
    w: float
    h: float
    item: TaskItem


@dataclass(frozen=True)
class DockLayout:
    capsule_x: float
    capsule_y: float
    capsule_w: float
    capsule_h: float
    slot_size: int
    slots: tuple[SlotRect, ...]
    separator_x: float | None = None
    overflow_rect: tuple[float, float, float, float] | None = None
    overflow_items: tuple[TaskItem, ...] = ()
    start_rect: tuple[float, float, float, float] | None = None  # botão Iniciar (abre o Lançador)
    start_separator_x: float | None = None

    @property
    def overflow_count(self) -> int:
        return len(self.overflow_items)

    def hit(self, x: float, y: float) -> SlotRect | None:
        if not (self.capsule_y <= y <= self.capsule_y + self.capsule_h):
            return None
        for slot in self.slots:
            if slot.x - GAP / 2 <= x <= slot.x + slot.w + GAP / 2:
                return slot
        return None

    def hit_start(self, x: float, y: float) -> bool:
        if self.start_rect is None:
            return False
        sx, _sy, sw, _sh = self.start_rect
        in_band = self.capsule_y <= y <= self.capsule_y + self.capsule_h
        return in_band and sx - GAP / 2 <= x <= sx + sw + GAP / 2

    def hit_overflow(self, x: float, y: float) -> bool:
        if self.overflow_rect is None:
            return False
        ox, oy, ow, oh = self.overflow_rect
        return ox <= x <= ox + ow and oy <= y <= oy + oh

    def insert_index_at(self, x: float, count: int, *, pinned: bool = True) -> int:
        section_slots = [s for s in self.slots if s.item.pinned is pinned]
        if not section_slots:
            return 0
        for idx, slot in enumerate(section_slots):
            if x < slot.x + slot.w / 2:
                return idx
        return count


def _content_width(
    n_slots: int, slot_size: int, has_sep: bool, has_overflow: bool, has_start: bool = False
) -> float:
    total_units = n_slots + (1 if has_overflow else 0)
    if total_units == 0:
        return float(PAD_X * 2 + slot_size)
    w = PAD_X * 2 + total_units * slot_size + max(0, total_units - 1) * GAP
    if has_sep:
        w += DIVIDER_W - GAP
    if has_start:
        w += slot_size + DIVIDER_W  # botão Iniciar + divisória antes dos apps
    return float(w)


class SlotModel:
    """Calcula a geometria centrada da cápsula da Doca com encolhimento e +N overflow."""

    def layout(
        self,
        items: list[TaskItem],
        window_w: int,
        max_w: int | None = None,
        *,
        drag_key: str = "",
        drag_outside: bool = False,
        insert_index: int | None = None,
        insert_section: str = "pinned",
        start_button: bool = False,
    ) -> DockLayout:
        limit = max(ICON_MIN + PAD_X * 2, max_w or (window_w - 32))
        visible_items = [
            it for it in items if not (drag_key and not drag_outside and it.key == drag_key)
        ]
        # Quando drag_key está sendo arrastado dentro da doca, contamos um espaço vazio para o gap
        has_gap = insert_index is not None and not drag_outside
        pinned_gap = has_gap and insert_section == "pinned"
        unpinned_gap = has_gap and insert_section == "unpinned"
        if drag_key and drag_outside:
            visible_items = [it for it in items if it.key != drag_key]

        overflow: list[TaskItem] = []
        slot_size = ICON_MAX

        def split_counts(candidates: list[TaskItem]) -> tuple[int, bool]:
            pinned_n = sum(1 for it in candidates if it.pinned)
            unpinned_n = len(candidates) - pinned_n
            has_sep = (pinned_n > 0 or pinned_gap) and (unpinned_n > 0 or unpinned_gap)
            return len(candidates) + (1 if has_gap else 0), has_sep

        n_units, has_sep = split_counts(visible_items)
        if _content_width(n_units, ICON_MAX, has_sep, False, start_button) > limit:
            # Encolhe até ICON_MIN antes de jogar excedente para +N
            fitted = ICON_MIN
            for candidate_sz in range(ICON_MAX, ICON_MIN - 1, -1):
                if _content_width(n_units, candidate_sz, has_sep, False, start_button) <= limit:
                    fitted = candidate_sz
                    break
            slot_size = fitted
            if _content_width(n_units, slot_size, has_sep, False, start_button) > limit:
                kept = list(visible_items)
                while kept:
                    idx_pop = len(kept) - 1
                    overflow.insert(0, kept.pop(idx_pop))
                    n_units, has_sep = split_counts(kept)
                    if _content_width(n_units, slot_size, has_sep, True, start_button) <= limit:
                        break
                visible_items = kept

        n_units, has_sep = split_counts(visible_items)
        has_overflow = len(overflow) > 0
        capsule_w = _content_width(n_units, slot_size, has_sep, has_overflow, start_button)
        capsule_x = (window_w - capsule_w) / 2.0
        capsule_y = float(WINDOW_H - BOTTOM_MARGIN - DOCK_H)
        slot_y = capsule_y + (DOCK_H - slot_size) / 2.0

        pinned = [it for it in visible_items if it.pinned]
        unpinned = [it for it in visible_items if not it.pinned]

        slots: list[SlotRect] = []
        cursor_x = capsule_x + PAD_X
        start_rect: tuple[float, float, float, float] | None = None
        start_separator_x: float | None = None
        if start_button:
            start_rect = (cursor_x, slot_y, float(slot_size), float(slot_size))
            if n_units > 0 or has_overflow:
                cursor_x += slot_size + DIVIDER_W
                start_separator_x = cursor_x - DIVIDER_W / 2.0
        gap_pos = max(0, min(insert_index, len(pinned))) if pinned_gap and insert_index is not None else -1
        unpinned_gap_pos = (
            max(0, min(insert_index, len(unpinned))) if unpinned_gap and insert_index is not None else -1
        )

        visual_idx = 0
        for idx, item in enumerate(pinned):
            if idx == gap_pos:
                cursor_x += slot_size + GAP
            slots.append(
                SlotRect(
                    key=item.key,
                    index=visual_idx,
                    x=cursor_x,
                    y=slot_y,
                    w=float(slot_size),
                    h=float(slot_size),
                    item=item,
                )
            )
            cursor_x += slot_size + GAP
            visual_idx += 1

        if gap_pos == len(pinned) and pinned_gap:
            cursor_x += slot_size + GAP

        separator_x: float | None = None
        if has_sep:
            separator_x = cursor_x - GAP + DIVIDER_W / 2.0
            cursor_x += DIVIDER_W - GAP

        for idx, item in enumerate(unpinned):
            if idx == unpinned_gap_pos:
                cursor_x += slot_size + GAP
            slots.append(
                SlotRect(
                    key=item.key,
                    index=visual_idx,
                    x=cursor_x,
                    y=slot_y,
                    w=float(slot_size),
                    h=float(slot_size),
                    item=item,
                )
            )
            cursor_x += slot_size + GAP
            visual_idx += 1

        if unpinned_gap_pos == len(unpinned) and unpinned_gap:
            cursor_x += slot_size + GAP

        overflow_rect: tuple[float, float, float, float] | None = None
        if has_overflow:
            overflow_rect = (cursor_x, slot_y, float(slot_size), float(slot_size))

        return DockLayout(
            capsule_x=capsule_x,
            capsule_y=capsule_y,
            capsule_w=capsule_w,
            capsule_h=float(DOCK_H),
            slot_size=slot_size,
            slots=tuple(slots),
            separator_x=separator_x,
            overflow_rect=overflow_rect,
            overflow_items=tuple(overflow),
            start_rect=start_rect,
            start_separator_x=start_separator_x,
        )


def should_dodge(
    dock_rect: Rect,
    fg_rect: Rect | None,
    dock_mode: str,
    *,
    menu_open: bool = False,
    thumb_open: bool = False,
    dragging: bool = False,
    hover_reveal: bool = False,
    fullscreen: bool = False,
) -> bool:
    """Decide se a Doca deve recuar para baixo neste instante.

    Em tela cheia (jogos, vídeos) encostar o mouse embaixo não traz a Doca: só o Lançador
    (tecla Windows) ou um arraste a chamam de volta.
    """
    if menu_open or dragging:
        return False
    if fullscreen:
        return True
    if thumb_open or hover_reveal:
        return False
    if dock_mode == "sempre":
        return False
    if dock_mode == "oculta":
        return True
    # Modo "esquivar": só recua se a janela ativa encostar/cobrir a área da cápsula
    if fg_rect is None:
        return False
    dl, dt, dr, db = dock_rect
    fl, ft, fr, fb = fg_rect
    return fl < dr and fr > dl and ft < db and fb > dt


def edge_reveal(cursor_x: int, cursor_y: int, screen_rect: Rect, edge_px: int = 4) -> bool:
    """Cursor encostou na borda inferior do monitor?"""
    left, _top, right, bottom = screen_rect
    return left <= cursor_x < right and (bottom - edge_px) <= cursor_y <= (bottom + 2)
