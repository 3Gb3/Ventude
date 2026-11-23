"""Luz noturna do Windows ligada/desligada direto pelo Ventude (sem abrir as Configurações).

O Windows não tem API pública para isso. O estado fica num blob do CloudStore no registro do usuário
(HKCU, sem administrador), e o próprio Windows aplica a mudança assim que o valor é regravado:

- desligada: ``... 2A 2B 0E 13 | 43 42 01 00 | D0 0A ...``
- ligada:    ``... 2A 2B 0E 15 | 43 42 01 00 | 10 00 | D0 0A ...``  (2 bytes a mais; o 0x13 vira 0x15)

Os bytes 10..14 são um carimbo de tempo: sobe um para o Windows perceber a troca.
Se o blob não tiver o formato esperado, nada é gravado (o botão abre as Configurações).
"""

from __future__ import annotations

import sys

KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
    r"\default$windows.data.bluelightreduction.bluelightreductionstate"
    r"\windows.data.bluelightreduction.bluelightreductionstate"
)
SIZE_BYTE = 18
OFF, ON = 0x13, 0x15
HEADER = bytes((0x43, 0x42, 0x01, 0x00))
HEADER_AT = 19
FLAG = bytes((0x10, 0x00))
STAMP = range(10, 15)


def is_on(data: bytes) -> bool | None:
    """True/False pelo blob; None se o formato não for o conhecido."""
    if len(data) <= HEADER_AT + 4 or data[HEADER_AT : HEADER_AT + 4] != HEADER:
        return None
    if data[SIZE_BYTE] == ON and data[HEADER_AT + 4 : HEADER_AT + 6] == FLAG:
        return True
    if data[SIZE_BYTE] == OFF:
        return False
    return None


def toggled(data: bytes, on: bool) -> bytes | None:
    """Blob novo com a luz noturna ligada/desligada (None se o formato for desconhecido)."""
    state = is_on(data)
    if state is None:
        return None
    out = bytearray(data)
    cut = HEADER_AT + 4
    if on and not state:
        out[cut:cut] = FLAG
        out[SIZE_BYTE] = ON
    elif not on and state:
        del out[cut : cut + 2]
        out[SIZE_BYTE] = OFF
    for i in STAMP:  # carimbo de tempo (varint): sobe um no primeiro byte que dá
        if out[i] != 0xFF:
            out[i] += 1
            break
    return bytes(out)


def _read() -> bytes | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            value, kind = winreg.QueryValueEx(key, "Data")
    except OSError:
        return None
    return bytes(value) if kind == winreg.REG_BINARY else None


def state() -> bool | None:
    """Luz noturna ligada agora? None se não deu para ler (Windows sem o recurso, formato novo...)."""
    data = _read()
    return is_on(data) if data is not None else None


def set_on(on: bool) -> bool:
    """Liga/desliga. False quando não deu (aí o certo é abrir as Configurações)."""
    data = _read()
    if data is None:
        return False
    new = toggled(data, on)
    if new is None:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "Data", 0, winreg.REG_BINARY, new)
    except OSError:
        return False
    return True


__all__ = ["is_on", "set_on", "state", "toggled"]
