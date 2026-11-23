from __future__ import annotations

import atexit
import getpass
import json
import logging
import sys
import time
from collections.abc import Callable

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
MAX_IPC_BYTES = 4096
MAX_NOTIFY_PER_SEC = 5


def handle_ipc_payload(
    payload: bytes,
    island: object,
    timestamps: list[float],
    now: float | None = None,
) -> str:
    """Processa mensagem recebida via QLocalServer: notify {json} (rate-limited) ou show."""
    if len(payload) > MAX_IPC_BYTES:
        return "too_large"
    raw = payload.strip()
    if raw.startswith(b"notify"):
        rest = raw[6:].strip()
        if not rest:
            return "invalid"
        cur = time.monotonic() if now is None else now
        recent = [t for t in timestamps if (cur - t) < 1.0]
        timestamps[:] = recent
        if len(recent) >= MAX_NOTIFY_PER_SEC:
            return "rate_limited"
        timestamps.append(cur)
        try:
            data = json.loads(rest.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return "invalid"
        if not isinstance(data, dict):
            return "invalid"
        title = str(data.get("title", "")).strip()
        if not title:
            return "invalid"
        from ventude.ui import theme
        from ventude.ui.toast import Toast

        text = str(data.get("text", "")).strip()
        icon = str(data.get("icon", "bell")).strip() or "bell"
        try:
            seconds = float(data.get("seconds", 3.5))
        except (TypeError, ValueError):
            seconds = 3.5
        toast = Toast(
            title=title[:120],
            text=text[:240],
            icon=icon[:32],
            color=theme.ACCENT,
            seconds=max(1.0, min(15.0, seconds)),
            kind="info",
        )
        island.show_toast(toast)  # type: ignore[attr-defined]
        return "toast"

    island.user_hidden(False)  # type: ignore[attr-defined]
    island.expand()  # type: ignore[attr-defined]
    island.raise_()  # type: ignore[attr-defined]
    island.activateWindow()  # type: ignore[attr-defined]
    return "reveal"


def _send_notify_cli(name: str, argv: list[str]) -> int:
    """Envia comando 'notify {json}' para a instância em execução via CLI."""
    title = ""
    text = ""
    icon = "bell"
    seconds = 3.5
    i = 0
    positional: list[str] = []
    while i < len(argv):
        tok = argv[i]
        if tok in ("--title", "-t") and i + 1 < len(argv):
            title = argv[i + 1]
            i += 2
        elif tok in ("--text", "-m") and i + 1 < len(argv):
            text = argv[i + 1]
            i += 2
        elif tok == "--icon" and i + 1 < len(argv):
            icon = argv[i + 1]
            i += 2
        elif tok == "--seconds" and i + 1 < len(argv):
            try:
                seconds = float(argv[i + 1])
            except ValueError:
                pass
            i += 2
        else:
            positional.append(tok)
            i += 1
    if not title and positional:
        title = positional[0]
    if not text and len(positional) > 1:
        text = " ".join(positional[1:])
    if not title:
        return 1
    payload = b"notify " + json.dumps(
        {"title": title, "text": text, "icon": icon, "seconds": seconds}
    ).encode("utf-8")
    if len(payload) > MAX_IPC_BYTES:
        return 1
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(300):
        return 1
    socket.write(payload)
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return 0


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


def _check_for_update(tasks: Tasks, quit_cb: Callable[[], None]) -> None:
    """Uma vez por inicialização: pergunta se quer atualizar quando a main tem versão nova."""
    from ventude.ui.update_dialog import ask_to_update

    def found(update: updates.Update | None) -> None:
        if update is None:
            return
        log.info("Atualização disponível: %s", update.version)
        _windows.append(ask_to_update(update, tasks, quit_cb))

    tasks.run(updates.check, __version__, done=found)


def _install_failsafes(app: QApplication, shell: object) -> None:
    """A barra do Windows sempre volta e a tecla Windows sempre é solta: ao sair, ao fechar o Qt
    e em qualquer erro não tratado."""
    hider = shell.hider  # type: ignore[attr-defined]
    keys = shell.keys  # type: ignore[attr-defined]

    def restore_shell() -> None:
        try:
            shell.restore()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            try:
                if hider.hidden or hider.marker.exists():
                    hider.restore()  # só ctypes: funciona mesmo depois do Qt fechar
            except Exception:  # noqa: BLE001
                log.exception("Falha ao restaurar a barra de tarefas")
        try:
            keys.remove()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao remover o atalho da tecla Windows")

    atexit.register(restore_shell)
    app.aboutToQuit.connect(restore_shell)
    previous = sys.excepthook

    def excepthook(kind, value, tb) -> None:  # noqa: ANN001
        log.error("Erro inesperado", exc_info=(kind, value, tb))
        try:
            shell.emergency()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            restore_shell()
        previous(kind, value, tb)

    sys.excepthook = excepthook


def run() -> int:
    setup_logging()
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Ventude")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Ventude")
    app.setQuitOnLastWindowClosed(False)

    name = _server_name()
    if len(sys.argv) >= 2 and sys.argv[1] == "notify":
        return _send_notify_cli(name, sys.argv[2:])
    if _already_running(name):
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
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
    from ventude.ui.shell.controller import ShellController

    shell = ShellController(ctx, island)  # barra, Iniciar, Pesquisa, tecla Windows e "Até logo!"
    tray = Tray(ctx, island)
    from ventude.ui.lockscreen import LockScreen

    ctx.lock_screen = LockScreen(ctx)  # tela de bloqueio do Ventude (e o bloqueio por inatividade)
    _install_failsafes(app, shell)

    ipc_timestamps: list[float] = []

    def reveal() -> None:
        connection = server.nextPendingConnection()
        payload = b""
        if connection:
            if connection.bytesAvailable() == 0:
                connection.waitForReadyRead(150)
            payload = bytes(connection.read(MAX_IPC_BYTES + 64))
            connection.close()
            connection.deleteLater()
        handle_ipc_payload(payload, island, ipc_timestamps)

    server.newConnection.connect(reveal)

    def screens_changed(*_: object) -> None:
        if ctx.settings.monitor_index >= len(QGuiApplication.screens()):
            ctx.update_settings(monitor_index=0, island_position_json="")
        island.reposition()
        QTimer.singleShot(300, shell.screens_changed)

    app.screenAdded.connect(screens_changed)
    app.screenRemoved.connect(screens_changed)
    for screen in app.screens():
        screen.availableGeometryChanged.connect(lambda *_: island.reposition())
    app.screenAdded.connect(lambda s: s.availableGeometryChanged.connect(lambda *_: island.reposition()))

    quitting = False

    def quit_app() -> None:
        nonlocal quitting
        if quitting:
            return
        quitting = True
        log.info("Encerrando Ventude")
        try:
            island.prepare_quit()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao fechar a ilha")
        try:
            shell.shutdown()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao restaurar o Shell")
        try:
            tray.hide()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao esconder o ícone da bandeja")
        app.quit()

    ctx.quit_requested.connect(quit_app)

    ctx.media.start()
    ctx.limits.start()
    ctx.start()
    tray.show()
    island.show()
    shell.start()
    ctx.lock_screen.start()
    log.info("Ventude %s iniciado", __version__)
    tasks.run(net.browser_transport, done=lambda name: log.info("Conexão com claude.ai/grok.com: %s", name))
    if updates.enabled():
        QTimer.singleShot(3000, lambda: _check_for_update(tasks, quit_app))
    code = app.exec()

    shell.shutdown()
    ctx.stop()
    tray.hide()
    ctx.media.stop()
    tasks.shutdown()
    server.close()
    store.close()
    return code
