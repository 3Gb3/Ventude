"""Doca flutuante centrada da Ventude Shell 3.0 (cápsula única em Ink com Switch/Pílula)."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

from ventude.services import dwm, winutil
from ventude.ui import brand, icons, motion, theme
from ventude.ui.shell.common import app_pixmap, draw_pixmap, shell_font
from ventude.ui.shell.dock_model import (
    BOTTOM_MARGIN,
    DOCK_H,
    DOCK_RADIUS,
    LABEL_BAND,
    UNPIN_DRAG_OFFSET,
    WINDOW_H,
    DockLayout,
    SlotModel,
    SlotRect,
)
from ventude.ui.shell.items import TaskItem

MIME_VENTUDE_ITEM = "application/x-ventude-item"
RESPIRO_TIMEOUT_S = 8.0
HOVER_LABEL_DELAY_MS = 220
HOVER_EASE = 0.42  # fração por quadro (16 ms): ~90 ms para o halo/elevação chegarem
HOVER_LIFT_PX = 4.0
HOVER_SCALE = 0.10
PINNED_IDLE_OPACITY = 0.9  # fixado sem janela: cor normal, só um pouco mais discreto
EDGE_STRIP_PX = 4
END_BUTTONS = ("background", "notifications", "controls")
END_ICONS = {"background": "chevron-up", "notifications": "bell", "controls": "sliders"}


class Dock(QWidget):
    """Cápsula flutuante única por monitor: pinta tudo em um paintEvent e conversa por sinais."""

    item_clicked = Signal(object, object)  # (TaskItem, anchor QRect global)
    item_middle_clicked = Signal(object)  # TaskItem
    item_menu_requested = Signal(object, object)  # (TaskItem, global QPoint)
    slot_hovered = Signal(object, object, bool)  # (TaskItem | None, anchor QRect global, entrou?)
    pin_reordered = Signal(list)  # list[int] dos pin_ids na nova ordem
    unpinned_reordered = Signal(list)  # list[str] das chaves dos apps abertos na nova ordem
    pin_unpinned = Signal(int)  # pin_id removido via Puf
    pin_dropped = Signal(str, str, str, int)  # (kind, label, target, insert_index)
    start_clicked = Signal()  # botão Iniciar: abre/fecha o Lançador sem precisar da tecla Windows
    end_clicked = Signal(str)  # "background" | "notifications" (Lançador nessa página) | "controls" (ilha)

    def __init__(self, ctx: object, shell: object, screen: object) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.ctx = ctx
        self.shell = shell
        self.screen_ = screen
        self.setWindowTitle("Ventude: doca")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

        self._model = SlotModel()
        self.items: list[TaskItem] = []
        self._layout: DockLayout = self._model.layout([], 640)
        self.slid = False
        self.fullscreen = False
        self._fs_hidden = False  # escondida de vez por um jogo/vídeo em tela cheia
        self.menu_open = False
        self.suppress_hover_label = False

        # Estado visual por slot
        self._hovered_key = ""
        self._hovered_overflow = False
        self._pressed_key = ""
        self._hovered_start = False
        self._hovered_end = ""
        self._pressed_end = ""
        self._pressed_start = False
        self._start_halo = 0.0
        self._show_hover_label = False
        self._halo_progress: dict[str, float] = {}
        self._slot_x: dict[str, float] = {}
        self._arrivals: dict[str, float] = {}
        self._indicator_w: dict[str, float] = {}
        self._launching_until: dict[str, float] = {}
        self._attention_keys: set[str] = set()
        self._capsule_cache: tuple[tuple[int, int, int, float], QPixmap] | None = None

        # Arrasto interno (reordenar ou desafixar com Puf) e externo
        self._drag_start_pos: QPointF | None = None
        self._drag_slot: SlotRect | None = None
        self._dragging_internal = False
        self._drag_pos = QPointF()
        self._drag_outside = False
        self._drag_insert_idx: int | None = None
        self._drag_section: str = "pinned"
        self._external_insert_idx: int | None = None

        # Animações de slide (Esquiva/Volta) e entrada (Maré)
        self._slide_offset = 0.0
        self._opacity = 1.0
        self._slide_tween = motion.Tween(self)
        self._slide_tween.progressed.connect(self._on_slide_step)
        self._entrance_tween = motion.Tween(self)
        self._entrance_tween.progressed.connect(self._on_entrance_step)

        self._hover_label_timer = QTimer(self, interval=HOVER_LABEL_DELAY_MS, singleShot=True)
        self._hover_label_timer.timeout.connect(self._reveal_hover_label)

        self._tick_timer = QTimer(self, interval=16)
        self._tick_timer.timeout.connect(self._tick)

    @property
    def dragging(self) -> bool:
        return self._dragging_internal or self._external_insert_idx is not None

    @property
    def current_layout(self) -> DockLayout:
        return self._layout

    # ---------- geometria ----------
    def screen_rect(self) -> QRect:
        return self.screen_.geometry()  # type: ignore[attr-defined]

    def shown_rect(self) -> QRect:
        area = self.screen_rect()
        width = max(360, int(area.width() * 0.72))
        x = area.left() + (area.width() - width) // 2
        y = area.bottom() - WINDOW_H + 1
        return QRect(x, y, width, WINDOW_H)

    def capsule_rect_on_screen(self) -> QRect:
        """Retângulo global exato da cápsula em repouso (para testes de esquiva)."""
        win = self.shown_rect()
        lay = self._layout
        return QRect(
            win.left() + round(lay.capsule_x),
            win.top() + round(lay.capsule_y),
            round(lay.capsule_w),
            round(lay.capsule_h),
        )

    def dock_top_global(self) -> int:
        return self.shown_rect().top() + round(self._layout.capsule_y)

    def slot_screen_rect(self, key: str) -> QRect:
        for slot in self._layout.slots:
            if slot.key == key:
                top_left = self.mapToGlobal(QPoint(round(slot.x), round(slot.y)))
                return QRect(top_left.x(), top_left.y(), round(slot.w), round(slot.h))
        return self.capsule_rect_on_screen()

    def slot_at(self, number: int) -> TaskItem | None:
        """Item 1-based para atalhos Win+1..9."""
        if 1 <= number <= len(self._layout.slots):
            return self._layout.slots[number - 1].item
        return None

    def place(self) -> None:
        rect = self.shown_rect()
        self.resize(rect.size())
        self._recompute_layout(snap=True)
        if not self._slide_tween.running and not self._entrance_tween.running:
            hidden_y = self._hidden_top()
            shown_y = rect.top()
            y = hidden_y if self.slid else shown_y
            self.move(rect.left(), y)

    def _hidden_top(self) -> int:
        area = self.screen_rect()
        return area.bottom() - EDGE_STRIP_PX + 1

    def _keep_on_top(self) -> None:
        app = QApplication.instance()
        if app is not None and app.platformName().lower() == "offscreen":
            return
        winutil.keep_topmost(int(self.winId()))

    def start_bar(self, entrance: bool = True, *, start_hidden: bool = False) -> None:
        self._slide_tween.stop()
        self._entrance_tween.stop()
        rect = self.shown_rect()
        self.resize(rect.size())
        self._recompute_layout(snap=True)
        if self.winId():
            dwm.square_corners(int(self.winId()))
        if start_hidden:
            self.slid = True
            self._slide_offset = 0.0
            self._opacity = 1.0
            self.move(rect.left(), self._hidden_top())
            self.setWindowOpacity(1.0)
            self.show()
            self._ensure_tick()
            return
        self.slid = False
        dur = motion.ms("mare", getattr(self.ctx, "settings", None))
        if entrance and dur > 0:
            self._slide_offset = float(DOCK_H + BOTTOM_MARGIN + 8)
            self._opacity = 0.0
            self.move(rect.left(), rect.top() + round(self._slide_offset))
            self.setWindowOpacity(0.0)
            self.show()
            self.raise_()
            self._keep_on_top()
            self._entrance_tween.play(dur, 0.0, 1.0, motion.named("enfase"))
        else:
            self._slide_offset = 0.0
            self._opacity = 1.0
            self.move(rect.topLeft())
            self.setWindowOpacity(1.0)
            self.show()
            self.raise_()
            self._keep_on_top()
        self._ensure_tick()

    def stop_bar(self) -> None:
        self._tick_timer.stop()
        self._hover_label_timer.stop()
        self._slide_tween.stop()
        self._entrance_tween.stop()
        self.slid = False
        self._fs_hidden = False
        self.hide()

    def sync_fullscreen(self, on: bool) -> None:
        """Em tela cheia, depois de recuar, a Doca some de vez (sem a faixa de 4 px sobre o jogo)."""
        self.fullscreen = on
        if on and self.slid and not self._slide_tween.running and self.isVisible():
            self._fs_hidden = True
            self.hide()
        elif not on and self._fs_hidden:
            self._fs_hidden = False
            self.move(self.shown_rect().left(), self._hidden_top())
            self.show()
            self.update()

    def slide(self, hide: bool) -> None:
        if not hide and self._fs_hidden:
            self._fs_hidden = False
            self.move(self.shown_rect().left(), self._hidden_top())
            self.show()
        if hide == self.slid:
            return
        if self._entrance_tween.running:
            self._entrance_tween.stop()
            self._slide_offset = 0.0
            self._opacity = 1.0
            self.setWindowOpacity(1.0)
        self.slid = hide
        if hide:
            self._hovered_key = ""
            self._hovered_overflow = False
            self._show_hover_label = False
            self._hover_label_timer.stop()
        else:
            self.raise_()
            self._keep_on_top()
            if hasattr(self.shell, "raise_launcher_above_dock"):
                self.shell.raise_launcher_above_dock()
        settings = getattr(self.ctx, "settings", None)
        dur = motion.ms("esquiva" if hide else "volta", settings)
        curve = motion.named("saida") if hide else motion.named("enfase")
        start_y = float(self.y())
        end_y = float(self._hidden_top() if hide else self.shown_rect().top())
        if dur <= 0:
            self.move(self.shown_rect().left(), round(end_y))
            self.update()
            return
        self._slide_tween.play(dur, start_y, end_y, curve)

    def _on_slide_step(self, value: float) -> None:
        self.move(self.shown_rect().left(), round(value))
        self.update()

    def _on_entrance_step(self, value: float) -> None:
        self._slide_offset = (1.0 - value) * float(DOCK_H + BOTTOM_MARGIN + 8)
        self._opacity = value
        self.move(self.shown_rect().left(), self.shown_rect().top() + round(self._slide_offset))
        self.setWindowOpacity(min(1.0, value * 1.15))
        self.update()

    # ---------- itens & animações ----------
    def set_items(self, items: list[TaskItem]) -> None:
        now = time.monotonic()
        old_keys = {i.key for i in self.items}
        old_had_windows = {i.key: bool(i.windows) for i in self.items}
        self.items = list(items)
        new_keys = {i.key for i in items}
        for key in new_keys - old_keys:
            if old_keys:
                self._arrivals[key] = 0.0
        for item in items:
            if item.windows and not old_had_windows.get(item.key, False):
                self._launching_until.pop(item.key, None)
            if item.windows and any(w.foreground for w in item.windows) and not item.other_monitor:
                self._attention_keys.discard(item.key)
        for stale in list(self._arrivals):
            if stale not in new_keys:
                self._arrivals.pop(stale, None)
        for stale in list(self._attention_keys):
            if stale not in new_keys:
                self._attention_keys.discard(stale)
        self._launching_until = {
            k: t for k, t in self._launching_until.items() if k in new_keys and t > now
        }
        self._recompute_layout(snap=not old_keys)
        self._ensure_tick()
        self.update()

    def bounce_key(self, key: str) -> None:
        self._launching_until[key] = time.monotonic() + RESPIRO_TIMEOUT_S
        self._ensure_tick()
        self.update()

    def flash_hwnd(self, hwnd: int) -> None:
        for item in self.items:
            if any(w.hwnd == hwnd for w in item.windows):
                self._attention_keys.add(item.key)
                self._ensure_tick()
                self.update()
                break

    def _recompute_layout(self, snap: bool = False) -> None:
        max_w = max(320, self.width() - 24)
        drag_key = self._drag_slot.key if (self._dragging_internal and self._drag_slot) else ""
        insert_idx = self._drag_insert_idx if self._dragging_internal else self._external_insert_idx
        self._layout = self._model.layout(
            self.items,
            self.width(),
            max_w,
            drag_key=drag_key,
            drag_outside=self._drag_outside,
            insert_index=insert_idx,
            insert_section=self._drag_section if self._dragging_internal else "pinned",
            start_button=True,
            end_buttons=END_BUTTONS,
        )
        reduced = motion.reduced_motion(getattr(self.ctx, "settings", None))
        for slot in self._layout.slots:
            if snap or reduced or slot.key not in self._slot_x:
                self._slot_x[slot.key] = slot.x
            target_ind = _target_indicator_width(slot.item)
            if snap or reduced or slot.key not in self._indicator_w:
                self._indicator_w[slot.key] = target_ind
        valid = {s.key for s in self._layout.slots}
        if self._dragging_internal and self._drag_slot:
            valid.add(self._drag_slot.key)
        for k in list(self._slot_x):
            if k not in valid:
                self._slot_x.pop(k, None)
        for k in list(self._indicator_w):
            if k not in valid:
                self._indicator_w.pop(k, None)

    def _ensure_tick(self) -> None:
        needs = self._needs_animation()
        if needs and not self._tick_timer.isActive():
            self._tick_timer.start()
        elif not needs and self._tick_timer.isActive():
            self._tick_timer.stop()

    def _needs_animation(self) -> bool:
        if motion.reduced_motion(getattr(self.ctx, "settings", None)):
            return False
        if self._arrivals or self._launching_until or self._attention_keys:
            return True
        if abs(self._start_halo - (1.0 if self._hovered_start else 0.0)) > 0.02:
            return True
        for slot in self._layout.slots:
            if abs(self._slot_x.get(slot.key, slot.x) - slot.x) > 0.35:
                return True
            if abs(self._indicator_w.get(slot.key, 0.0) - _target_indicator_width(slot.item)) > 0.25:
                return True
            target_halo = 1.0 if slot.key == self._hovered_key else 0.0
            if abs(self._halo_progress.get(slot.key, 0.0) - target_halo) > 0.02:
                return True
        return False

    def _tick(self) -> None:
        now = time.monotonic()
        dirty = False
        # Expirar Respiro aos 8 segundos
        for key, until in list(self._launching_until.items()):
            if now >= until:
                self._launching_until.pop(key, None)
                dirty = True
            else:
                dirty = True
        if self._attention_keys:
            dirty = True
        target_start = 1.0 if self._hovered_start else 0.0
        if abs(self._start_halo - target_start) > 0.02:
            self._start_halo += (target_start - self._start_halo) * HOVER_EASE
            dirty = True
        elif self._start_halo != target_start:
            self._start_halo = target_start
            dirty = True
        for key in list(self._arrivals):
            nxt = self._arrivals[key] + 0.067
            if nxt >= 1.0:
                self._arrivals.pop(key, None)
            else:
                self._arrivals[key] = nxt
            dirty = True
        for slot in self._layout.slots:
            cur_x = self._slot_x.get(slot.key, slot.x)
            if abs(cur_x - slot.x) > 0.35:
                self._slot_x[slot.key] = cur_x + (slot.x - cur_x) * 0.28
                dirty = True
            elif cur_x != slot.x:
                self._slot_x[slot.key] = slot.x
                dirty = True
            target_ind = _target_indicator_width(slot.item)
            cur_ind = self._indicator_w.get(slot.key, target_ind)
            if abs(cur_ind - target_ind) > 0.25:
                self._indicator_w[slot.key] = cur_ind + (target_ind - cur_ind) * 0.26
                dirty = True
            elif cur_ind != target_ind:
                self._indicator_w[slot.key] = target_ind
                dirty = True
            target_h = 1.0 if slot.key == self._hovered_key else 0.0
            cur_h = self._halo_progress.get(slot.key, 0.0)
            if abs(cur_h - target_h) > 0.02:
                self._halo_progress[slot.key] = cur_h + (target_h - cur_h) * HOVER_EASE
                dirty = True
            elif cur_h != target_h:
                self._halo_progress[slot.key] = target_h
                dirty = True
        if not self._needs_animation():
            self._tick_timer.stop()
        if dirty:
            self.update()

    def _menus_open(self) -> bool:
        fn = getattr(self.shell, "menus_open", None)
        return bool(callable(fn) and fn())

    def _reveal_hover_label(self) -> None:
        if (
            (self._hovered_key or self._hovered_start or self._hovered_end)
            and not self.suppress_hover_label
            and not self._dragging_internal
            and not self._menus_open()
        ):
            self._show_hover_label = True
            self.update()

    # ---------- pintura ----------
    def paintEvent(self, _event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.slid and not self._slide_tween.running:
            mode = getattr(getattr(self.ctx, "settings", None), "dock_mode", "esquivar")
            if not self.fullscreen and mode != "oculta":
                alca = QRectF((self.width() - 28) / 2.0, 0.5, 28.0, 2.5)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 95))
                p.drawRoundedRect(alca, 1.25, 1.25)
            return

        lay = self._layout
        cap = QRectF(lay.capsule_x, lay.capsule_y, lay.capsule_w, lay.capsule_h)
        self._paint_capsule(p, cap)

        now = time.monotonic()
        reduced = motion.reduced_motion(getattr(self.ctx, "settings", None))

        self._paint_start(p, reduced)

        for slot in lay.slots:
            self._paint_slot(p, slot, now, reduced)

        self._paint_ends(p)

        for sep_x in (lay.start_separator_x, lay.separator_x, lay.end_separator_x):
            if sep_x is not None:
                p.setPen(QPen(QColor(theme.HAIRLINE), 1.0))
                p.drawLine(QPointF(sep_x, cap.top() + 12), QPointF(sep_x, cap.bottom() - 12))

        if lay.overflow_rect is not None and lay.overflow_count > 0:
            ox, oy, ow, oh = lay.overflow_rect
            or_rect = QRectF(ox + 2, oy + 4, ow - 4, oh - 8)
            p.setPen(QPen(QColor(theme.HAIRLINE), 1.0))
            p.setBrush(QColor(theme.HALO if self._hovered_overflow else theme.INK_KEY))
            p.drawRoundedRect(or_rect, theme.R_ITEM, theme.R_ITEM)
            p.setPen(QColor(theme.TEXT))
            p.setFont(shell_font(11, tabular=True))
            p.drawText(or_rect, Qt.AlignmentFlag.AlignCenter, f"+{lay.overflow_count}")

        # Mini-ilha acima do ícone com hover depois de 300ms (quando sem thumb strip/lançador visível)
        if (
            self._show_hover_label
            and self._hovered_start
            and lay.start_rect is not None
            and not self.suppress_hover_label
            and not self._menus_open()
        ):
            sx, _sy, sw, _sh = lay.start_rect
            self._paint_label_pill(p, sx + sw / 2.0, "Iniciar")
        end = lay.end_rect(self._hovered_end) if self._hovered_end else None
        label_ok = self._show_hover_label and not self.suppress_hover_label and not self._menus_open()
        if label_ok and end is not None:
            self._paint_label_pill(p, end[0] + end[2] / 2.0, self._end_label(self._hovered_end))
        if (
            self._show_hover_label
            and self._hovered_key
            and not self.suppress_hover_label
            and not self._menus_open()
        ):
            for slot in lay.slots:
                if slot.key == self._hovered_key:
                    self._paint_hover_label(p, slot)
                    break

        # Ícone arrastado seguindo o cursor (e rótulo Puf se acima da doca)
        if self._dragging_internal and self._drag_slot is not None:
            self._paint_drag_ghost(p, self._drag_slot)

    def _paint_capsule(self, p: QPainter, cap: QRectF) -> None:
        """Fundo da cápsula em cache: o hover só repinta ícones, nunca o gradiente/contorno."""
        ratio = self.devicePixelRatioF()
        key = (round(cap.width()), round(cap.height()), DOCK_RADIUS, ratio)
        if self._capsule_cache is None or self._capsule_cache[0] != key:
            pad = 2
            pm = QPixmap(round((cap.width() + pad * 2) * ratio), round((cap.height() + pad * 2) * ratio))
            pm.setDevicePixelRatio(ratio)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            brand.paint_ink(painter, QRectF(pad, pad, cap.width(), cap.height()), DOCK_RADIUS, outer_rim=True)
            painter.end()
            self._capsule_cache = (key, pm)
        p.drawPixmap(QPointF(cap.left() - 2, cap.top() - 2), self._capsule_cache[1])

    def _slot_pixmap(self, item: TaskItem, size: int, awake: bool) -> QPixmap:  # noqa: ARG002
        """Ícone real do app, sempre em cores (nada de versão cinza congelada no cache)."""
        return app_pixmap(self.ctx, item.icon_kind, item.icon_target, size, "window")

    def _paint_slot(self, p: QPainter, slot: SlotRect, now: float, reduced: bool) -> None:
        item = slot.item
        x = self._slot_x.get(slot.key, slot.x)
        rect = QRectF(x, slot.y, slot.w, slot.h)
        hovered = slot.key == self._hovered_key
        pressed = slot.key == self._pressed_key
        halo_t = 1.0 if (hovered and reduced) else self._halo_progress.get(slot.key, 0.0)
        arrival = self._arrivals.get(slot.key, 1.0)
        scale = motion.out_back(1.1)(arrival) if arrival < 1.0 else 1.0
        lift = 0.0
        if not reduced:
            scale *= 1.0 + HOVER_SCALE * halo_t
            lift = HOVER_LIFT_PX * halo_t
        if pressed:
            scale *= 0.90
            lift *= 0.4

        if halo_t > 0.02 or item.active:
            p.setPen(Qt.PenStyle.NoPen)
            if pressed:
                color = QColor(theme.HALO_PRESS)
            elif halo_t > 0.02:
                color = QColor(255, 255, 255, round(20 * halo_t))
            else:
                color = QColor(255, 255, 255, 10)
            p.setBrush(color)
            p.drawRoundedRect(rect.adjusted(1, 2, -1, -2), theme.R_ITEM, theme.R_ITEM)

        # 26 px: perto dos 24 px da barra do Windows (o cache carrega em 2x, então fica nítido)
        icon_base = 26 if slot.w >= 36 else max(18, round(slot.w * 0.72))
        icon_size = icon_base * scale
        cx, cy = rect.center().x(), rect.center().y() - 2.0 - lift
        awake = bool(item.windows) or hovered or pressed or (slot.key in self._launching_until)
        px = self._slot_pixmap(item, icon_base, awake)

        opacity = min(1.0, 0.25 + arrival) if arrival < 1.0 else (1.0 if awake else PINNED_IDLE_OPACITY)
        # Respiro suave quando o app está abrindo (expira em 8s)
        until = self._launching_until.get(slot.key)
        if until is not None and not reduced:
            phase = (RESPIRO_TIMEOUT_S - max(0.0, until - now)) * (math.pi * 2 / 1.1)
            opacity *= 0.72 + 0.28 * math.sin(phase)

        p.save()
        p.setOpacity(max(0.15, min(1.0, opacity)))
        draw_pixmap(
            p,
            px,
            QRectF(cx - icon_size / 2.0, cy - icon_size / 2.0, icon_size, icon_size),
        )
        p.restore()

        # Ponto / cápsula de estado abaixo do ícone
        ind_w = self._indicator_w.get(slot.key, _target_indicator_width(item))
        if ind_w > 0.5:
            attention = slot.key in self._attention_keys
            dot_y = rect.bottom() - 4.0
            dot_rect = QRectF(cx - ind_w / 2.0, dot_y, ind_w, 3.0)
            if item.other_monitor:
                p.setPen(QPen(QColor(theme.DOT_OPEN), 1.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(dot_rect, 1.5, 1.5)
            else:
                if attention:
                    dot_color = QColor(theme.DOT_ATTENTION)
                    if not reduced:
                        pulse = 0.6 + 0.4 * math.sin(now * math.pi * 2 / 0.8)
                        dot_color.setAlphaF(pulse)
                elif item.active:
                    dot_color = QColor(theme.DOT_FOCUS)
                else:
                    dot_color = QColor(theme.DOT_OPEN)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(dot_color)
                p.drawRoundedRect(dot_rect, 1.5, 1.5)

    def _launcher_open(self) -> bool:
        launcher = getattr(self.shell, "launcher", None)
        return bool(launcher is not None and launcher.isVisible() and not getattr(launcher, "closing", False))

    def _paint_start(self, p: QPainter, reduced: bool) -> None:
        """Botão Iniciar na ponta esquerda: o tornadinho da Ventude, abre o Lançador."""
        start = self._layout.start_rect
        if start is None:
            return
        rect = QRectF(*start)
        halo_t = (1.0 if self._hovered_start else 0.0) if reduced else self._start_halo
        lift = 0.0 if reduced else HOVER_LIFT_PX * halo_t
        scale = 1.0 if reduced else 1.0 + HOVER_SCALE * halo_t
        if self._pressed_start:
            scale *= 0.90
            lift *= 0.4
        is_open = self._launcher_open()
        if halo_t > 0.02 or is_open or self._pressed_start:
            p.setPen(Qt.PenStyle.NoPen)
            if self._pressed_start:
                color = QColor(theme.HALO_PRESS)
            elif halo_t > 0.02:
                color = QColor(255, 255, 255, round(20 * halo_t))
            else:
                color = QColor(255, 255, 255, 10)
            p.setBrush(color)
            p.drawRoundedRect(rect.adjusted(1, 2, -1, -2), theme.R_ITEM, theme.R_ITEM)
        icon = (26 if rect.width() >= 36 else max(18, round(rect.width() * 0.72))) * scale
        cx, cy = rect.center().x(), rect.center().y() - 2.0 - lift
        brand.paint_tornado(p, QRectF(cx - icon / 2.0, cy - icon / 2.0, icon, icon), background=False)
        if is_open:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.DOT_FOCUS))
            p.drawRoundedRect(QRectF(cx - 7.0, rect.bottom() - 4.0, 14.0, 3.0), 1.5, 1.5)

    def _unread(self) -> int:
        center = getattr(self.ctx, "notifications", None)
        return int(getattr(center, "unread", 0) or 0)

    def _end_label(self, name: str) -> str:
        if name == "notifications":
            unread = self._unread()
            return f"Notificações · {unread} nova{'s' if unread > 1 else ''}" if unread else "Notificações"
        if name == "controls":
            return "Controles · Win + A"
        return "Em segundo plano"

    def _controls_open(self) -> bool:
        island = getattr(self.shell, "island", None)
        mode = getattr(getattr(island, "mode", None), "value", "")
        return mode == "controls" and bool(island is not None and island.isVisible())

    def _launcher_page(self) -> str:
        launcher = getattr(self.shell, "launcher", None)
        if not self._launcher_open() or launcher is None or not hasattr(launcher, "current_page"):
            return ""
        return str(launcher.current_page())

    def _paint_ends(self, p: QPainter) -> None:
        """Ponta direita: segundo plano (seta), Notificações (sino com selo) e Controles da ilha."""
        page = self._launcher_page()
        controls = self._controls_open()
        for name, raw in self._layout.end_rects:
            rect = QRectF(*raw)
            active = controls if name == "controls" else page == name
            if self._pressed_end == name or self._hovered_end == name or active:
                p.setPen(Qt.PenStyle.NoPen)
                if self._pressed_end == name:
                    p.setBrush(QColor(theme.HALO_PRESS))
                elif self._hovered_end == name:
                    p.setBrush(QColor(255, 255, 255, 20))
                else:
                    p.setBrush(QColor(255, 255, 255, 10))
                p.drawRoundedRect(rect.adjusted(0, 1, 0, -1), theme.R_ITEM, theme.R_ITEM)
            icon_name = END_ICONS.get(name, "dot")
            color = theme.ACCENT if active else theme.TEXT_2
            size = 16.0
            draw_pixmap(
                p,
                icons.pixmap(icon_name, color, int(size)),
                QRectF(rect.center().x() - size / 2, rect.center().y() - size / 2, size, size),
            )
            if name == "notifications" and self._unread():
                p.setPen(QPen(QColor(theme.INK), 1.5))
                p.setBrush(QColor(theme.ACCENT))
                p.drawEllipse(QPointF(rect.center().x() + 5.5, rect.center().y() - 6.0), 3.6, 3.6)

    def _set_end_hover(self, name: str) -> None:
        if name == self._hovered_end:
            return
        self._hovered_end = name
        if name:
            self._show_hover_label = False
            self._hover_label_timer.start()
        elif not (self._hovered_key or self._hovered_start):
            self._show_hover_label = False
            self._hover_label_timer.stop()
        self.update()

    def _paint_hover_label(self, p: QPainter, slot: SlotRect) -> None:
        text = slot.item.label
        if slot.item.other_monitor:
            text = f"{text} · outra tela"
        self._paint_label_pill(p, self._slot_x.get(slot.key, slot.x) + slot.w / 2.0, text)

    def _paint_label_pill(self, p: QPainter, cx: float, text: str) -> None:
        font = shell_font(11)
        p.setFont(font)
        text_w = p.fontMetrics().horizontalAdvance(text)
        pill_w = min(220.0, float(text_w + 22))
        pill_h = 26.0
        px = max(6.0, min(cx - pill_w / 2.0, self.width() - pill_w - 6.0))
        py = max(4.0, float(LABEL_BAND - pill_h - 8))
        rect = QRectF(px, py, pill_w, pill_h)
        brand.paint_ink(p, rect, pill_h / 2.0, bg=theme.INK_RAISED, outer_rim=True)
        p.setPen(QColor(theme.TEXT))
        p.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_drag_ghost(self, p: QPainter, slot: SlotRect) -> None:
        icon_size = max(24, round(slot.w * 0.7))
        px = self._slot_pixmap(slot.item, icon_size, True)
        cx, cy = self._drag_pos.x(), self._drag_pos.y()
        p.save()
        p.setOpacity(0.55 if self._drag_outside else 0.92)
        draw_pixmap(
            p,
            px,
            QRectF(cx - icon_size / 2.0, cy - icon_size / 2.0, float(icon_size), float(icon_size)),
        )
        p.restore()
        if self._drag_outside:
            font = shell_font(11)
            p.setFont(font)
            msg = "Solte para desafixar"
            tw = p.fontMetrics().horizontalAdvance(msg) + 20
            pill = QRectF(max(6.0, min(cx - tw / 2.0, self.width() - tw - 6.0)), 6.0, float(tw), 24.0)
            brand.paint_ink(p, pill, 12.0, bg=theme.INK_RAISED, outer_rim=True)
            p.setPen(QColor(theme.WARN))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, msg)

    # ---------- mouse, hover e arrasto ----------
    def _set_start_hover(self, on: bool) -> None:
        if on == self._hovered_start:
            return
        self._hovered_start = on
        if on:
            self._show_hover_label = False
            self._hover_label_timer.start()
        elif not self._hovered_key:  # indo direto para um ícone: o timer agora é dele
            self._show_hover_label = False
            self._hover_label_timer.stop()
        self._ensure_tick()
        self.update()

    def _set_hover(self, slot: SlotRect | None, overflow: bool = False) -> None:
        new_key = slot.key if slot is not None else ""
        if new_key == self._hovered_key and overflow == self._hovered_overflow:
            return
        old_key = self._hovered_key
        self._hovered_key = new_key
        self._hovered_overflow = overflow
        self._show_hover_label = False
        self._hover_label_timer.stop()
        if old_key:
            old_slot = next((s for s in self._layout.slots if s.key == old_key), None)
            if old_slot is not None:
                self.slot_hovered.emit(old_slot.item, self.slot_screen_rect(old_key), False)
        if slot is not None:
            self._hover_label_timer.start()
            self.slot_hovered.emit(slot.item, self.slot_screen_rect(slot.key), True)
        self._ensure_tick()
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self._pressed_key = ""
        self._pressed_start = False
        self._pressed_end = ""
        self._set_start_hover(False)
        self._set_end_hover("")
        if not self._dragging_internal:
            self._set_hover(None, False)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._drag_slot is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            if not self._dragging_internal:
                if (pos - (self._drag_start_pos or pos)).manhattanLength() >= 6:
                    self._dragging_internal = True
                    self._show_hover_label = False
                    self._hover_label_timer.stop()
                    self._set_hover(None, False)
            if self._dragging_internal:
                self._drag_pos = pos
                is_pinned = self._drag_section == "pinned"
                self._drag_outside = is_pinned and pos.y() < (self._layout.capsule_y - UNPIN_DRAG_OFFSET)
                section_n = sum(
                    1 for i in self.items if (i.pinned is is_pinned) and i.key != self._drag_slot.key
                )
                self._drag_insert_idx = (
                    None
                    if self._drag_outside
                    else self._layout.insert_index_at(pos.x(), section_n, pinned=is_pinned)
                )
                self._recompute_layout()
                self._ensure_tick()
                self.update()
                return
        on_end = self._layout.hit_end(pos.x(), pos.y())
        on_start = self._layout.hit_start(pos.x(), pos.y())
        slot = None if (on_start or on_end) else self._layout.hit(pos.x(), pos.y())
        overflow = False if (slot is not None or on_start) else self._layout.hit_overflow(pos.x(), pos.y())
        self._set_hover(slot, overflow)
        self._set_start_hover(on_start)
        self._set_end_hover(on_end)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if event.button() == Qt.MouseButton.LeftButton and self._layout.hit_start(pos.x(), pos.y()):
            self._pressed_start = True
            self.update()
            return
        end = self._layout.hit_end(pos.x(), pos.y())
        if end:
            if event.button() == Qt.MouseButton.LeftButton:
                self._pressed_end = end
                self.update()
            return
        slot = self._layout.hit(pos.x(), pos.y())
        if event.button() == Qt.MouseButton.LeftButton and slot is not None:
            self._pressed_key = slot.key
            self._drag_start_pos = pos
            self._drag_slot = slot
            self._drag_pos = pos
            self._drag_section = "pinned" if slot.item.pinned else "unpinned"
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        was_pressed = self._pressed_key
        self._pressed_key = ""
        if self._pressed_start:
            self._pressed_start = False
            if event.button() == Qt.MouseButton.LeftButton and self._layout.hit_start(pos.x(), pos.y()):
                self.start_clicked.emit()
            self.update()
            return
        if self._pressed_end:
            pressed, self._pressed_end = self._pressed_end, ""
            same = self._layout.hit_end(pos.x(), pos.y()) == pressed
            if event.button() == Qt.MouseButton.LeftButton and same:
                self.end_clicked.emit(pressed)
            self.update()
            return
        if self._dragging_internal and self._drag_slot is not None:
            dragged = self._drag_slot
            outside = self._drag_outside
            insert_idx = self._drag_insert_idx
            section = self._drag_section
            self._drag_slot = None
            self._drag_start_pos = None
            self._dragging_internal = False
            self._drag_outside = False
            self._drag_insert_idx = None
            self._drag_section = "pinned"
            if section == "pinned":
                if outside and dragged.item.pin_id:
                    self.pin_unpinned.emit(dragged.item.pin_id)
                elif insert_idx is not None and dragged.item.pin_id:
                    order = [i.pin_id for i in self.items if i.pinned and i.pin_id != dragged.item.pin_id]
                    clamped = max(0, min(insert_idx, len(order)))
                    order.insert(clamped, dragged.item.pin_id)
                    self.pin_reordered.emit(order)
            elif insert_idx is not None:
                order = [i.key for i in self.items if not i.pinned and i.key != dragged.key]
                clamped = max(0, min(insert_idx, len(order)))
                order.insert(clamped, dragged.key)
                self.unpinned_reordered.emit(order)
            self._recompute_layout(snap=True)
            self.update()
            return

        self._drag_slot = None
        self._drag_start_pos = None
        slot = self._layout.hit(pos.x(), pos.y())
        if event.button() == Qt.MouseButton.LeftButton:
            if slot is not None and slot.key == was_pressed:
                self.item_clicked.emit(slot.item, self.slot_screen_rect(slot.key))
            elif self._layout.hit_overflow(pos.x(), pos.y()):
                self._show_overflow_menu(event.globalPosition().toPoint())
        elif event.button() == Qt.MouseButton.MiddleButton and slot is not None:
            self.item_middle_clicked.emit(slot.item)
        elif event.button() == Qt.MouseButton.RightButton and slot is not None:
            self.item_menu_requested.emit(slot.item, event.globalPosition().toPoint())
        self.update()

    def _show_overflow_menu(self, global_pos: QPoint) -> None:
        if not self._layout.overflow_items:
            return
        menu = brand.ink_menu(self)
        for item in self._layout.overflow_items:
            action = menu.addAction(item.label)
            action.triggered.connect(
                lambda _=False, it=item: self.item_clicked.emit(it, self.capsule_rect_on_screen())
            )
        self.menu_open = True
        try:
            menu.exec(global_pos)
        finally:
            self.menu_open = False

    # ---------- drag & drop externo ----------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if _extract_drop_item(event.mimeData()) is not None:
            event.acceptProposedAction()
            self._update_external_insert(event.position().x())

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if _extract_drop_item(event.mimeData()) is not None:
            event.acceptProposedAction()
            self._update_external_insert(event.position().x())

    def dragLeaveEvent(self, _event: QDragLeaveEvent) -> None:
        if self._external_insert_idx is not None:
            self._external_insert_idx = None
            self._recompute_layout()
            self.update()

    def dropEvent(self, event: QDropEvent) -> None:
        payload = _extract_drop_item(event.mimeData())
        idx = self._external_insert_idx if self._external_insert_idx is not None else 0
        self._external_insert_idx = None
        self._recompute_layout()
        self.update()
        if payload is not None:
            kind, label_text, target = payload
            self.pin_dropped.emit(kind, label_text, target, idx)
            event.acceptProposedAction()

    def _update_external_insert(self, x: float) -> None:
        pinned_n = sum(1 for i in self.items if i.pinned)
        idx = self._layout.insert_index_at(x, pinned_n)
        if idx != self._external_insert_idx:
            self._external_insert_idx = idx
            self._recompute_layout()
            self._ensure_tick()
            self.update()

    # ---------- fechamento / winutil compat ----------
    def hwnd(self) -> int:
        return int(self.winId())

    def hide_taskbar_and_restore(self) -> None:  # compat opcional
        self.stop_bar()


def _target_indicator_width(item: TaskItem) -> float:
    if not item.windows:
        return 0.0
    if item.active:
        return 14.0
    return 4.0


def _extract_drop_item(mime: object) -> tuple[str, str, str] | None:
    if mime is None:
        return None
    if hasattr(mime, "hasFormat") and mime.hasFormat(MIME_VENTUDE_ITEM):  # type: ignore[attr-defined]
        try:
            raw = bytes(mime.data(MIME_VENTUDE_ITEM)).decode("utf-8")  # type: ignore[attr-defined]
            data = json.loads(raw)
            kind = str(data.get("kind", ""))
            label_text = str(data.get("label", ""))
            target = str(data.get("target", ""))
            if kind and target:
                return (kind, label_text or Path(target).stem, target)
        except (ValueError, UnicodeDecodeError):
            return None
    if hasattr(mime, "hasUrls") and mime.hasUrls():  # type: ignore[attr-defined]
        for url in mime.urls():  # type: ignore[attr-defined]
            if url.isLocalFile():
                path = url.toLocalFile()
                p = Path(path)
                if p.is_dir():
                    return ("folder", p.name or path, path)
                if p.suffix.lower() in (".exe", ".lnk"):
                    return ("app", p.stem, path)
                if path:
                    return ("file", p.name, path)
    return None


__all__ = ["Dock", "MIME_VENTUDE_ITEM"]
