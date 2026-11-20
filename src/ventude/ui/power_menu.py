"""Central de Energia: menu do botão de energia no topo do painel.

Bloquear, suspender e hibernar rodam no clique. Reiniciar, desligar, sair da sessão e as versões
"atualizar e..." pedem um segundo clique (o botão fica vermelho com "Clique de novo para confirmar").
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QPushButton, QWidget

from ventude.services import power
from ventude.services.power import PowerAction
from ventude.ui import icons, theme
from ventude.ui.widgets import label, repolish, vbox

CONFIRM_MS = 4000


class PowerMenu(QFrame):
    acted = Signal(str)

    def __init__(
        self,
        actions: list[PowerAction],
        parent: QWidget | None = None,
        runner: Callable[[str], tuple[bool, str]] | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("PowerMenu")
        self.setStyleSheet(
            f"QFrame#PowerMenu {{ background: {theme.SURFACE}; border: 1px solid {theme.STROKE_2};"
            " border-radius: 14px; }"
            "QPushButton { text-align: left; padding: 9px 12px; }"
        )
        self._run = runner or power.run
        self.armed = ""
        self.buttons: dict[str, QPushButton] = {}
        self.actions = {a.key: a for a in actions}
        grid = QGridLayout()
        grid.setSpacing(6)
        for index, action in enumerate(actions):
            button = QPushButton(f"  {action.title}")
            button.setIcon(icons.icon(action.icon, theme.WARN if action.needs_update else theme.TEXT, 17))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumWidth(190)
            button.clicked.connect(lambda _c=False, k=action.key: self.choose(k))
            grid.addWidget(button, index // 2, index % 2)
            self.buttons[action.key] = button
        self.status = label("", "Faint", wrap=True)
        self.status.hide()
        title = label("Energia", "H2")
        hint = (
            "Há uma atualização do Windows esperando reiniciar."
            if power.BY_KEY["update_restart"] in actions
            else ""
        )
        self.hint = label(hint, "Warn", wrap=True)
        self.hint.setVisible(bool(hint))
        self.setLayout(vbox(title, self.hint, grid, self.status, spacing=8, margins=14))
        self._disarm = QTimer(self, singleShot=True, interval=CONFIRM_MS)
        self._disarm.timeout.connect(self.reset)

    def choose(self, key: str) -> None:
        action = self.actions[key]
        if action.destructive and self.armed != key:
            self.reset()
            self.armed = key
            button = self.buttons[key]
            button.setText(f"  {action.title}: clique de novo")
            button.setObjectName("Danger")
            repolish(button)
            self._disarm.start()
            return
        self.reset()
        self.hide()
        delay = 350 if key in ("sleep", "hibernate") else 0  # a ilha some antes de o PC dormir
        QTimer.singleShot(delay, lambda: self._execute(key))

    def _execute(self, key: str) -> None:
        ok, message = self._run(key)
        self.acted.emit(key if ok else f"erro:{message}")

    def reset(self) -> None:
        self._disarm.stop()
        if self.armed and self.armed in self.buttons:
            button = self.buttons[self.armed]
            button.setText(f"  {self.actions[self.armed].title}")
            button.setObjectName("")
            repolish(button)
        self.armed = ""

    def popup_below(self, anchor: QWidget) -> None:
        self.adjustSize()
        below = anchor.mapToGlobal(QPoint(anchor.width() - self.width(), anchor.height() + 6))
        self.move(below)
        self.show()
