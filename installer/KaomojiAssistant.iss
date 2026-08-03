; Kaomoji Assistant 安装脚本（Inno Setup）
; 编译：用 Inno Setup Compiler 打开本文件，点击 Build。
; 前置：先运行 build.py 生成 dist/KaomojiAssistant/。

#define MyAppName "Kaomoji Assistant"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Kaomoji Assistant"
#define MyAppURL "https://github.com/owoflying/kaomoji-assistant"
#define MyAppExeName "KaomojiAssistant.exe"
#define MySource "..\dist\KaomojiAssistant"

[Setup]
AppId={{8F3B9C2A-1D4E-4F2A-9B6C-3E5A7D9C0B1F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=out
OutputBaseFilename=KaomojiAssistant-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallPreviousVersion=yes
MinVersion=10.0.17763

[Languages]
; 中文安装向导。如需英文，把下一行换成 compiler:Default.isl
Name: "chinese"; MessagesFile: "compiler:ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项："
Name: "autostart"; Description: "开机自动启动"; GroupDescription: "附加选项："

[Files]
; MySource 相对本脚本（installer/）上一级的项目根目录解析
Source: "{#MySource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Registry]
; 勾选「开机自动启动」任务时写入 HKCU 启动项；卸载时自动清理
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "KaomojiAssistant"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: autostart; Flags: uninsdeletevalue
