# -*- mode: python ; coding: utf-8 -*-
# Build (na raiz do projeto):  uv run pyinstaller --noconfirm packaging\ventude.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("ventude")
try:
    hidden += collect_submodules("winrt")
except Exception:
    pass

# Módulos que o Ventude não usa: fora do pacote, ele fica bem menor.
EXCLUDES = [
    "tkinter",
    "unittest",
    "pydoc_data",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtXml",
    "PySide6.QtDBus",
    "PySide6.QtConcurrent",
    "PySide6.QtHelp",
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
    "PySide6.QtPrintSupport",
]
DROP_BINARIES = ("qt6quick", "qt6qml", "qt6pdf", "qt6opengl", "opengl32sw", "qt6virtualkeyboard")

a = Analysis(
    ["../src/ventude/__main__.py"],
    pathex=["../src"],
    hiddenimports=sorted(set(hidden)),
    excludes=EXCLUDES,
    noarchive=False,
)


def keep_binary(entry):
    name = Path(entry[0]).name.lower()
    # O Qt usa o ICU do Windows; um ICU privado no PATH da máquina de build quebra o app.
    if name == "icuuc.dll" or name.startswith("icudt"):
        return False
    return not name.startswith(DROP_BINARIES)


a.binaries = [b for b in a.binaries if keep_binary(b)]
a.datas = [d for d in a.datas if "/translations/" not in d[0].replace("\\", "/")]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ventude",
    icon="ventude.ico",
    console=False,
    upx=False,
    strip=False,
    debug=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Ventude")
