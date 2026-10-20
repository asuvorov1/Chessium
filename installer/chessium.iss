; Установщик Chessium (Inno Setup 6). Собирается из build.ps1.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6F3C2B7E-9A41-4C2D-8E5B-1D7A3C9F0B21}
AppName=Chessium
AppVersion={#AppVersion}
AppVerName=Chessium {#AppVersion}
AppPublisher=Chessium
AppPublisherURL=https://github.com/asuvorov1/Chessium
AppSupportURL=https://github.com/asuvorov1/Chessium/issues
DefaultDirName={autopf}\Chessium
DefaultGroupName=Chessium
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Chessium-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\Chessium.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
AppMutex=ChessiumAppMutex
LicenseFile=..\LICENSE

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\Chessium\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Chessium"; Filename: "{app}\Chessium.exe"
Name: "{group}\{cm:UninstallProgram,Chessium}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Chessium"; Filename: "{app}\Chessium.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Chessium.exe"; Description: "{cm:LaunchProgram,Chessium}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\Chessium"
