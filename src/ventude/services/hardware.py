"""Métricas do PC ao vivo, sem psutil: tudo via ctypes (kernel32, psapi, iphlpapi, pdh e NVML).

- CPU: `GetSystemTimes` (delta de ocioso/kernel/usuário), núcleos e os processos que mais usam.
- GPU: NVML (`nvml.dll`) nas NVIDIA; contadores PDH de "GPU Engine" e "GPU Adapter Memory" nas demais.
- RAM: `GlobalMemoryStatusEx` e os processos com mais memória (`K32EnumProcesses` + `GetProcessMemoryInfo`).
- Rede: `GetIfTable2` (contadores de 64 bits, só placas físicas, sem filtros/VPN) e ping por ICMP.
- Disco (`shutil.disk_usage`) e bateria (`GetSystemPowerStatus`).

As leituras rodam fora da thread da interface. O `HardwareMonitor` só lê a cada 1,5 s com a aba
Sistema aberta ou com alguma métrica fixada na pílula; fora isso, uma leitura leve a cada 20 s
cuida dos avisos de temperatura e memória.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import shutil
import socket
import sys
import time
from collections import deque
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)

METRICS: dict[str, str] = {
    "cpu": "CPU",
    "gpu": "GPU",
    "gpu_temp": "Temperatura da GPU",
    "ram": "RAM",
    "net_down": "Download",
    "net_up": "Upload",
    "ping": "Ping",
    "battery": "Bateria",
}
INTERVAL_MS = 1500
ALERT_INTERVAL_MS = 20_000
HISTORY = 40  # 40 leituras de 1,5 s = 60 segundos
PING_EVERY = 3.0
PING_HOSTS = ("1.1.1.1", "8.8.8.8")
ALERT_COOLDOWN = 10 * 60
TEMP_LIMIT = 85.0
RAM_LIMIT = 92.0
GB = 1024**3


# ---------- dados ----------
@dataclass
class Gpu:
    name: str = ""
    util: float | None = None
    vram_used: float | None = None  # bytes
    vram_total: float | None = None
    temp: float | None = None  # °C
    power: float | None = None  # W
    source: str = ""  # nvml | pdh | ""

    @property
    def vram_pct(self) -> float | None:
        if self.vram_used is None or not self.vram_total:
            return None
        return max(0.0, min(100.0, self.vram_used / self.vram_total * 100))


@dataclass
class Battery:
    percent: int
    charging: bool
    plugged: bool
    minutes_left: int | None = None


@dataclass
class Snapshot:
    at: float = 0.0
    cpu: float | None = None
    cores: int = 0
    cpu_temp: float | None = None
    gpu: Gpu = field(default_factory=Gpu)
    ram_used: float = 0.0
    ram_total: float = 0.0
    net_down: float | None = None  # bytes/s
    net_up: float | None = None
    ping: float | None = None  # ms
    disk_path: str = ""
    disk_used: float = 0.0
    disk_total: float = 0.0
    battery: Battery | None = None
    top_cpu: list[tuple[str, float]] = field(default_factory=list)
    top_ram: list[tuple[str, float]] = field(default_factory=list)

    @property
    def ram_pct(self) -> float | None:
        return self.ram_used / self.ram_total * 100 if self.ram_total else None

    @property
    def disk_pct(self) -> float | None:
        return self.disk_used / self.disk_total * 100 if self.disk_total else None

    def value(self, metric: str) -> float | None:
        """Número da métrica para o histórico e os avisos."""
        return {
            "cpu": self.cpu,
            "gpu": self.gpu.util,
            "gpu_temp": self.gpu.temp,
            "ram": self.ram_pct,
            "net_down": self.net_down,
            "net_up": self.net_up,
            "ping": self.ping,
            "battery": float(self.battery.percent) if self.battery else None,
        }.get(metric)


# ---------- formatação (pt-BR) ----------
def _comma(text: str) -> str:
    return text.replace(".", ",")


def fmt_rate(value: float | None) -> str:
    """Bytes por segundo em KB/s ou MB/s: '820 KB/s', '12,4 MB/s'."""
    if value is None:
        return "—"
    if value >= 1024 * 1000:
        return _comma(f"{value / 1024**2:.1f} MB/s")
    return f"{value / 1024:.0f} KB/s"


def fmt_gb(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    return _comma(f"{value / GB:.{digits}f} GB")


def fmt_temp(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}°"


def pin_text(metric: str, snap: Snapshot | None) -> str:
    """Texto curto de uma métrica fixada na pílula (ex.: 'CPU 22%', '↓ 12,4 MB/s', '18 ms')."""
    if snap is None:
        return {"net_down": "↓ —", "net_up": "↑ —", "ping": "— ms", "gpu_temp": "—°"}.get(
            metric, f"{METRICS.get(metric, metric).split()[0][:3].upper()} —"
        )
    pct = "{:.0f}%".format
    if metric == "cpu":
        return f"CPU {pct(snap.cpu)}" if snap.cpu is not None else "CPU —"
    if metric == "gpu":
        return f"GPU {pct(snap.gpu.util)}" if snap.gpu.util is not None else "GPU —"
    if metric == "gpu_temp":
        return fmt_temp(snap.gpu.temp)
    if metric == "ram":
        return f"RAM {pct(snap.ram_pct)}" if snap.ram_pct is not None else "RAM —"
    if metric == "net_down":
        return f"↓ {fmt_rate(snap.net_down)}"
    if metric == "net_up":
        return f"↑ {fmt_rate(snap.net_up)}"
    if metric == "ping":
        return f"{snap.ping:.0f} ms" if snap.ping is not None else "— ms"
    if metric == "battery":
        if not snap.battery:
            return "BAT —"
        return f"BAT {snap.battery.percent}%" + (" ⚡" if snap.battery.charging else "")
    return ""


PIN_TEMPLATES = {
    "cpu": "CPU 100%",
    "gpu": "GPU 100%",
    "gpu_temp": "100°",
    "ram": "RAM 100%",
    "net_down": "↓ 99,9 MB/s",
    "net_up": "↑ 99,9 MB/s",
    "ping": "999 ms",
    "battery": "BAT 100% ⚡",
}


def pill_parts(metrics: list[str], snap: Snapshot | None, *, template: bool = False) -> list[str]:
    """Pedaços do chip da pílula. GPU e temperatura da GPU fixadas juntas viram 'GPU 75% · 64°'."""
    parts: list[str] = []
    shown = [m for m in metrics if m in METRICS]
    for metric in shown:
        text = PIN_TEMPLATES[metric] if template else pin_text(metric, snap)
        if metric == "gpu_temp":
            if "gpu" in shown:
                continue  # entra junto com a GPU
            text = f"GPU {text}"
        if metric == "gpu" and "gpu_temp" in shown:
            temp = PIN_TEMPLATES["gpu_temp"] if template else pin_text("gpu_temp", snap)
            text = f"{text} · {temp}"
        parts.append(text)
    return parts


# ---------- contas puras (testáveis) ----------
def cpu_percent(prev: tuple[int, int, int] | None, cur: tuple[int, int, int] | None) -> float | None:
    """(ocioso, kernel, usuário) em 100 ns. O tempo de kernel já inclui o ocioso."""
    if not prev or not cur:
        return None
    idle = cur[0] - prev[0]
    total = (cur[1] - prev[1]) + (cur[2] - prev[2])
    if total <= 0:
        return None
    return max(0.0, min(100.0, (1 - idle / total) * 100))


def rate(prev: float | None, cur: float, seconds: float) -> float | None:
    if prev is None or seconds <= 0 or cur < prev:  # contador zerou (placa trocada/reiniciada)
        return None
    return (cur - prev) / seconds


def top(items: dict[str, float], count: int = 3, minimum: float = 0.0) -> list[tuple[str, float]]:
    ranked = sorted(((v, k) for k, v in items.items() if v > minimum), reverse=True)[:count]
    return [(k, v) for v, k in ranked]


def gpu_utilization(instances: dict[str, float]) -> float | None:
    """Soma o uso 3D por GPU (instâncias 'pid_X_luid_..._engtype_3D') e fica com a mais usada."""
    if not instances:
        return None
    per_gpu: dict[str, float] = {}
    for name, value in instances.items():
        match = re.search(r"luid_(0x[0-9a-fA-F]+_0x[0-9a-fA-F]+)", name)
        key = match.group(1).lower() if match else "?"
        per_gpu[key] = per_gpu.get(key, 0.0) + max(0.0, value)
    return max(0.0, min(100.0, max(per_gpu.values())))


def thermal_celsius(kelvins: list[float]) -> float | None:
    """Zonas térmicas do ACPI vêm em Kelvin; ignora o valor fixo de muitas placas (27,85 °C)."""
    values = [k - 273.15 for k in kelvins if k > 0]
    values = [v for v in values if 30.0 < v < 115.0]
    return max(values) if values else None


def display_name(path: str) -> str:
    from ventude.services.winutil import app_name

    return app_name(path) or Path(path).stem


# ---------- leituras nativas ----------
class FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    @property
    def value(self) -> int:
        return (self.high << 32) | self.low


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class SYSTEM_POWER_STATUS(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


class _GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]


class MIB_IF_ROW2(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("InterfaceLuid", ctypes.c_uint64),
        ("InterfaceIndex", wintypes.ULONG),
        ("InterfaceGuid", _GUID),
        ("Alias", wintypes.WCHAR * 257),
        ("Description", wintypes.WCHAR * 257),
        ("PhysicalAddressLength", wintypes.ULONG),
        ("PhysicalAddress", ctypes.c_ubyte * 32),
        ("PermanentPhysicalAddress", ctypes.c_ubyte * 32),
        ("Mtu", wintypes.ULONG),
        ("Type", wintypes.ULONG),
        ("TunnelType", ctypes.c_int),
        ("MediaType", ctypes.c_int),
        ("PhysicalMediumType", ctypes.c_int),
        ("AccessType", ctypes.c_int),
        ("DirectionType", ctypes.c_int),
        ("Flags", ctypes.c_ubyte),  # bit 0: placa física; bit 1: filtro
        ("OperStatus", ctypes.c_int),
        ("AdminStatus", ctypes.c_int),
        ("MediaConnectState", ctypes.c_int),
        ("NetworkGuid", _GUID),
        ("ConnectionType", ctypes.c_int),
        ("TransmitLinkSpeed", ctypes.c_uint64),
        ("ReceiveLinkSpeed", ctypes.c_uint64),
        ("InOctets", ctypes.c_uint64),
        ("InUcastPkts", ctypes.c_uint64),
        ("InNUcastPkts", ctypes.c_uint64),
        ("InDiscards", ctypes.c_uint64),
        ("InErrors", ctypes.c_uint64),
        ("InUnknownProtos", ctypes.c_uint64),
        ("InUcastOctets", ctypes.c_uint64),
        ("InMulticastOctets", ctypes.c_uint64),
        ("InBroadcastOctets", ctypes.c_uint64),
        ("OutOctets", ctypes.c_uint64),
        ("OutUcastPkts", ctypes.c_uint64),
        ("OutNUcastPkts", ctypes.c_uint64),
        ("OutDiscards", ctypes.c_uint64),
        ("OutErrors", ctypes.c_uint64),
        ("OutUcastOctets", ctypes.c_uint64),
        ("OutMulticastOctets", ctypes.c_uint64),
        ("OutBroadcastOctets", ctypes.c_uint64),
        ("OutQLen", ctypes.c_uint64),
    ]


class ICMP_ECHO_REPLY(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("Address", wintypes.ULONG),
        ("Status", wintypes.ULONG),
        ("RoundTripTime", wintypes.ULONG),
        ("DataSize", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("Data", ctypes.c_void_p),
        ("Ttl", ctypes.c_ubyte),
        ("Tos", ctypes.c_ubyte),
        ("OptFlags", ctypes.c_ubyte),
        ("OptionsSize", ctypes.c_ubyte),
        ("OptionsData", ctypes.c_void_p),
    ]


class PDH_FMT_COUNTERVALUE(ctypes.Structure):  # noqa: N801
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):  # noqa: N801
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", PDH_FMT_COUNTERVALUE)]


PDH_FMT_DOUBLE, PDH_FMT_NOCAP100, PDH_MORE_DATA = 0x200, 0x8000, 0x800007D2
PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_VM_READ = 0x1000, 0x0010


def system_times() -> tuple[int, int, int] | None:
    idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    ):
        return None
    return idle.value, kernel.value, user.value


def memory() -> tuple[float, float]:
    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return 0.0, 0.0
    return float(status.ullTotalPhys - status.ullAvailPhys), float(status.ullTotalPhys)


def processes() -> list[tuple[int, str, int, int]]:
    """(pid, caminho do .exe, tempo de CPU em 100 ns, memória em uso) dos processos que dá para abrir."""
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(FILETIME)] * 4
    kernel32.K32GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
        wintypes.DWORD,
    ]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    pids = (wintypes.DWORD * 4096)()
    needed = wintypes.DWORD()
    if not kernel32.K32EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
        return []
    count = needed.value // ctypes.sizeof(wintypes.DWORD)
    out: list[tuple[int, str, int, int]] = []
    buf = ctypes.create_unicode_buffer(1024)
    creation, exit_, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
    own = os.getpid()
    for pid in pids[:count]:
        if pid in (0, 4, own):
            continue
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            size = wintypes.DWORD(len(buf))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                continue
            cpu = 0
            if kernel32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_), ctypes.byref(kernel), ctypes.byref(user)
            ):
                cpu = kernel.value + user.value
            mem = 0
            if kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                mem = int(counters.WorkingSetSize)
            out.append((int(pid), buf.value, cpu, mem))
        finally:
            kernel32.CloseHandle(handle)
    return out


def network_totals() -> tuple[int, int] | None:
    """Bytes recebidos e enviados nas placas físicas ativas (sem loopback, filtros nem VPN)."""
    if ctypes.sizeof(MIB_IF_ROW2) != 1352:  # layout inesperado: melhor não ler lixo
        return None
    iphlpapi = ctypes.windll.iphlpapi  # type: ignore[attr-defined]
    table = ctypes.c_void_p()
    if iphlpapi.GetIfTable2(ctypes.byref(table)) != 0 or not table.value:
        return None
    try:
        entries = ctypes.c_ulong.from_address(table.value).value
        rows = (MIB_IF_ROW2 * entries).from_address(table.value + 8)
        rx = tx = 0
        for row in rows:
            hardware, filtered = bool(row.Flags & 0x1), bool(row.Flags & 0x2)
            if hardware and not filtered and row.OperStatus == 1 and row.Type != 24:
                rx += int(row.InOctets)
                tx += int(row.OutOctets)
        return rx, tx
    finally:
        iphlpapi.FreeMibTable(table)


IF_TYPE_ETHERNET, IF_TYPE_WIFI = 6, 71


def network_kind() -> str:
    """'wifi', 'ethernet' ou '' (sem rede) pela placa física conectada (ícone da barra de tarefas)."""
    if sys.platform != "win32" or ctypes.sizeof(MIB_IF_ROW2) != 1352:
        return ""
    iphlpapi = ctypes.windll.iphlpapi  # type: ignore[attr-defined]
    table = ctypes.c_void_p()
    if iphlpapi.GetIfTable2(ctypes.byref(table)) != 0 or not table.value:
        return ""
    try:
        entries = ctypes.c_ulong.from_address(table.value).value
        rows = (MIB_IF_ROW2 * entries).from_address(table.value + 8)
        kinds = {
            int(row.Type)
            for row in rows
            if row.Flags & 0x1 and not row.Flags & 0x2 and row.OperStatus == 1 and row.MediaConnectState == 1
        }
    finally:
        iphlpapi.FreeMibTable(table)
    if IF_TYPE_ETHERNET in kinds:
        return "ethernet"
    if IF_TYPE_WIFI in kinds:
        return "wifi"
    return ""


def ping(host: str, timeout_ms: int = 1000) -> float | None:
    """Ida e volta em ms por ICMP (sem precisar de administrador). None sem resposta."""
    iphlpapi = ctypes.windll.iphlpapi  # type: ignore[attr-defined]
    iphlpapi.IcmpCreateFile.restype = wintypes.HANDLE
    iphlpapi.IcmpCloseHandle.argtypes = [wintypes.HANDLE]
    iphlpapi.IcmpSendEcho.argtypes = [
        wintypes.HANDLE,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.WORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    try:
        address = int.from_bytes(socket.inet_aton(host), "little")
    except OSError:
        return None
    handle = iphlpapi.IcmpCreateFile()
    if not handle or handle == wintypes.HANDLE(-1).value:
        return None
    try:
        payload = b"ventude-ping"
        size = ctypes.sizeof(ICMP_ECHO_REPLY) + len(payload) + 8
        reply = ctypes.create_string_buffer(size)
        data = ctypes.create_string_buffer(payload)
        count = iphlpapi.IcmpSendEcho(handle, address, data, len(payload), None, reply, size, timeout_ms)
        if not count:
            return None
        echo = ICMP_ECHO_REPLY.from_buffer(reply)
        return float(echo.RoundTripTime) if echo.Status == 0 else None
    finally:
        iphlpapi.IcmpCloseHandle(handle)


def battery() -> Battery | None:
    status = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    if status.BatteryFlag & 128 or status.BatteryFlag == 255 or status.BatteryLifePercent > 100:
        return None  # PC sem bateria
    seconds = status.BatteryLifeTime
    return Battery(
        percent=int(status.BatteryLifePercent),
        charging=bool(status.BatteryFlag & 8),
        plugged=status.ACLineStatus == 1,
        minutes_left=None if seconds in (0xFFFFFFFF, 0) else int(seconds // 60),
    )


def disk(path: str | None = None) -> tuple[str, float, float]:
    path = path or (os.environ.get("SystemDrive", "C:") + "\\")
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return path, 0.0, 0.0
    return path, float(usage.used), float(usage.total)


def local_ip() -> str:
    """IP desta máquina na rede local (não envia nada: só pergunta a rota ao sistema)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return ""


