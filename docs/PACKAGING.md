# Empacotar o Ventude

Em desenvolvimento:

```powershell
uv sync
uv run ventude
```

## Executável (PyInstaller, pasta única)

O PyInstaller já está no grupo `dev` do projeto:

```powershell
uv sync
uv run pyinstaller --noconfirm packaging\ventude.spec
```

O resultado fica em `dist\Ventude\Ventude.exe`. Leve a pasta `dist\Ventude` inteira: o `.exe` precisa da pasta `_internal` ao lado.

O spec:

- usa o ícone `packaging/ventude.ico`;
- abre sem console (`console=False`);
- exclui os módulos Qt que o Ventude não usa (QML, Quick, PDF, SQL, OpenGL etc.) e as traduções do Qt;
- descarta um ICU privado que esteja no PATH da máquina de build (o Qt usa o ICU do Windows).

Os dados continuam em `%APPDATA%\Ventude`, fora do pacote.

## Observações

- O Windows pode mostrar o aviso do SmartScreen porque o `.exe` não é assinado: **Mais informações > Executar assim mesmo**.
- Com o pacote, **Ajustes > Iniciar com o Windows** registra o próprio `Ventude.exe`.
- Avisos do build sobre plugins SQL do Qt (`fbclient.dll`, `OCI.dll`, `LIBPQ.dll`) podem ser ignorados.
