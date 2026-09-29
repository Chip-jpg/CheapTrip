; CheapTrip for Windows: one setup .exe (Inno Setup 6).
;
; Wraps the PyInstaller build in dist\cheaptrip\ (packaging\cheaptrip.spec):
; CheapTrip.exe (the desktop app) and cheaptrip-cli.exe (the console commands).
; packaging\build_windows.ps1 builds it with:
;   ISCC.exe /DAppVersion=0.6.0 packaging\installer.iss
;
; Installs per user (no admin prompt) to %LOCALAPPDATA%\Programs\CheapTrip.
; Settings, preferences, the database and logs live in %APPDATA%\CheapTrip
; (utils/paths.py): the app creates them at its first start and asks for the
; home airports, Telegram and keys in its own setup wizard (from 0.8; the
; installer used to ask). They are kept on upgrade and on uninstall.
;
; "Start at sign-in" is the Run value HKCU\...\Run\CheapTrip, which the
; app's Settings switch changes too (desktop/windows.py).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "CheapTrip"
#define AppExe "CheapTrip.exe"
#define CliExe "cheaptrip-cli.exe"
; The app's AppUserModelID (notifier/desktop.py APP_ID): groups its window, taskbar button and notifications
#define AppUserModelId "CheapTrip"
#define RepoUrl "https://github.com/Chip-jpg/CheapTrip"

[Setup]
AppId={{8E0C6F4B-5D2A-4C1B-9E37-CA7E1F0B6D21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=CheapTrip-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=icons\cheaptrip.ico
UninstallDisplayName={#AppName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Start CheapTrip when I sign in to Windows (in the tray)"; GroupDescription: "Startup:"

[InstallDelete]
; Up to 0.6 the console program was cheaptrip.exe: remove it so CheapTrip.exe keeps its name's case
Type: files; Name: "{app}\cheaptrip.exe"
; Up to 0.6 "start at sign-in" was a Startup folder shortcut; it's the Run value below now
Type: files; Name: "{userstartup}\CheapTrip.lnk"

[Files]
Source: "..\dist\cheaptrip\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
; cheaptrip:// links (a notification's buttons, cheaptrip://deal/<id>) open the app
Root: HKCU; Subkey: "Software\Classes\cheaptrip"; ValueType: string; ValueName: ""; ValueData: "URL:CheapTrip"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\cheaptrip"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\cheaptrip\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKCU; Subkey: "Software\Classes\cheaptrip\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
; Start at sign-in, in the tray (the app's Settings → App switch reads and changes the same value)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "CheapTrip"; ValueData: """{app}\{#AppExe}"" --minimized"; Tasks: autostart; Flags: uninsdeletevalue
; Written by the app for its notifications' name and icon: removed with it
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\{#AppUserModelId}"; Flags: uninsdeletekey dontcreatekey

[Dirs]
Name: "{userappdata}\{#AppName}\config"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\CheapTrip"; Filename: "{app}\{#AppExe}"; WorkingDir: "{userappdata}\{#AppName}"; AppUserModelID: "{#AppUserModelId}"; Comment: "Travel deals: searches, alerts and your settings"
; Settings, the setup checks and test messages are in the app; this is for when it won't start
Name: "{group}\Check setup (console)"; Filename: "{cmd}"; Parameters: "/k """"{app}\{#CliExe}"" doctor"""; WorkingDir: "{userappdata}\{#AppName}"; Comment: "Check the Telegram bot, keys, preferences and sources"
Name: "{group}\Open data folder"; Filename: "{userappdata}\{#AppName}"
Name: "{group}\Uninstall CheapTrip"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#AppExe}"; WorkingDir: "{userappdata}\{#AppName}"; Description: "Open CheapTrip and set it up"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Stop the app (and a console engine) so their files can be removed
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe} /IM {#CliExe}"; Flags: runhidden; RunOnceId: "StopCheapTrip"

[Code]
function DataDir(): String;
begin
  Result := ExpandConstant('{userappdata}\{#AppName}');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent() then
    MsgBox('CheapTrip is removed. Your settings, preferences and price history are still in ' +
           DataDir() + '; delete that folder to remove them too.', mbInformation, MB_OK);
end;