def public_ip() -> str:
    from ventude.core import net

    text = net.get_text("https://api.ipify.org", timeout=6).strip()
    if not re.fullmatch(r"[0-9a-fA-F:.]{3,45}", text):
        raise net.NetError("resposta inválida")
    return text


class Nvml:
    """GPU NVIDIA pela biblioteca do próprio driver (nvml.dll). Sem NVIDIA, `ok` fica False."""

    class _Util(ctypes.Structure):
        _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

    class _Memory(ctypes.Structure):
        _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]

    def __init__(self) -> None:
        self.ok = False
        self.name = ""
        self.lib = None
        self.handle = ctypes.c_void_p()
        windir = os.environ.get("SystemRoot", r"C:\Windows")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        for candidate in (
            Path(windir) / "System32" / "nvml.dll",
            Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvml.dll",
        ):
            if not candidate.exists():
                continue
            try:
                lib = ctypes.CDLL(str(candidate))
                if lib.nvmlInit_v2() != 0:
                    continue
                lib.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
                if lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self.handle)) != 0:
                    continue
                name = ctypes.create_string_buffer(96)
                lib.nvmlDeviceGetName.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]
                if lib.nvmlDeviceGetName(self.handle, name, 96) == 0:
                    self.name = name.value.decode("utf-8", "replace")
                for fn in ("Temperature", "PowerUsage", "UtilizationRates", "MemoryInfo"):
                    getattr(lib, f"nvmlDeviceGet{fn}").restype = ctypes.c_int
                self.lib, self.ok = lib, True
                return
            except OSError:
                log.debug("nvml.dll não carregou de %s", candidate, exc_info=True)

    def read(self) -> Gpu:
        gpu = Gpu(name=self.name or "NVIDIA", source="nvml")
        lib, handle = self.lib, self.handle
        if lib is None:
            return gpu
        util = self._Util()
        if lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util)) == 0:
            gpu.util = float(util.gpu)
        mem = self._Memory()
        if lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem)) == 0:
            gpu.vram_used, gpu.vram_total = float(mem.used), float(mem.total)
        temp = ctypes.c_uint()
        if lib.nvmlDeviceGetTemperature(handle, 0, ctypes.byref(temp)) == 0:
            gpu.temp = float(temp.value)
        power = ctypes.c_uint()
        if lib.nvmlDeviceGetPowerUsage(handle, ctypes.byref(power)) == 0:
            gpu.power = power.value / 1000
        return gpu


