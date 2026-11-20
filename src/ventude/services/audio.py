"""Áudio do Windows pelo Core Audio (COM via ctypes, sem pacotes extras).

- Saídas (fones, caixas, HDMI, Bluetooth) e entradas (microfones) ativas, via `IMMDeviceEnumerator`.
- Trocar o dispositivo padrão com 1 clique (`IPolicyConfig::SetDefaultEndpoint` nos três papéis:
  console, multimídia e comunicações).
- Volume e mudo do master e do microfone (`IAudioEndpointVolume`).
- Mixer por aplicativo (`IAudioSessionManager2` → `ISimpleAudioVolume`), agrupado pelo .exe.

Tudo roda numa thread própria ("ventude-audio") com o COM inicializado uma vez. Comandos seguidos
do mesmo tipo (ex.: arrastar um slider) são juntados: só o último valor é aplicado.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, Signal

from ventude.services import com

log = logging.getLogger(__name__)

CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
IID_IAudioEndpointVolume = "5CDF2C82-841E-4546-9722-0CF74078229A"
IID_IAudioSessionManager2 = "77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"
IID_IAudioSessionControl2 = "BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"
IID_ISimpleAudioVolume = "87CE5498-68D6-44E5-9215-6DA47EF883D8"
CLSID_PolicyConfigClient = "870AF99C-171D-4F9E-AF0D-E63DF40C2BC9"
IID_IPolicyConfig = "F8679F50-850A-41CF-9C72-430F290290C8"
PKEY_FRIENDLY_NAME = ("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)
RENDER, CAPTURE = 0, 1
CONSOLE, MULTIMEDIA, COMMUNICATIONS = 0, 1, 2
DEVICE_STATE_ACTIVE = 1
SESSION_EXPIRED = 2
STEP = 0.02  # 2% por "dente" da roda do mouse, como as teclas de volume


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    flow: str  # render | capture
    default: bool = False

    @property
    def kind(self) -> str:
        return device_kind(self.name, self.flow)


@dataclass(frozen=True)
class AppSession:
    key: str  # caminho do .exe em minúsculas, "system" ou "pid:N"
    name: str
    path: str
    pids: tuple[int, ...]
    level: float  # 0..1
    muted: bool
    active: bool = False
    system: bool = False


@dataclass(frozen=True)
class AudioState:
    outputs: tuple[Device, ...] = ()
    inputs: tuple[Device, ...] = ()
    master: float | None = None
    master_muted: bool = False
    mic: float | None = None
    mic_muted: bool = False
    sessions: tuple[AppSession, ...] = ()

    @property
    def default_output(self) -> Device | None:
        return next((d for d in self.outputs if d.default), None)

    @property
    def default_input(self) -> Device | None:
        return next((d for d in self.inputs if d.default), None)


def device_kind(name: str, flow: str) -> str:
    """Ícone do dispositivo pelo nome: speaker | headphones | hdmi | bluetooth | mic."""
    if flow == "capture":
        return "mic"
    lowered = name.lower()
    if any(
        w in lowered for w in ("headphone", "headset", "fone", "airpods", "buds", "earphone", "auricular")
    ):
        return "headphones"
    if any(
        w in lowered
        for w in ("hdmi", "displayport", "display audio", "monitor", " tv", "high definition audio")
    ):
        return "hdmi"
    if any(w in lowered for w in ("bluetooth", "hands-free", "handsfree", "a2dp")):
        return "bluetooth"
    return "speaker"


KIND_ICONS = {
    "speaker": "volume",
    "headphones": "headphones",
    "hdmi": "monitor",
    "bluetooth": "bluetooth",
    "mic": "mic",
}


def short_device_name(name: str) -> str:
    """'Alto-falantes (Realtek(R) Audio)' -> 'Alto-falantes · Realtek(R) Audio'."""
    if name.endswith(")") and " (" in name:
        head, _, tail = name.partition(" (")
        return f"{head} · {tail[:-1]}"
    return name


def group_sessions(raw: list[dict]) -> tuple[AppSession, ...]:
    """Junta as sessões do mesmo .exe (ex.: várias abas do navegador) numa linha só."""
    from ventude.services.winutil import app_name

    groups: dict[str, dict] = {}
    for item in raw:
        if item.get("state") == SESSION_EXPIRED:
            continue
        system = bool(item.get("system"))
        path = item.get("path") or ""
        key = "system" if system else (path.lower() if path else f"pid:{item.get('pid', 0)}")
        group = groups.setdefault(
            key,
            {
                "key": key,
                "path": "" if system else path,
                "name": "Sons do sistema" if system else (app_name(path) or item.get("display") or "App"),
                "pids": [],
                "levels": [],
                "mutes": [],
                "active": False,
                "system": system,
            },
        )
        group["pids"].append(int(item.get("pid") or 0))
        group["levels"].append(float(item.get("level", 1.0)))
        group["mutes"].append(bool(item.get("muted")))
        group["active"] = group["active"] or item.get("state") == 1
    sessions = [
        AppSession(
            key=g["key"],
            name=g["name"],
            path=g["path"],
            pids=tuple(dict.fromkeys(g["pids"])),
            level=max(g["levels"]),
            muted=all(g["mutes"]),
            active=g["active"],
            system=g["system"],
        )
        for g in groups.values()
    ]
    sessions.sort(key=lambda s: (s.system, not s.active, s.name.lower()))
    return tuple(sessions)


# ---------- Core Audio (só Windows; chame numa thread com com.init()) ----------
class CoreAudio:
    def __init__(self) -> None:
        self._enum: com.ComPtr | None = None

    def enumerator(self) -> com.ComPtr:
        if self._enum is None or not self._enum:
            self._enum = com.create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator, "IMMDeviceEnumerator")
        return self._enum

    # ---------- dispositivos ----------
    @staticmethod
    def _device_id(device: com.ComPtr) -> str:
        pointer = ctypes.c_void_p()
        device.call(5, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(pointer), what="GetId")
        return com.take_wstr(pointer)

    @staticmethod
    def _device_name(device: com.ComPtr) -> str:
        with device.out_ptr(4, [wintypes.DWORD], 0, name="IPropertyStore") as store:
            key = com.PROPERTYKEY(com.guid(PKEY_FRIENDLY_NAME[0]), PKEY_FRIENDLY_NAME[1])
            value = com.PROPVARIANT()
            store.call(
                5,
                [ctypes.POINTER(com.PROPERTYKEY), ctypes.POINTER(com.PROPVARIANT)],
                ctypes.byref(key),
                ctypes.byref(value),
                what="GetValue",
            )
            return com.propvariant_text(value)

    def _default(self, flow: int) -> com.ComPtr | None:
        try:
            return self.enumerator().out_ptr(4, [ctypes.c_int, ctypes.c_int], flow, CONSOLE, name="IMMDevice")
        except com.ComError:
            return None  # nenhum dispositivo desse tipo

    def devices(self, flow: int) -> list[Device]:
        default = self._default(flow)
        default_id = ""
        if default:
            with default:
                default_id = self._device_id(default)
        out: list[Device] = []
        with self.enumerator().out_ptr(
            3, [ctypes.c_int, wintypes.DWORD], flow, DEVICE_STATE_ACTIVE, name="IMMDeviceCollection"
        ) as collection:
            count = ctypes.c_uint()
            collection.call(3, [ctypes.POINTER(ctypes.c_uint)], ctypes.byref(count))
            for index in range(count.value):
                with collection.out_ptr(4, [ctypes.c_uint], index, name="IMMDevice") as device:
                    device_id = self._device_id(device)
                    out.append(
                        Device(
                            device_id,
                            self._device_name(device) or "Dispositivo de áudio",
                            "render" if flow == RENDER else "capture",
                            device_id == default_id,
                        )
                    )
        return out

    @staticmethod
    def _activate(device: com.ComPtr, iid: str, name: str) -> com.ComPtr:
        return device.out_ptr(
            3,
            [ctypes.POINTER(com.GUID), wintypes.DWORD, ctypes.c_void_p],
            ctypes.byref(com.guid(iid)),
            com.CLSCTX_ALL,
            None,
            name=name,
            what=f"Activate({name})",
        )

    def _endpoint(self, flow: int) -> com.ComPtr | None:
        device = self._default(flow)
        if not device:
            return None
        with device:
            return self._activate(device, IID_IAudioEndpointVolume, "IAudioEndpointVolume")

    # ---------- volume do dispositivo padrão ----------
    def endpoint_volume(self, flow: int) -> tuple[float | None, bool]:
        volume = self._endpoint(flow)
        if not volume:
            return None, False
        with volume:
            level, mute = ctypes.c_float(), wintypes.BOOL()
            volume.call(
                9, [ctypes.POINTER(ctypes.c_float)], ctypes.byref(level), what="GetMasterVolumeLevelScalar"
            )
            volume.call(15, [ctypes.POINTER(wintypes.BOOL)], ctypes.byref(mute), what="GetMute")
            return max(0.0, min(1.0, float(level.value))), bool(mute.value)

    def set_endpoint_volume(self, flow: int, level: float | None = None, mute: bool | None = None) -> None:
        volume = self._endpoint(flow)
        if not volume:
            return
        with volume:
            if level is not None:
                value = ctypes.c_float(max(0.0, min(1.0, level)))
                volume.call(
                    7, [ctypes.c_float, ctypes.c_void_p], value, None, what="SetMasterVolumeLevelScalar"
                )
            if mute is not None:
                volume.call(14, [wintypes.BOOL, ctypes.c_void_p], bool(mute), None, what="SetMute")

    def step_master(self, delta: float) -> tuple[float | None, bool]:
        level, muted = self.endpoint_volume(RENDER)
        if level is None:
            return None, muted
        level = round(max(0.0, min(1.0, level + delta)) / STEP) * STEP
        unmute = False if muted and delta > 0 else None  # subir o volume tira do mudo, como no Windows
        self.set_endpoint_volume(RENDER, level, unmute)
        return level, bool(muted and unmute is None)

    def toggle_mic_mute(self) -> bool | None:
        level, muted = self.endpoint_volume(CAPTURE)
        if level is None:
            return None
        self.set_endpoint_volume(CAPTURE, mute=not muted)
        return not muted

    # ---------- sessões (mixer por app) ----------
    def _each_session(self, callback: Callable[[dict, com.ComPtr], None]) -> None:
        from ventude.services.winutil import process_path

        device = self._default(RENDER)
        if not device:
            return
        with device, self._activate(device, IID_IAudioSessionManager2, "IAudioSessionManager2") as manager:
            with manager.out_ptr(5, [], name="IAudioSessionEnumerator") as sessions:
                count = ctypes.c_int()
                sessions.call(3, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(count))
                for index in range(count.value):
                    with sessions.out_ptr(4, [ctypes.c_int], index, name="IAudioSessionControl") as control:
                        try:
                            info = self._session_info(control, process_path)
                        except com.ComError:
                            continue
                        with control.query(com.guid(IID_ISimpleAudioVolume), "ISimpleAudioVolume") as simple:
                            callback(info, simple)

    @staticmethod
    def _session_info(control: com.ComPtr, process_path: Callable[[int], str]) -> dict:
        state = ctypes.c_int()
        control.call(3, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(state), what="GetState")
        display = ctypes.c_void_p()
        control.call(4, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(display), what="GetDisplayName")
        info = {
            "state": state.value,
            "display": com.take_wstr(display),
            "pid": 0,
            "system": False,
            "path": "",
        }
        with control.query(com.guid(IID_IAudioSessionControl2), "IAudioSessionControl2") as control2:
            pid = wintypes.DWORD()
            control2.call(14, [ctypes.POINTER(wintypes.DWORD)], ctypes.byref(pid), what="GetProcessId")
            info["pid"] = int(pid.value)
            info["system"] = control2.call(15, [], what="IsSystemSoundsSession") == com.S_OK
        if info["display"].startswith("@"):
            info["display"] = ""
        if info["pid"] and not info["system"]:
            info["path"] = process_path(info["pid"])
        return info

    def sessions(self) -> tuple[AppSession, ...]:
        raw: list[dict] = []

        def read(info: dict, simple: com.ComPtr) -> None:
            level, mute = ctypes.c_float(), wintypes.BOOL()
            simple.call(4, [ctypes.POINTER(ctypes.c_float)], ctypes.byref(level), what="GetMasterVolume")
            simple.call(6, [ctypes.POINTER(wintypes.BOOL)], ctypes.byref(mute), what="GetMute")
            raw.append({**info, "level": float(level.value), "muted": bool(mute.value)})

        self._each_session(read)
        return group_sessions(raw)

    def set_session(self, key: str, level: float | None = None, mute: bool | None = None) -> None:
        def apply(info: dict, simple: com.ComPtr) -> None:
            path = (info.get("path") or "").lower()
            this = "system" if info.get("system") else (path or f"pid:{info.get('pid', 0)}")
            if this != key:
                return
            if level is not None:
                value = ctypes.c_float(max(0.0, min(1.0, level)))
                simple.call(3, [ctypes.c_float, ctypes.c_void_p], value, None, what="SetMasterVolume")
            if mute is not None:
                simple.call(5, [wintypes.BOOL, ctypes.c_void_p], bool(mute), None, what="SetMute")

        self._each_session(apply)

    # ---------- dispositivo padrão ----------
    @staticmethod
    def set_default(device_id: str) -> None:
        with com.create(CLSID_PolicyConfigClient, IID_IPolicyConfig, "IPolicyConfig") as policy:
            for role in (CONSOLE, MULTIMEDIA, COMMUNICATIONS):
                policy.call(13, [ctypes.c_wchar_p, ctypes.c_int], device_id, role, what="SetDefaultEndpoint")

    def read_state(self) -> AudioState:
        master, master_muted = self.endpoint_volume(RENDER)
        mic, mic_muted = self.endpoint_volume(CAPTURE)
        return AudioState(
            outputs=tuple(self.devices(RENDER)),
            inputs=tuple(self.devices(CAPTURE)),
            master=master,
            master_muted=master_muted,
            mic=mic,
            mic_muted=mic_muted,
            sessions=self.sessions(),
        )


# ---------- serviço (thread própria, resultados por sinais Qt) ----------
class AudioService(QObject):
    changed = Signal(object)  # AudioState
    master_feedback = Signal(float, bool)  # volume depois da roda do mouse na pílula, mudo?
    mic_feedback = Signal(bool)  # microfone mutado?
    failed = Signal(str)

    def __init__(self, backend: object | None = None) -> None:
        super().__init__()
        self.state = AudioState()
        self._backend = backend if backend is not None else (CoreAudio() if com.available() else None)
        self._pending: dict[str, Callable[[], None]] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._step = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._backend is None or self.running:
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._loop, name="ventude-audio", daemon=True)
        self._thread.start()
        self.refresh()

    def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None

    def _submit(self, key: str, job: Callable[[], None]) -> None:
        with self._lock:
            self._pending.pop(key, None)
            self._pending[key] = job
        self._wake.set()

    def _loop(self) -> None:
        try:
            com.init()
        except OSError as exc:
            log.warning("Áudio indisponível: %s", exc)
            return
        while not self._stopping:
            self._wake.wait()
            self._wake.clear()
            if self._stopping:
                break
            with self._lock:
                jobs = list(self._pending.values())
                self._pending.clear()
            for job in jobs:
                try:
                    job()
                except Exception as exc:  # noqa: BLE001
                    log.debug("Comando de áudio falhou", exc_info=True)
                    self.failed.emit(str(exc))
            try:
                self.state = self._backend.read_state()  # type: ignore[union-attr]
                self.changed.emit(self.state)
            except Exception:  # noqa: BLE001
                log.debug("Leitura do áudio falhou", exc_info=True)

    # ---------- comandos (qualquer thread) ----------
    def refresh(self) -> None:
        self._submit("refresh", lambda: None)

    def set_master(self, level: float) -> None:
        self.state = replace(self.state, master=level)
        self._submit("master", lambda: self._backend.set_endpoint_volume(RENDER, level))  # type: ignore[union-attr]

    def set_master_mute(self, mute: bool) -> None:
        self._submit("master-mute", lambda: self._backend.set_endpoint_volume(RENDER, mute=mute))  # type: ignore[union-attr]

    def set_mic(self, level: float) -> None:
        self.state = replace(self.state, mic=level)
        self._submit("mic", lambda: self._backend.set_endpoint_volume(CAPTURE, level))  # type: ignore[union-attr]

    def set_mic_mute(self, mute: bool) -> None:
        self._submit("mic-mute", lambda: self._backend.set_endpoint_volume(CAPTURE, mute=mute))  # type: ignore[union-attr]

    def set_session(self, key: str, level: float) -> None:
        self._submit(f"session:{key}", lambda: self._backend.set_session(key, level=level))  # type: ignore[union-attr]

    def set_session_mute(self, key: str, mute: bool) -> None:
        self._submit(f"session-mute:{key}", lambda: self._backend.set_session(key, mute=mute))  # type: ignore[union-attr]

    def set_default(self, device_id: str) -> None:
        self._submit(f"default:{device_id}", lambda: self._backend.set_default(device_id))  # type: ignore[union-attr]

    def step_master(self, up: bool, steps: int = 1) -> None:
        """Roda do mouse na pílula: soma os "dentes" e aplica de uma vez, avisando o volume novo."""
        with self._lock:
            self._step += (STEP if up else -STEP) * max(1, steps)

        def apply() -> None:
            with self._lock:
                delta, self._step = self._step, 0.0
            if delta:
                level, muted = self._backend.step_master(delta)  # type: ignore[union-attr]
                if level is not None:
                    self.master_feedback.emit(level, muted)

        self._submit("step", apply)

    def toggle_mic_mute(self) -> None:
        def apply() -> None:
            muted = self._backend.toggle_mic_mute()  # type: ignore[union-attr]
            if muted is not None:
                self.mic_feedback.emit(muted)

        self._submit("mic-toggle", apply)
