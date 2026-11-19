# Empacotar e publicar o Ventude

## Automático (recomendado)

O workflow [`.github/workflows/release.yml`](../.github/workflows/release.yml) roda a cada push na `main`, num Windows do GitHub:

1. `ruff` e `pytest`.
2. Carimba a versão `X.Y.N` (X.Y do `pyproject.toml`, N = número do build) em `src/ventude/_build.py`.
3. Gera `dist\Ventude\Ventude.exe` com o PyInstaller e testa se ele abre.
4. Gera `dist\Ventude-Setup.exe` com o Inno Setup (`packaging/installer.iss`).
5. Publica a Release `vX.Y.N` com o instalador e as mudanças desde a versão anterior.

Só a `main` publica versões. O app instalado consulta `releases/latest` ao iniciar e oferece a atualização.

## Manual (no Windows)

```powershell
uv sync
uv run python scripts/stamp_version.py 0
uv run pyinstaller --noconfirm packaging\ventude.spec
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" packaging\installer.iss
```

Resultado: `dist\Ventude\Ventude.exe` (pasta portátil) e `dist\Ventude-Setup.exe` (instalador).

## O instalador

- Instala em `%LOCALAPPDATA%\Programs\Ventude`, sem pedir administrador.
- Cria atalhos na área de trabalho e no menu Iniciar.
- Aparece em "Aplicativos instalados" para desinstalar. A desinstalação remove o "Iniciar com o Windows" e mantém seus dados em `%APPDATA%\Ventude`.
- Na atualização, roda em silêncio (`/VERYSILENT`), fecha o Ventude, troca os arquivos e o reabre.

## O executável

- Usa o ícone `packaging/ventude.ico` e abre sem console.
- Exclui os módulos Qt que o Ventude não usa (QML, Quick, PDF, SQL, OpenGL etc.) e as traduções do Qt.
- Descarta um ICU privado que esteja no PATH da máquina de build (o Qt usa o ICU do Windows).

## Observações

- O SmartScreen avisa porque nem o instalador nem o `.exe` são assinados: **Mais informações > Executar assim mesmo**.
- Avisos do build sobre plugins SQL do Qt (`fbclient.dll`, `OCI.dll`, `LIBPQ.dll`) podem ser ignorados.