class Pdh:
    """Consulta PDH (os mesmos contadores do Gerenciador de Tarefas), com nomes em inglês."""

    def __init__(self, counters: dict[str, str]) -> None:
        self.pdh = ctypes.windll.pdh  # type: ignore[attr-defined]
        self.pdh.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self.pdh.PdhAddEnglishCounterW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        self.pdh.PdhGetFormattedCounterArrayW.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self.pdh.PdhGetFormattedCounterArrayW.restype = wintypes.DWORD
        self.pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]
        self.query = ctypes.c_void_p()
        self.counters: dict[str, ctypes.c_void_p] = {}
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != 0:
            return
        for name, path in counters.items():
            handle = ctypes.c_void_p()
            if self.pdh.PdhAddEnglishCounterW(self.query, path, None, ctypes.byref(handle)) == 0:
                self.counters[name] = handle
        if self.counters:
            self.pdh.PdhCollectQueryData(self.query)  # contadores de taxa precisam de duas leituras

    def collect(self) -> dict[str, dict[str, float]]:
        if not self.counters or self.pdh.PdhCollectQueryData(self.query) != 0:
            return {}
        out: dict[str, dict[str, float]] = {}
        for name, handle in self.counters.items():
            size, count = wintypes.DWORD(0), wintypes.DWORD(0)
            fmt = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
            status = self.pdh.PdhGetFormattedCounterArrayW(
                handle, fmt, ctypes.byref(size), ctypes.byref(count), None
            )
            if status != PDH_MORE_DATA or not size.value:
                continue
            buf = ctypes.create_string_buffer(size.value)
            if self.pdh.PdhGetFormattedCounterArrayW(
                handle, fmt, ctypes.byref(size), ctypes.byref(count), buf
            ):
                continue
            items = (PDH_FMT_COUNTERVALUE_ITEM_W * count.value).from_buffer(buf)
            out[name] = {
                (item.szName or ""): float(item.FmtValue.doubleValue)
                for item in items
                if item.FmtValue.CStatus in (0, 1)  # PDH_CSTATUS_VALID_DATA / NEW_DATA
            }
        return out

    def close(self) -> None:
        if self.query.value:
            self.pdh.PdhCloseQuery(self.query)
            self.query = ctypes.c_void_p()


