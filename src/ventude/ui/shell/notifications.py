"""Tela de Notificações do Ventude (página do Lançador, também aberta pelo sino da Doca).

Mostra os avisos guardados pela `NotificationCenter` (limites das IAs, Agenda, timer, Sistema...),
agrupados por origem e do mais novo para o mais antigo, com Não perturbe (o Silêncio) e Limpar tudo.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ventude.core.timefmt import ago
from ventude.services import apps
from ventude.services.notifications import Notice, group_of
from ventude.ui import icons, theme
from ventude.ui.shell.common import HoverButton, draw_pixmap, shell_font

CARD_H = 62


def when_text(at: float, now: float | None = None) -> str:
    text = ago(at, now)
    return "agora" if text == "agora mesmo" else text


class NoticeCard(QWidget):
    """Um aviso: ícone na cor dele, título, texto (até 2 linhas), quando e X ao passar o mouse."""

    dismissed = Signal(int)

    def __init__(self, notice: Notice, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.notice = notice
        self.setFixedHeight(CARD_H)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setMouseTracking(True)
        self.setAccessibleName(f"{notice.title}. {notice.text}")
        self.setToolTip(f"{notice.title}\n{notice.text}" if notice.text else notice.title)

    def _close_rect(self) -> QRectF:
        return QRectF(self.width() - 30, 8, 22, 22)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QColor(255, 255, 255, 18))
        p.setBrush(QColor(255, 255, 255, 14 if self.underMouse() else 8))
        p.drawRoundedRect(rect, 14, 14)
        if not self.notice.read:  # risquinho de "nova" na borda esquerda
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color(theme.ACCENT))
            p.drawRoundedRect(QRectF(0, 18, 3, CARD_H - 36), 1.5, 1.5)

        tint = self.notice.color or theme.ACCENT
        try:
            base = theme.color(tint)
        except Exception:  # noqa: BLE001 - cor inválida vinda do SQLite
            base = theme.color(theme.ACCENT)
        bubble = QRectF(12, (CARD_H - 34) / 2, 34, 34)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(base.red(), base.green(), base.blue(), 40))
        p.drawRoundedRect(bubble, 11, 11)
        draw_pixmap(
            p,
            icons.pixmap(self.notice.icon or "bell", base.name(), 17),
            bubble.adjusted(8.5, 8.5, -8.5, -8.5),
        )

        when = when_text(self.notice.at)
        p.setFont(shell_font(10))
        when_w = p.fontMetrics().horizontalAdvance(when) + 4
        hovering = self.underMouse()
        right = self.width() - (38 if hovering else 12)
        p.setPen(QColor(theme.MUTED))
        p.drawText(
            QRectF(right - when_w, 10, when_w, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            when,
        )

        left = 58
        p.setPen(QColor(theme.TEXT))
        p.setFont(shell_font(12, QFont.Weight.DemiBold))
        title_w = int(right - when_w - 8 - left)
        title = p.fontMetrics().elidedText(self.notice.title, Qt.TextElideMode.ElideRight, max(20, title_w))
        p.drawText(QRectF(left, 10, title_w, 16), Qt.AlignmentFlag.AlignVCenter, title)
        if self.notice.text:
            p.setPen(QColor(theme.TEXT_2))
            p.setFont(shell_font(11))
            text_w = int(self.width() - left - 14)
            text = p.fontMetrics().elidedText(self.notice.text, Qt.TextElideMode.ElideRight, text_w)
            p.drawText(QRectF(left, 30, text_w, 18), Qt.AlignmentFlag.AlignVCenter, text)

        if hovering:
            close = self._close_rect()
            path = QPainterPath()
            path.addEllipse(close)
            p.fillPath(path, QColor(255, 255, 255, 22))
            draw_pixmap(p, icons.pixmap("close", theme.TEXT_2, 12), close.adjusted(5, 5, -5, -5))

    def enterEvent(self, _event: object) -> None:
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        pos = QPointF(event.position())
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton
            and self._close_rect().adjusted(-4, -4, 4, 4).contains(pos)
        ):
            self.dismissed.emit(self.notice.id)


def _pill_btn(text: str, tip: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setToolTip(tip)
    btn.setAccessibleName(text)
    btn.setFixedHeight(28)
    return btn


class NotificationsView(QWidget):
    """Página "Notificações" do Lançador."""

    back_requested = Signal()
    finished = Signal()

    def __init__(self, ctx: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.cards: list[QWidget] = []

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        back = HoverButton("back", "", "Voltar ao Início", QSize(28, 28), 14, 10)
        back.clicked.connect(self.back_requested)
        self.title = QLabel("Notificações")
        self.title.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.quiet_btn = _pill_btn(
            "Não perturbe", "Só deixa passar timer, agenda, avisos do sistema e limites críticos"
        )
        self.quiet_btn.setCheckable(True)
        self.quiet_btn.clicked.connect(self._toggle_quiet)
        self.clear_btn = _pill_btn("Limpar tudo", "Apagar todas as notificações")
        self.clear_btn.clicked.connect(self._clear)
        top.addWidget(back)
        top.addSpacing(4)
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(self.quiet_btn)
        top.addWidget(self.clear_btn)

        self.list_host = QWidget()
        self.list_box = QVBoxLayout(self.list_host)
        self.list_box.setContentsMargins(0, 0, 6, 0)
        self.list_box.setSpacing(6)
        self.list_box.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; }"
            " QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self.scroll.setWidget(self.list_host)

        self.empty = QWidget()
        empty_box = QVBoxLayout(self.empty)
        empty_box.addStretch(1)
        empty_icon = QLabel()
        empty_icon.setPixmap(icons.pixmap("bell", theme.MUTED, 34))
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title = QLabel("Tudo em dia")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        empty_text = QLabel("Limites das IAs, lembretes da Agenda, timer e avisos do sistema aparecem aqui.")
        empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_text.setWordWrap(True)
        empty_text.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 12px;")
        empty_box.addWidget(empty_icon)
        empty_box.addSpacing(6)
        empty_box.addWidget(empty_title)
        empty_box.addWidget(empty_text)
        empty_box.addStretch(1)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel("")
        self.summary.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")
        win = QPushButton("Notificações do Windows ›")
        win.setCursor(Qt.CursorShape.PointingHandCursor)
        win.setAccessibleName("Notificações do Windows")
        win.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; padding: 4px 6px; color: {theme.TEXT_2};"
            " font-size: 11px; border-radius: 8px; }"
            f"QPushButton:hover {{ color: {theme.TEXT}; background: rgba(255,255,255,0.06); }}"
        )
        win.clicked.connect(self._open_windows)
        foot.addWidget(self.summary)
        foot.addStretch(1)
        foot.addWidget(win)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.addLayout(top)
        root.addWidget(self.scroll, 1)
        root.addWidget(self.empty, 1)
        root.addLayout(foot)

        self._style_buttons()
        center = getattr(ctx, "notifications", None)
        if center is not None:
            center.changed.connect(self._changed)
        settings_changed = getattr(ctx, "settings_changed", None)
        if settings_changed is not None:
            settings_changed.connect(lambda _s: self._style_buttons())
        self.refresh()

    @property
    def center(self) -> object | None:
        return getattr(self.ctx, "notifications", None)

    def _style_buttons(self) -> None:
        quiet = bool(getattr(getattr(self.ctx, "settings", None), "quiet_mode", False))
        self.quiet_btn.setChecked(quiet)
        self.quiet_btn.setText("Não perturbe · ligado" if quiet else "Não perturbe")
        base = (
            "QPushButton {{ background: {bg}; border: none; border-radius: 14px; padding: 0 12px;"
            " color: {fg}; font-size: 11px; font-weight: 600; }}"
            "QPushButton:hover {{ background: {hover}; }}"
        )
        self.quiet_btn.setStyleSheet(
            base.format(
                bg=theme.TOGGLE_ON_BG if quiet else theme.INK_KEY,
                fg=theme.TOGGLE_ON_FG if quiet else theme.TEXT_2,
                hover=theme.TOGGLE_ON_BG if quiet else "rgba(255,255,255,0.10)",
            )
        )
        self.clear_btn.setStyleSheet(
            base.format(bg=theme.INK_KEY, fg=theme.TEXT_2, hover="rgba(255,255,255,0.10)")
        )

    def _changed(self) -> None:
        if self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        for widget in self.cards:
            self.list_box.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self.cards = []
        center = self.center
        items = center.newest_first() if center is not None else []  # type: ignore[attr-defined]
        # agrupa por origem, na ordem do aviso mais novo de cada grupo
        groups: dict[str, list[Notice]] = {}
        for notice in items:
            groups.setdefault(group_of(notice.kind)[0], []).append(notice)
        insert_at = 0
        for label, notices in groups.items():
            header = self._group_header(label, notices[0].kind, len(notices))
            self.list_box.insertWidget(insert_at, header)
            self.cards.append(header)
            insert_at += 1
            for notice in notices:
                card = NoticeCard(notice)
                card.dismissed.connect(self._dismiss)
                self.list_box.insertWidget(insert_at, card)
                self.cards.append(card)
                insert_at += 1
        has = bool(items)
        self.scroll.setVisible(has)
        self.empty.setVisible(not has)
        self.clear_btn.setEnabled(has)
        unread = center.unread if center is not None else 0  # type: ignore[attr-defined]
        if not has:
            self.summary.setText("")
        elif unread:
            novas = "1 nova" if unread == 1 else f"{unread} novas"
            self.summary.setText(f"{len(items)} notificações · {novas}")
        else:
            self.summary.setText(f"{len(items)} notificações · {when_text(items[0].at, time.time())}")

    def _group_header(self, label: str, kind: str, count: int) -> QWidget:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(4, 6, 0, 0)
        row.setSpacing(6)
        icon = QLabel()
        icon.setPixmap(icons.pixmap(group_of(kind)[1], theme.TEXT_2, 13))
        name = QLabel(f"{label} · {count}" if count > 1 else label)
        name.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px; font-weight: 600;")
        clear = HoverButton("close", "", f"Limpar {label}", QSize(22, 22), 11, 10)
        clear.clicked.connect(lambda k=kind: self._clear_group(k))
        row.addWidget(icon)
        row.addWidget(name)
        row.addStretch(1)
        row.addWidget(clear)
        return host

    def mark_read(self) -> None:
        center = self.center
        if center is not None:
            center.mark_all_read()  # type: ignore[attr-defined]

    def _dismiss(self, notice_id: int) -> None:
        center = self.center
        if center is not None:
            center.dismiss(notice_id)  # type: ignore[attr-defined]

    def _clear_group(self, kind: str) -> None:
        center = self.center
        if center is not None:
            center.dismiss_group(kind)  # type: ignore[attr-defined]

    def _clear(self) -> None:
        center = self.center
        if center is not None:
            center.clear()  # type: ignore[attr-defined]

    def _toggle_quiet(self) -> None:
        settings = getattr(self.ctx, "settings", None)
        update = getattr(self.ctx, "update_settings", None)
        if settings is not None and update is not None:
            update(quiet_mode=not bool(getattr(settings, "quiet_mode", False)))
        self._style_buttons()

    def _open_windows(self) -> None:
        self.finished.emit()
        ok, _err = apps.open_system("ms-actioncenter:")
        if not ok:
            apps.send_keys(0x5B, 0x4E)  # Win + N


__all__ = ["NoticeCard", "NotificationsView", "when_text"]
