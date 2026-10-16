; Inno Setup 6 script for LEVEL7 MTM (x64). Build after PyInstaller:
;   iscc /DAppVersion=0.1.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{3F6C2A91-8D4E-4B7A-9C51-2E7D8A6B4F12}
AppName=LEVEL7 MTM
AppVersion={#AppVersion}
AppPublisher=LEVEL7
DefaultDirName={autopf}\LEVEL7 MTM
DefaultGroupName=LEVEL7 MTM
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\installer
OutputBaseFilename=LEVEL7MTM-{#AppVersion}-Setup
SetupIconFile=..\level7mtm\resources\level7mtm.ico
UninstallDisplayIcon={app}\LEVEL7MTM.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\LEVEL7MTM\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\LEVEL7 MTM"; Filename: "{app}\LEVEL7MTM.exe"
Name: "{group}\{cm:UninstallProgram,LEVEL7 MTM}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LEVEL7 MTM"; Filename: "{app}\LEVEL7MTM.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LEVEL7MTM.exe"; Description: "{cm:LaunchProgram,LEVEL7 MTM}"; Flags: nowait postinstall skipifsilent
; online update (started by the app with /SILENT /LAUNCH=1): start LEVEL7 MTM again as the normal user
Filename: "{app}\LEVEL7MTM.exe"; Flags: nowait runasoriginaluser; Check: LaunchAfterUpdate

[Code]
function LaunchAfterUpdate: Boolean;
begin
  Result := ExpandConstant('{param:LAUNCH|0}') = '1';
end;