def adapter_info() -> tuple[str, float | None]:
    """Nome e VRAM total da placa de vídeo com mais memória (registro do driver de vídeo)."""
    import winreg

    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    best: tuple[str, float | None] = ("", None)
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return best
    with root:
        for index in range(32):
            try:
                sub = winreg.EnumKey(root, index)
            except OSError:
                break
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as key:
                    name = str(winreg.QueryValueEx(key, "DriverDesc")[0])
                    size = None
                    for value in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                        try:
                            raw = winreg.QueryValueEx(key, value)[0]
                        except OSError:
                            continue
                        size = float(int.from_bytes(raw, "little") if isinstance(raw, bytes) else int(raw))
                        break
            except OSError:
                continue
            if size and (best[1] is None or size > best[1]):
                best = (name, size)
            elif not best[0]:
                best = (name, best[1])
    return best


class Sampler:
    """Guarda o estado entre leituras (deltas de CPU, processos e rede). Uma leitura por vez."""

    def __init__(self) -> None:
        self.windows = sys.platform == "win32"
        self.cores = os.cpu_count() or 1
        self._cpu: tuple[int, int, int] | None = None
        self._procs: dict[int, int] = {}
        self._procs_at = 0.0
        self._net: tuple[int, int, float] | None = None
        self._nvml: Nvml | None = None
        self._pdh: Pdh | None = None
        self._adapter: tuple[str, float | None] | None = None
        self._ping: tuple[float, float | None] = (0.0, None)

    def _gpu_sources(self) -> None:
        if self._nvml is None:
            self._nvml = Nvml()
        if self._pdh is None:
            counters = {"thermal": r"\Thermal Zone Information(*)\Temperature"}
            if not self._nvml.ok:
                counters |= {
                    "engine": r"\GPU Engine(*engtype_3D)\Utilization Percentage",
                    "vram": r"\GPU Adapter Memory(*)\Dedicated Usage",
                }
            self._pdh = Pdh(counters)

    def sample(self, detail: bool = False, network: bool = True, light: bool = False) -> Snapshot:
        snap = Snapshot(at=time.time(), cores=self.cores)
        if not self.windows:
            path, used, total = disk("/")
            snap.disk_path, snap.disk_used, snap.disk_total = path, used, total
            return snap
        snap.ram_used, snap.ram_total = memory()
        self._gpu_sources()
        pdh = self._pdh.collect() if self._pdh else {}
        snap.cpu_temp = thermal_celsius(list(pdh.get("thermal", {}).values()))
        if self._nvml and self._nvml.ok:
            snap.gpu = self._nvml.read()
        else:
            if self._adapter is None:
                self._adapter = adapter_info()
            name, total = self._adapter
            vram = pdh.get("vram", {})
            snap.gpu = Gpu(
                name=name,
                util=gpu_utilization(pdh.get("engine", {})),
                vram_used=max(vram.values()) if vram else None,
                vram_total=total,
                source="pdh" if pdh.get("engine") is not None else "",
            )
        if light:
            return snap
        cur = system_times()
        snap.cpu = cpu_percent(self._cpu, cur)
        self._cpu = cur
        snap.disk_path, snap.disk_used, snap.disk_total = disk()
        snap.battery = battery()
        if network:
            self._read_network(snap)
        if detail:
            self._read_processes(snap)
        return snap

    def _read_network(self, snap: Snapshot) -> None:
        now = time.monotonic()
        totals = network_totals()
        if totals is not None:
            if self._net is not None:
                seconds = now - self._net[2]
                snap.net_down = rate(self._net[0], totals[0], seconds)
                snap.net_up = rate(self._net[1], totals[1], seconds)
            self._net = (totals[0], totals[1], now)
        last, value = self._ping
        if now - last >= PING_EVERY:
            value = None
            for host in PING_HOSTS:
                value = ping(host)
                if value is not None:
                    break
            self._ping = (now, value)
        snap.ping = value

    def _read_processes(self, snap: Snapshot) -> None:
        now = time.monotonic()
        rows = processes()
        seconds = now - self._procs_at if self._procs_at else 0.0
        cpu_by: dict[str, float] = {}
        mem_by: dict[str, float] = {}
        paths: dict[str, str] = {}
        current: dict[int, int] = {}
        for pid, path, cpu, mem in rows:
            name = Path(path).name.lower()
            paths.setdefault(name, path)
            current[pid] = cpu
            mem_by[name] = mem_by.get(name, 0.0) + mem
            before = self._procs.get(pid)
            if seconds > 0 and before is not None and cpu >= before:
                share = (cpu - before) / (seconds * 1e7 * self.cores) * 100
                cpu_by[name] = cpu_by.get(name, 0.0) + share
        self._procs, self._procs_at = current, now
        snap.top_cpu = [(display_name(paths[n]), min(100.0, v)) for n, v in top(cpu_by, minimum=0.05)]
        snap.top_ram = [(display_name(paths[n]), v) for n, v in top(mem_by)]


