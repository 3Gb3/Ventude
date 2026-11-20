from __future__ import annotations

import getpass
import logging
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from ventude import __version__
from ventude.core import net, updates
from ventude.core.logs import setup_logging
from ventude.core.paths import app_data_dir
from ventude.core.store import Store
from ventude.core.tasks import Tasks
from ventude.services import system

log = logging.getLogger(__name__)
_windows: list[object] = []  # mantém janelas avulsas vivas (ex.: aviso de atualização)


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return f"ventude-{user}"


def _already_running(name: str) -> bool:
    """Se outro Ventude já está aberto, pede para ele aparecer e sai."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(300):
        socket.write(b"show")
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return True
    return False


def _check_for_update(tasks: Tasks, app: QApplication) -> None:
    """Uma vez por inicialização: pergunta se quer atualizar quando a main tem versão nova."""
    from ventude.ui.update_dialog import ask_to_update

    def found(update: updates.Update | None) -> None:
        if update is None:
            return
        log.info("Atualização disponível: %s", update.version)
        _windows.append(ask_to_update(update, tasks, app.quit))

    tasks.run(updates.check, __version__, done=found)


def run() -> int:
    setup_logging()
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Ventude")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Ventude")
    app.setQuitOnLastWindowClosed(False)

    name = _server_name()
    if _already_running(name):
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)

    from ventude.ui import theme
    from ventude.ui.context import AppContext
    from ventude.ui.island import Island
    from ventude.ui.tray import Tray, app_icon

    theme.apply(app)
    app.setWindowIcon(app_icon())
    store = Store(app_data_dir() / "ventude.db")
    tasks = Tasks()
    ctx = AppContext(store, tasks)
    if ctx.settings.start_with_windows and system.autostart_supported():
        system.set_autostart(True)  # mantém o caminho certo se o projeto mudou de pasta
    island = Island(ctx)
    tray = Tray(ctx, island)

    def reveal() -> None:
        connection = server.nextPendingConnection()
        if connection:
            connection.deleteLater()
        island.user_hidden(False)
        island.expand()
        island.raise_()
        island.activateWindow()

    server.newConnection.connect(reveal)

    def screens_changed(*_: object) -> None:
        if ctx.settings.monitor_index >= len(QGuiApplication.screens()):
            ctx.update_settings(monitor_index=0, island_position_json="")
        island.reposition()

    app.screenAdded.connect(screens_changed)
    app.screenRemoved.connect(screens_changed)
    for screen in app.screens():
        screen.availableGeometryChanged.connect(lambda *_: island.reposition())
    app.screenAdded.connect(lambda s: s.availableGeometryChanged.connect(lambda *_: island.reposition()))
    ctx.quit_requested.connect(app.quit)

    ctx.media.start()
    ctx.limits.start()
    tray.show()
    island.show()
    log.info("Ventude %s iniciado", __version__)
    tasks.run(net.browser_transport, done=lambda name: log.info("Conexão com claude.ai/grok.com: %s", name))
    if updates.enabled():
        QTimer.singleShot(3000, lambda: _check_for_update(tasks, app))
    code = app.exec()

    tray.hide()
    ctx.media.stop()
    tasks.shutdown()
    server.close()
    store.close()
    return code
