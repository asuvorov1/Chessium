; Установщик Chessium (Inno Setup 6.6+). Собирается из build.ps1.
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
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Установщик Chessium

; Куда ставить: по умолчанию для текущего пользователя, без прав администратора
DefaultDirName={autopf}\Chessium
DefaultGroupName=Chessium
PrivilegesRequired=lowest
ShowLanguageDialog=auto
UsePreviousAppDir=yes

; Страницы мастера: приветствие -> папка -> ярлыки -> готово -> установка -> финиш
DisableWelcomePage=no
DisableDirPage=no
DisableProgramGroupPage=yes
DisableReadyPage=no
DisableFinishedPage=no
AllowNoIcons=yes

; Оформление
WizardStyle=modern dark
WizardSizePercent=110
WizardImageFile=art\wizard-100.bmp,art\wizard-150.bmp,art\wizard-200.bmp,art\wizard-250.bmp
WizardSmallImageFile=art\small-55.bmp,art\small-83.bmp,art\small-110.bmp,art\small-138.bmp
WizardImageStretch=yes
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\Chessium.exe
UninstallDisplayName=Chessium

OutputDir=..\dist
OutputBaseFilename=Chessium-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
AppMutex=ChessiumAppMutex
CloseApplications=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
russian.WelcomeLabel1=Добро пожаловать в Chessium
russian.WelcomeLabel2=Chessium находит шахматную доску на экране и анализирует партию прямо поверх сайта: оценка позиции, лучшие ходы, оценка каждого хода и точность игроков.%n%nБудет установлена версия {#AppVersion}.
russian.FinishedHeadingLabel=Chessium установлен
russian.FinishedLabel=Откройте партию на chess.com или lichess — Chessium сам найдёт доску и начнёт анализ.
russian.FinishedLabelNoIcons=Откройте партию на chess.com или lichess — Chessium сам найдёт доску и начнёт анализ.
english.WelcomeLabel1=Welcome to Chessium
english.WelcomeLabel2=Chessium finds the chessboard on your screen and analyses the game right on top of the website.%n%nThis will install version {#AppVersion}.

[CustomMessages]
russian.DesktopIcon=Создать ярлык на рабочем столе
english.DesktopIcon=Create a desktop shortcut
russian.ShortcutsGroup=Ярлыки:
english.ShortcutsGroup=Shortcuts:
russian.LaunchNow=Запустить Chessium
english.LaunchNow=Launch Chessium

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:ShortcutsGroup}"

[Files]
Source: "..\dist\Chessium\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Chessium"; Filename: "{app}\Chessium.exe"
Name: "{group}\{cm:UninstallProgram,Chessium}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Chessium"; Filename: "{app}\Chessium.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Chessium.exe"; Description: "{cm:LaunchNow}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\Chessium"