# ---------- monitor (thread da interface) ----------
class HardwareMonitor(QObject):
    updated = Signal(object)  # Snapshot
    alert = Signal(str, str, str)  # tipo, título, texto

    def __init__(
        self,
        run: Callable[..., None],
        pinned: Callable[[], list[str]],
        alerts_on: Callable[[], bool],
        sampler: Sampler | None = None,
    ) -> None:
        super().__init__()
        self._run, self._pinned, self._alerts_on = run, pinned, alerts_on
        self.sampler = sampler or Sampler()
        self.last: Snapshot | None = None
        self.history: dict[str, deque[float]] = {m: deque(maxlen=HISTORY) for m in METRICS}
        self._viewers: set[str] = set()
        self._busy = False
        self._started = False
        self._alerted: dict[str, float] = {}
        self.timer = QTimer(self, interval=INTERVAL_MS)
        self.timer.timeout.connect(self._tick)
        self.slow = QTimer(self, interval=ALERT_INTERVAL_MS)
        self.slow.timeout.connect(lambda: self._tick(light=True))

    def start(self) -> None:
        self._started = True
        self.sync()

    def stop(self) -> None:
        self._started = False
        self.sync()

    def watch(self, viewer: str, on: bool) -> None:
        """A aba Sistema (ou outra tela) avisa quando aparece e some."""
        if on:
            self._viewers.add(viewer)
        else:
            self._viewers.discard(viewer)
        self.sync()
        if on and self._started:
            self._tick()

    @property
    def live(self) -> bool:
        return self._started and bool(self._viewers or self._pinned())

    def sync(self) -> None:
        if self.live:
            self.slow.stop()
            if not self.timer.isActive():
                self.timer.start()
        else:
            self.timer.stop()
            if self._started and self._alerts_on():
                if not self.slow.isActive():
                    self.slow.start()
            else:
                self.slow.stop()

    def _tick(self, light: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        detail = "page" in self._viewers
        self._run(self.sampler.sample, detail, True, light, done=self._done, fail=self._failed, owner=self)

    def _failed(self, error: BaseException) -> None:
        self._busy = False
        log.debug("Leitura de hardware falhou: %r", error)

    def _done(self, snap: Snapshot) -> None:
        self._busy = False
        light = snap.cpu is None and not snap.disk_total
        if not light:
            self.last = snap
            for metric in METRICS:
                value = snap.value(metric)
                if value is not None:
                    self.history[metric].append(value)
            self.updated.emit(snap)
        self.check_alerts(snap)

    def check_alerts(self, snap: Snapshot, now: float | None = None) -> list[str]:
        """Avisa de GPU/CPU acima de 85 °C e RAM acima de 92% (no máximo uma vez a cada 10 min)."""
        if not self._alerts_on():
            return []
        now = time.time() if now is None else now
        fired = []
        checks = (
            ("gpu_temp", snap.gpu.temp, TEMP_LIMIT, "GPU muito quente", "{:.0f} °C na placa de vídeo"),
            ("cpu_temp", snap.cpu_temp, TEMP_LIMIT, "Processador muito quente", "{:.0f} °C no processador"),
            ("ram", snap.ram_pct, RAM_LIMIT, "Memória quase cheia", "{:.0f}% da RAM em uso"),
        )
        for kind, value, limit, title, text in checks:
            if value is None or value <= limit:
                continue
            if now - self._alerted.get(kind, 0.0) < ALERT_COOLDOWN:
                continue
            self._alerted[kind] = now
            fired.append(kind)
            self.alert.emit(kind, title, text.format(value))
        return fired
