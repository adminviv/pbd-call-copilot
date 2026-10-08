; Windows installer for PBD Call Copilot (built by .github/workflows/build.yml).
; Installs for the current user only (no admin prompt). Files installed this way
; don't carry the "downloaded from the internet" mark that broke the zip.
#ifndef AppVersion
  #define AppVersion "1.0"
#endif

[Setup]
AppId={{E9EA93A9-EFEC-4F54-ABEC-6E65A390730B}
AppName=PBD Call Copilot
AppVersion={#AppVersion}
AppPublisher=PBD Project
AppPublisherURL=https://www.pbdproject.org
DefaultDirName={localappdata}\Programs\PBD Call Copilot
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=PBD-Call-Copilot-Setup
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
UninstallDisplayIcon={app}\PBD Call Copilot.exe
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Files]
Source: "..\dist\PBD Call Copilot\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\PBD Call Copilot"; Filename: "{app}\PBD Call Copilot.exe"
Name: "{autodesktop}\PBD Call Copilot"; Filename: "{app}\PBD Call Copilot.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PBD Call Copilot.exe"; Description: "Open PBD Call Copilot now"; Flags: nowait postinstall skipifsilent
