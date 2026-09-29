; CheapTrip for Windows: one setup .exe (Inno Setup 6).
;
; Wraps the PyInstaller build in dist\cheaptrip\ (packaging\cheaptrip.spec):
; CheapTrip.exe (the desktop app) and cheaptrip-cli.exe (the console commands).
; packaging\build_windows.ps1 builds it with:
;   ISCC.exe /DAppVersion=0.6.0 packaging\installer.iss
;
; Installs per user (no admin prompt) to %LOCALAPPDATA%\Programs\CheapTrip.
; Settings, preferences, the database and logs live in %APPDATA%\CheapTrip
; (utils/paths.py); they are written once from the wizard's answers, kept on
; upgrade and kept on uninstall.
;
; Silent installs (/VERYSILENT) take the answers as parameters:
;   /TELEGRAMTOKEN= /TELEGRAMCHATID= /ANTHROPICKEY= /TRAVELPAYOUTSTOKEN= /HOMEAIRPORTS="MXP, LIN, BGY"

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

[Files]
Source: "..\dist\cheaptrip\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
; cheaptrip:// links (a notification's buttons, cheaptrip://deal/<id>) open the app
Root: HKCU; Subkey: "Software\Classes\cheaptrip"; ValueType: string; ValueName: ""; ValueData: "URL:CheapTrip"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\cheaptrip"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\cheaptrip\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKCU; Subkey: "Software\Classes\cheaptrip\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
; Written by the app for its notifications' name and icon: removed with it
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\{#AppUserModelId}"; Flags: uninsdeletekey dontcreatekey

[Dirs]
Name: "{userappdata}\{#AppName}\config"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\CheapTrip"; Filename: "{app}\{#AppExe}"; WorkingDir: "{userappdata}\{#AppName}"; AppUserModelID: "{#AppUserModelId}"; Comment: "Travel deals: searches, alerts and your settings"
Name: "{group}\Check setup"; Filename: "{cmd}"; Parameters: "/k """"{app}\{#CliExe}"" doctor"""; WorkingDir: "{userappdata}\{#AppName}"; Comment: "Check the Telegram bot, keys, preferences and sources"
Name: "{group}\Send a test message"; Filename: "{cmd}"; Parameters: "/k """"{app}\{#CliExe}"" test-alert"""; WorkingDir: "{userappdata}\{#AppName}"; Comment: "Send the current top deals to your Telegram chat"
Name: "{group}\Edit settings"; Filename: "{win}\notepad.exe"; Parameters: """{userappdata}\{#AppName}\.env"""; Comment: "Telegram token, chat ID and API keys"
Name: "{group}\Edit preferences"; Filename: "{win}\notepad.exe"; Parameters: """{userappdata}\{#AppName}\config\user_preferences.yaml"""; Comment: "Home airports, trip lengths, priority destinations"
Name: "{group}\Open data folder"; Filename: "{userappdata}\{#AppName}"
Name: "{group}\Uninstall CheapTrip"; Filename: "{uninstallexe}"
Name: "{userstartup}\CheapTrip"; Filename: "{app}\{#AppExe}"; Parameters: "--minimized"; WorkingDir: "{userappdata}\{#AppName}"; AppUserModelID: "{#AppUserModelId}"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; WorkingDir: "{userappdata}\{#AppName}"; Description: "Open CheapTrip now"; Flags: postinstall nowait skipifsilent
Filename: "{cmd}"; Parameters: "/k """"{app}\{#CliExe}"" doctor"""; WorkingDir: "{userappdata}\{#AppName}"; Description: "Check my setup in a console window"; Flags: postinstall nowait skipifsilent unchecked

[UninstallRun]
; Stop the app (and a console engine) so their files can be removed
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe} /IM {#CliExe}"; Flags: runhidden; RunOnceId: "StopCheapTrip"

[Code]
var
  TelegramPage, KeysPage, AirportsPage: TInputQueryWizardPage;

function DataDir(): String;
begin
  Result := ExpandConstant('{userappdata}\{#AppName}');
end;

function EnvFile(): String;
begin
  Result := DataDir() + '\.env';
end;

function PrefsFile(): String;
begin
  Result := DataDir() + '\config\user_preferences.yaml';
end;

{ One line of text: no surrounding spaces, no line breaks }
function OneLine(S: String): String;
begin
  Result := S;
  StringChangeEx(Result, #13, '', True);
  StringChangeEx(Result, #10, '', True);
  Result := Trim(Result);
end;

{ "mxp lin;bgy" -> "MXP, LIN, BGY" }
function AirportList(S: String): String;
begin
  Result := Uppercase(OneLine(S));
  StringChangeEx(Result, ',', ' ', True);
  StringChangeEx(Result, ';', ' ', True);
  while Pos('  ', Result) > 0 do
    StringChangeEx(Result, '  ', ' ', True);
  Result := Trim(Result);
  StringChangeEx(Result, ' ', ', ', True);
  if Result = '' then
    Result := 'MXP, LIN, BGY';
end;

procedure InitializeWizard();
begin
  TelegramPage := CreateInputQueryPage(wpSelectTasks,
    'Telegram', 'Where should CheapTrip send deal alerts?',
    'Create a bot by messaging @BotFather in Telegram, send your bot a message, then find your chat ID ' +
    '(step by step: {#RepoUrl}/blob/main/docs/telegram_setup.md).' + #13#10#13#10 +
    'You can leave these empty and fill them in later with Start menu > CheapTrip > Edit settings.');
  TelegramPage.Add('Bot token (from @BotFather):', False);
  TelegramPage.Add('Chat ID:', False);
  TelegramPage.Values[0] := ExpandConstant('{param:TELEGRAMTOKEN|}');
  TelegramPage.Values[1] := ExpandConstant('{param:TELEGRAMCHATID|}');

  KeysPage := CreateInputQueryPage(TelegramPage.ID,
    'Optional keys', 'Extra deal sources',
    'Anthropic API key: Claude reads Italian deal posts (PiratinViaggio) and polishes alerts.' + #13#10 +
    'Travelpayouts token: one more "search everywhere" fare source.' + #13#10#13#10 +
    'Leave either empty to skip it.');
  KeysPage.Add('Anthropic API key:', True);
  KeysPage.Add('Travelpayouts token:', True);
  KeysPage.Values[0] := ExpandConstant('{param:ANTHROPICKEY|}');
  KeysPage.Values[1] := ExpandConstant('{param:TRAVELPAYOUTSTOKEN|}');

  AirportsPage := CreateInputQueryPage(KeysPage.ID,
    'Home airports', 'Where do you fly from?',
    'Airport codes separated by commas. Airports of the same city are searched together, ' +
    'so MXP also covers Linate and Bergamo.');
  AirportsPage.Add('Home airports:', False);
  AirportsPage.Values[0] := ExpandConstant('{param:HOMEAIRPORTS|MXP, LIN, BGY}');
end;

{ On an upgrade the settings already exist: don't ask again }
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = TelegramPage.ID) or (PageID = KeysPage.ID) then
    Result := FileExists(EnvFile());
  if PageID = AirportsPage.ID then
    Result := FileExists(PrefsFile());
end;

procedure WriteEnv();
var
  Lines: TArrayOfString;
begin
  SetArrayLength(Lines, 8);
  Lines[0] := '# CheapTrip settings, written by the installer.';
  Lines[1] := '# Edit with Start menu > CheapTrip > Edit settings; every setting is described in';
  Lines[2] := '# {#RepoUrl}/blob/main/docs/configuration.md';
  Lines[3] := 'TELEGRAM_BOT_TOKEN=' + OneLine(TelegramPage.Values[0]);
  Lines[4] := 'TELEGRAM_CHAT_ID=' + OneLine(TelegramPage.Values[1]);
  Lines[5] := 'ANTHROPIC_API_KEY=' + OneLine(KeysPage.Values[0]);
  Lines[6] := 'TRAVELPAYOUTS_TOKEN=' + OneLine(KeysPage.Values[1]);
  Lines[7] := '';
  { UTF-8 (the app also reads a BOM, see config.py) }
  if not SaveStringsToUTF8File(EnvFile(), Lines, False) then
    Log('Could not write ' + EnvFile());
end;

{ The documented example, with the home airports from the wizard }
procedure WritePreferences();
var
  Example: AnsiString;
  Text: String;
begin
  if not LoadStringFromFile(ExpandConstant('{app}\_internal\config\user_preferences.yaml.example'), Example) then
  begin
    Log('Preferences example not found: the app will create its own preferences on first start');
    exit;
  end;
  Text := UTF8Decode(Example);
  StringChangeEx(Text, #13#10, #10, True);
  if StringChangeEx(Text, 'home_airports:' + #10 + '  - MXP' + #10 + '  - LIN' + #10 + '  - BGY' + #10,
                    'home_airports: [' + AirportList(AirportsPage.Values[0]) + ']' + #10, True) = 0 then
    Log('home_airports block not found in the example: kept the example as is');
  if not SaveStringToFile(PrefsFile(), Utf8Encode(Text), False) then
    Log('Could not write ' + PrefsFile());
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    ForceDirectories(DataDir() + '\config');
    if not FileExists(EnvFile()) then
      WriteEnv();
    if not FileExists(PrefsFile()) then
      WritePreferences();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent() then
    MsgBox('CheapTrip is removed. Your settings, preferences and price history are still in ' +
           DataDir() + '; delete that folder to remove them too.', mbInformation, MB_OK);
end;
