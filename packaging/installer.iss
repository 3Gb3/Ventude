; Instalador do Ventude (Inno Setup 6). Gerado pelo GitHub Actions a cada push na main.
;   iscc /DAppVersion=1.0.12 packaging\installer.iss
; Instala sem pedir administrador em %LOCALAPPDATA%\Programs\Ventude,
; cria atalho na área de trabalho e no menu Iniciar, e reabre o app ao terminar.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{F9EE6E10-6930-423A-AFA4-C6EEACD5ECFE}
AppName=Ventude
AppVersion={#AppVersion}
AppVerName=Ventude {#AppVersion}
AppPublisher=Ventude
AppPublisherURL=https://github.com/3Gb3/Ventude
AppUpdatesURL=https://github.com/3Gb3/Ventude/releases
VersionInfoVersion={#AppVersion}.0
DefaultDirName={localappdata}\Programs\Ventude
DefaultGroupName=Ventude
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Ventude-Setup
SetupIconFile=ventude.ico
UninstallDisplayIcon={app}\Ventude.exe
UninstallDisplayName=Ventude
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[InstallDelete]
; Remove arquivos da versão anterior que não existem mais na nova.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Ventude\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\Ventude"; Filename: "{app}\Ventude.exe"; Comment: "Dynamic Island do Ventude"
Name: "{autoprograms}\Ventude"; Filename: "{app}\Ventude.exe"; Comment: "Dynamic Island do Ventude"

[Registry]
; Só apaga o "Iniciar com o Windows" ao desinstalar (quem cria é o próprio app).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Ventude"; ValueType: none; Flags: uninsdeletevalue

[Run]
; Sem "skipifsilent": depois de uma atualização silenciosa o Ventude reabre sozinho.
Filename: "{app}\Ventude.exe"; Description: "Abrir o Ventude agora"; Flags: nowait postinstall

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM Ventude.exe"; Flags: runhidden; RunOnceId: "FecharVentude"
