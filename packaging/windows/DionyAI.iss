#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{1F0F9DA7-5A89-4B7F-A06C-2F0DF4B65C83}
AppName=Diony AI
AppVersion={#AppVersion}
AppPublisher=Diony AI
AppPublisherURL=https://diony.ai/
UninstallDisplayName=Diony AI
DefaultDirName={autopf}\Diony AI
DefaultGroupName=Diony AI
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputDir={#OutputDir}
OutputBaseFilename=DionyAI-Setup-{#AppVersion}-windows-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
CloseApplications=force
RestartApplications=no
UninstallDisplayIcon={app}\Diony AI.exe

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Diony AI"; Filename: "{app}\Diony AI.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Diony AI"; Filename: "{app}\Diony AI.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\Diony AI.exe"; Description: "Open Diony AI"; Flags: nowait postinstall skipifsilent
