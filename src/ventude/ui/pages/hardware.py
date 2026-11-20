"""Sistema: CPU, GPU, RAM, rede, disco e bateria ao vivo, com alfinete para fixar na pílula."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ventude.services import hardware
from ventude.services.hardware import Snapshot, fmt_gb, fmt_rate, fmt_temp
from ventude.ui import icons, theme
from ventude.ui.context import AppContext
from ventude.ui.pages.base import Page, scroller
from ventude.ui.widgets import (
    Card,
    ElidedLabel,
    PinButton,
    Ring,
    Sparkline,
    UsageBar,
    hbox,
    icon_tile,
    label,
    vbox,
)

CPU_COLOR = theme.ACCENT
GPU_COLOR = "#B49BFF"
RAM_COLOR = theme.SKY
DOWN_COLOR = theme.ACCENT
UP_COLOR = theme.SUN


def load_color(value: float | None, base: str) -> str:
    if value is not None and value >= 90:
        return theme.DANGER
    if value is not None and value >= 75:
        return theme.WARN
    return base


class MetricLine(QWidget):
    """'Uso  22%  📌': rótulo, valor e o alfinete que fixa a métrica na pílula."""

    pin_toggled = Signal(str, bool)

    def __init__(self, metric: str, title: str, big: bool = False) -> None:
        super().__init__()
        self.metric = metric
        self.title = label(title, "Muted")
        self.value = label("—", "")
        self._size = 18 if big else 13
        self.value.setStyleSheet(f"font-size: {self._size}px; font-weight: 600;")
        self.pin = PinButton(
            f"{hardware.METRICS[metric]} fixado na ilha. Clique para tirar.",
            f"Fixar {hardware.METRICS[metric].lower()} na ilha fechada",
        )
        self.pin.clicked.connect(lambda: self.pin_toggled.emit(self.metric, not self.pin.pinned))
        layout = vbox(self.title, hbox(self.value, None, self.pin, spacing=4), spacing=0) if big else None
        if layout is None:
            layout = hbox(self.title, None, self.value, self.pin, spacing=6)
        self.setLayout(layout)

    def set_value(self, text: str, color: str = "") -> None:
        self.value.setText(text)
        style = f"font-size: {self._size}px; font-weight: 600;" + (f" color: {color};" if color else "")
        if style != self.value.styleSheet():
            self.value.setStyleSheet(style)


class ProcessList(QWidget):
    """Os 3 processos que mais usam agora."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.rows: list[tuple[ElidedLabel, QWidget]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(label(title.upper(), "Caption"))
        for _ in range(3):
            name = ElidedLabel("", "")
            name.setStyleSheet("font-size: 12px;")
            value = label("", "Faint")
            layout.addLayout(hbox(name, value, spacing=6))
            self.rows.append((name, value))
        self.empty = label("Abra esta aba por alguns segundos…", "Faint")
        layout.addWidget(self.empty)

    def set_rows(self, rows: list[tuple[str, str]]) -> None:
        for index, (name, value) in enumerate(self.rows):
            text, amount = rows[index] if index < len(rows) else ("", "")
            name.setText(text)
            value.setText(amount)  # type: ignore[attr-defined]
            name.setVisible(bool(text))
            value.setVisible(bool(text))
        self.empty.setVisible(not rows)


def card_header(icon_name: str, title: str, color: str) -> tuple[QHBoxLayout, ElidedLabel]:
    sub = ElidedLabel("", "Faint")
    heading = vbox(label(title, "H2"), sub, spacing=0)
    return hbox(icon_tile(icon_name, color, 34, 17), heading, spacing=10), sub


