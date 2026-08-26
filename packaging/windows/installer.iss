; ============================================================
; DouYin SparkFlow desktop installer (Inno Setup 6)
; The build script passes /DAppVersion and /DAppSourceDir.
; NOTE: keep this file ASCII-only (Chinese text breaks ISCC's
; ANSI reader; the ChineseSimplified language strings are
; provided by the compiler's own .isl file).
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
AppId={{8B3E6C2A-5D91-4A7F-9E4C-4E9F1A2B3C4D}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\DouYinSparkFlow
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; per-user install: no admin rights required
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
; runtime data lives in %APPDATA%\DouYinSparkFlow and is kept on uninstall by default
CloseApplications=yes
RestartApplications=no

[Languages]
; ChineseSimplified.isl ships with this repo (packaging/windows/) so the build
; does not depend on the compiler machine having the language pack installed.
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl,ChineseSimplified.isl"
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
; delete user data on uninstall only when the user explicitly opts in
Type: filesandordirs; Name: "{userappdata}\DouYinSparkFlow"; Check: DeleteUserData

[Code]
var
  DeleteUserDataPage: TInputOptionWizardPage;

procedure InitializeWizard;
begin
  DeleteUserDataPage := CreateInputOptionPage(
    wpReady,
    'Uninstall options',
    'Delete user data?',
    'The program files will be removed. Your user data (login state, accounts, config, logs) is stored in %APPDATA%\DouYinSparkFlow.' + #13#10 +
    'Keeping it lets you reinstall without logging in again.',
    True, False);
  DeleteUserDataPage.Add('Keep my data (recommended)');
  DeleteUserDataPage.Add('Also delete user data');
  DeleteUserDataPage.Values[0] := True;
end;

function DeleteUserData: Boolean;
begin
  Result := DeleteUserDataPage.Values[1];
end;
