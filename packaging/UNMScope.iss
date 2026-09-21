; Inno Setup script for UNMScope -- wraps the PyInstaller onedir build
; (dist\UNMScope\, built from packaging\UNMScope.spec) into a normal
; Windows installer: Program Files install, Start Menu entry, optional
; Desktop icon, and an uninstaller in Add/Remove Programs.
;
; Build (from UNMScope_Python\, after packaging\UNMScope.spec has been
; built so dist\UNMScope\ exists):
;   "<Inno Setup install dir>\ISCC.exe" packaging\UNMScope.iss
; Output: installer\UNMScope-Setup-<version>.exe
;
; This installer does not need internet access to install or run --
; everything it needs is embedded in dist\UNMScope\. It does NOT bundle
; the NI-RIO/FlexRIO driver, the Hamamatsu DCAM SDK, or Micro-Manager's
; device adapters: those are vendor drivers tied to physical hardware,
; not something pip/PyInstaller ships, and installing them is unchanged
; from before this installer existed (docs/aotf.md, and the user's own
; `mmcore install` for Micro-Manager).

#define MyAppName "UNMScope"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "UNM Chakraborty Lab"
#define MyAppExeName "UNMScope.exe"
#define MySourceDir "..\dist\UNMScope"

[Setup]
AppId={{B6C1D9B8-3F2E-4C7E-9C2F-6A1E3B7C5D42}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Per-user install, no admin/UAC prompt required: {autopf} (Program Files)
; needs elevation, which this lab's machines cannot always click through
; unattended. {localappdata}\Programs is the standard per-user equivalent
; (the same location winget's own --scope user installs use).
DefaultDirName={localappdata}\Programs\{#MyAppName}
PrivilegesRequired=lowest
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\installer
OutputBaseFilename=UNMScope-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; No embedded license/EULA -- internal lab tool, not distributed publicly.
DisableWelcomePage=no
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent unchecked