class HardwarePage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        self.monitor = ctx.hardware
        row, self.subtitle = self.header("Sistema")
        self.subtitle.setText("Ao vivo a cada 1,5 s · o alfinete fixa a métrica na ilha fechada")
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 6, 0)
        grid.setSpacing(10)
        self.lines: dict[str, MetricLine] = {}
        grid.addWidget(self._cpu_card(), 0, 0, 1, 2)
        grid.addWidget(self._gpu_card(), 0, 2, 1, 2)
        grid.addWidget(self._ram_card(), 0, 4, 1, 2)
        grid.addWidget(self._net_card(), 1, 0, 1, 3)
        grid.addWidget(self._disk_card(), 1, 3, 1, 3)
        for column in range(6):
            grid.setColumnStretch(column, 1)
        self.root.addWidget(scroller(content), 1)
        for line in self.lines.values():
            line.pin_toggled.connect(self._pin)
        self.monitor.updated.connect(self._update)
        ctx.settings_changed.connect(lambda _s: self._sync_pins())
        self._sync_pins()
        if self.monitor.last is not None:
            self._update(self.monitor.last)

    # ---------- cards ----------
    def _line(self, metric: str, title: str, big: bool = False) -> MetricLine:
        line = MetricLine(metric, title, big)
        self.lines[metric] = line
        return line

    def _cpu_card(self) -> Card:
        card = Card(margins=(14, 11, 12, 11), spacing=6)
        head, self.cpu_sub = card_header("cpu", "Processador", CPU_COLOR)
        card.body.addLayout(head)
        self.cpu_ring = Ring(62, 6)
        self.cpu_temp = label("", "Faint")
        side = vbox(self._line("cpu", "Uso", big=True), self.cpu_temp, spacing=2)
        card.body.addLayout(hbox(self.cpu_ring, side, spacing=12))
        self.cpu_spark = Sparkline().expand(26)
        card.body.addWidget(self.cpu_spark)
        self.cpu_top = ProcessList("Mais usando agora")
        card.body.addWidget(self.cpu_top)
        card.body.addStretch(1)
        return card

    def _gpu_card(self) -> Card:
        card = Card(margins=(14, 11, 12, 11), spacing=6)
        head, self.gpu_sub = card_header("gpu", "Placa de vídeo", GPU_COLOR)
        card.body.addLayout(head)
        self.gpu_ring = Ring(62, 6)
        self.gpu_power = label("", "Faint")
        side = vbox(self._line("gpu", "Uso", big=True), self._line("gpu_temp", "Temperatura"), spacing=2)
        side.addWidget(self.gpu_power)
        card.body.addLayout(hbox(self.gpu_ring, side, spacing=12))
        self.vram = UsageBar()
        card.body.addWidget(self.vram)
        self.gpu_spark = Sparkline().expand(26)
        card.body.addWidget(self.gpu_spark)
        card.body.addStretch(1)
        return card

    def _ram_card(self) -> Card:
        card = Card(margins=(14, 11, 12, 11), spacing=6)
        head, self.ram_sub = card_header("memory", "Memória RAM", RAM_COLOR)
        card.body.addLayout(head)
        self.ram_ring = Ring(62, 6)
        self.ram_free = label("", "Faint")
        side = vbox(self._line("ram", "Em uso", big=True), self.ram_free, spacing=2)
        card.body.addLayout(hbox(self.ram_ring, side, spacing=12))
        self.ram_spark = Sparkline().expand(26)
        card.body.addWidget(self.ram_spark)
        self.ram_top = ProcessList("Mais memória")
        card.body.addWidget(self.ram_top)
        card.body.addStretch(1)
        return card

    def _net_card(self) -> Card:
        card = Card(margins=(14, 11, 12, 11), spacing=6)
        head, self.net_sub = card_header("wifi", "Rede", DOWN_COLOR)
        card.body.addLayout(head)
        card.body.addLayout(
            hbox(
                self._line("net_down", "↓ Download", big=True),
                self._line("net_up", "↑ Upload", big=True),
                self._line("ping", "Ping", big=True),
                spacing=14,
            )
        )
        self.down_spark = Sparkline().expand(30)
        self.up_spark = Sparkline().expand(30)
        card.body.addLayout(hbox(self.down_spark, self.up_spark, spacing=10))
        self.local_btn = QPushButton(" IP local")
        self.public_btn = QPushButton(" IP público")
        for button, slot in ((self.local_btn, self._copy_local), (self.public_btn, self._copy_public)):
            button.setIcon(icons.icon("copy", theme.TEXT, 14))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(slot)
        self.ip_status = ElidedLabel("", "Faint")
        card.body.addLayout(hbox(self.local_btn, self.public_btn, self.ip_status, spacing=8))
        card.body.addStretch(1)
        return card

    def _disk_card(self) -> Card:
        card = Card(margins=(14, 11, 12, 11), spacing=6)
        head, self.disk_sub = card_header("hard-drive", "Disco e bateria", theme.SUN)
        card.body.addLayout(head)
        self.disk = UsageBar()
        card.body.addWidget(self.disk)
        self.battery_ring = Ring(40, 4.5)
        self.battery_note = label("", "Faint")
        self.battery_box = QWidget()
        self.battery_box.setLayout(
            hbox(
                self.battery_ring,
                vbox(self._line("battery", "Bateria"), self.battery_note, spacing=0),
                spacing=10,
            )
        )
        card.body.addWidget(self.battery_box)
        self.no_battery = label("Sem bateria: este PC fica na tomada.", "Faint")
        card.body.addWidget(self.no_battery)
        card.body.addStretch(1)
        return card

    # ---------- dados ----------
    def _update(self, snap: Snapshot) -> None:
        if not self.isVisible():
            return  # aba escondida: nada para redesenhar (on_show usa a última leitura)
        hist = self.monitor.history
        # CPU
        color = load_color(snap.cpu, CPU_COLOR)
        self.cpu_ring.set_value(snap.cpu, color, "—" if snap.cpu is None else f"{snap.cpu:.0f}%")
        self.lines["cpu"].set_value("—" if snap.cpu is None else f"{snap.cpu:.0f}%")
        self.cpu_sub.setText(f"{snap.cores} núcleos lógicos")
        self.cpu_temp.setText(f"Temperatura {fmt_temp(snap.cpu_temp)}" if snap.cpu_temp else "")
        self.cpu_temp.setVisible(snap.cpu_temp is not None)
        self.cpu_spark.set_values(list(hist["cpu"]), CPU_COLOR, (0.0, 100.0))
        if snap.top_cpu:
            self.cpu_top.set_rows([(n, f"{v:.0f}%".replace(".", ",")) for n, v in snap.top_cpu])
        # GPU
        gpu = snap.gpu
        self.gpu_sub.setText(gpu.name or "Placa de vídeo não identificada")
        util = gpu.util
        self.gpu_ring.set_value(util, load_color(util, GPU_COLOR), "—" if util is None else f"{util:.0f}%")
        self.lines["gpu"].set_value("—" if util is None else f"{util:.0f}%")
        hot = gpu.temp is not None and gpu.temp >= hardware.TEMP_LIMIT
        self.lines["gpu_temp"].set_value(fmt_temp(gpu.temp), theme.DANGER if hot else "")
        self.gpu_power.setText(f"Consumo {gpu.power:.0f} W".replace(".", ",") if gpu.power else "")
        self.gpu_power.setVisible(gpu.power is not None)
        vram_sub = f"{fmt_gb(gpu.vram_used)} de {fmt_gb(gpu.vram_total)}" if gpu.vram_total else ""
        if not vram_sub and gpu.vram_used is not None:
            vram_sub = f"{fmt_gb(gpu.vram_used)} em uso"
        self.vram.set_data("Memória de vídeo", gpu.vram_pct, GPU_COLOR, vram_sub or "Sem leitura da VRAM")
        self.gpu_spark.set_values(list(hist["gpu"]), GPU_COLOR, (0.0, 100.0))
        # RAM
        pct = snap.ram_pct
        self.ram_ring.set_value(pct, load_color(pct, RAM_COLOR), "—" if pct is None else f"{pct:.0f}%")
        self.lines["ram"].set_value(f"{fmt_gb(snap.ram_used)} de {fmt_gb(snap.ram_total, 0)}")
        self.ram_sub.setText(f"{fmt_gb(snap.ram_total, 0)} no total")
        self.ram_free.setText(f"Livre {fmt_gb(snap.ram_total - snap.ram_used)}" if snap.ram_total else "")
        self.ram_spark.set_values(list(hist["ram"]), RAM_COLOR, (0.0, 100.0))
        if snap.top_ram:
            self.ram_top.set_rows([(n, fmt_gb(v)) for n, v in snap.top_ram])
        # Rede
        self.lines["net_down"].set_value(fmt_rate(snap.net_down))
        self.lines["net_up"].set_value(fmt_rate(snap.net_up))
        slow = snap.ping is not None and snap.ping >= 150
        self.lines["ping"].set_value(
            "—" if snap.ping is None else f"{snap.ping:.0f} ms", theme.WARN if slow else ""
        )
        self.net_sub.setText("Placas de rede físicas ativas")
        for spark, key, tint in (
            (self.down_spark, "net_down", DOWN_COLOR),
            (self.up_spark, "net_up", UP_COLOR),
        ):
            values = list(hist[key])
            spark.set_values(values, tint, (0.0, max(values or [1.0]) * 1.15 or 1.0))
        # Disco e bateria
        if snap.disk_total:
            free = snap.disk_total - snap.disk_used
            self.disk.set_data(
                f"Disco {snap.disk_path.rstrip(chr(92))}",
                snap.disk_pct,
                load_color(snap.disk_pct, theme.SUN),
                f"{fmt_gb(free, 0)} livres de {fmt_gb(snap.disk_total, 0)}",
            )
        bat = snap.battery
        self.battery_box.setVisible(bat is not None)
        self.no_battery.setVisible(bat is None)
        self.disk_sub.setText("Disco principal" + (" e bateria" if bat else ""))
        if bat is not None:
            low = bat.percent <= 15 and not bat.plugged
            tint = theme.DANGER if low else (theme.ACCENT if bat.charging else theme.SUN)
            self.battery_ring.set_value(float(bat.percent), tint)
            state = "carregando" if bat.charging else ("na tomada" if bat.plugged else "na bateria")
            self.lines["battery"].set_value(f"{bat.percent}% · {state}")
            left = ""
            if bat.minutes_left and not bat.plugged:
                hours, minutes = divmod(bat.minutes_left, 60)
                left = f"~{hours} h {minutes:02d} min restantes" if hours else f"~{minutes} min restantes"
            self.battery_note.setText(left)
            self.battery_note.setVisible(bool(left))

    # ---------- alfinetes ----------
    def _sync_pins(self) -> None:
        pinned = set(self.ctx.settings.pinned_metrics())
        for metric, line in self.lines.items():
            line.pin.set_pinned(metric in pinned)

    def _pin(self, metric: str, on: bool) -> None:
        self.ctx.update_settings(pinned_metrics_json=self.ctx.settings.with_metric(metric, on))

    # ---------- IPs ----------
    def _copy_local(self) -> None:
        ip = hardware.local_ip()
        if ip:
            QGuiApplication.clipboard().setText(ip)
            self.ip_status.setText(f"IP local {ip} copiado")
        else:
            self.ip_status.setText("Sem rede agora")

    def _copy_public(self) -> None:
        self.ip_status.setText("Consultando o IP público…")
        self.public_btn.setEnabled(False)

        def done(ip: str) -> None:
            self.public_btn.setEnabled(True)
            QGuiApplication.clipboard().setText(ip)
            self.ip_status.setText(f"IP público {ip} copiado")

        def fail(error: BaseException) -> None:
            self.public_btn.setEnabled(True)
            self.ip_status.setText(f"Não deu para ver o IP público ({error})")

        self.ctx.run(hardware.public_ip, done=done, fail=fail, owner=self)

    # ---------- ciclo de vida ----------
    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        self.monitor.watch("page", True)

    def hideEvent(self, event: object) -> None:
        super().hideEvent(event)  # type: ignore[arg-type]
        self.monitor.watch("page", False)

    def on_show(self) -> None:
        self._sync_pins()
        if self.monitor.last is not None:
            self._update(self.monitor.last)
