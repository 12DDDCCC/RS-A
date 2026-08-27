; RS-A 桌面版安装包规格 (Inno Setup 6)
; 构建: ISCC.exe RS-A.iss  (产物: cache\releases\RS-A-Setup-<ver>.exe)
; 原则: per-user 安装免管理员; 只装 RS-A.exe + _internal (运行时数据
; rs-a.env/cache 由程序首启自建 —— 主密钥不进安装包)
#define MyAppName "RS-A"
#define MyAppVer "0.3.2"

[Setup]
AppId={{8735FE11-BB8E-49A1-99E6-7F9374D61B48}}
AppName={#MyAppName} · 俯瞰世界
AppVersion={#MyAppVer}
AppPublisher={#MyAppName}
DefaultDirName={localappdata}\{#MyAppName}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\..\cache\releases
OutputBaseFilename=RS-A-Setup-{#MyAppVer}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\RS-A.exe
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional:"

[Files]
Source: "..\..\cache\releases\app-payload\RS-A\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}\{#MyAppName}"; Filename: "{app}\RS-A.exe"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\RS-A.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RS-A.exe"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
