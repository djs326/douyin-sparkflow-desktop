; ============================================================
; DouYin SparkFlow 桌面版安装程序（Inno Setup 6）
; 构建脚本会传入 /DAppVersion 与 /DAppSourceDir
; ============================================================

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef AppSourceDir
  #define AppSourceDir "..\..\dist\app"
#endif

#define MyAppName "DouYin SparkFlow"
#define MyAppPublisher "DouYinSparkFlow"
#define MyAppExeName "DouYinSparkFlow.exe"
#define MyAppId "8B3E6C2A-5D91-4A7F-9E4C-4E9F1A2B3C4D"

[Setup]
AppId={{8B3E6C2A-5D91-4A7F-9E4C-DouYinSparkFlow01}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\DouYinSparkFlow
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; per-user 安装，无需管理员权限
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputBaseFilename=DouYinSparkFlow-Setup-{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 运行时数据存于 %APPDATA%\DouYinSparkFlow，卸载时默认保留
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时可选删除用户数据（默认不删除，勾选才删）
Type: filesandordirs; Name: "{userappdata}\DouYinSparkFlow"; Check: DeleteUserData

[Code]
var
  DeleteUserDataPage: TInputOptionWizardPage;

procedure InitializeWizard;
begin
  DeleteUserDataPage := CreateInputOptionPage(
    wpReady,
    '卸载选项',
    '是否删除用户数据？',
    '程序安装目录将被卸载。用户数据（登录状态、账号、配置、日志）存放在 %APPDATA%\DouYinSparkFlow。' + #13#10 +
    '保留数据可以让你以后重新安装时不用重新扫码登录。',
    True, False);
  DeleteUserDataPage.Add('保留我的数据（推荐）');
  DeleteUserDataPage.Add('同时删除用户数据');
  DeleteUserDataPage.Values[0] := True;
end;

function DeleteUserData: Boolean;
begin
  Result := DeleteUserDataPage.Values[1];
end;
