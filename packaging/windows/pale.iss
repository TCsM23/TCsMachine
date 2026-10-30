; PALE Windows installer (Inno Setup 6).
; Build:  iscc /DAppVersion=0.2.0 packaging\windows\pale.iss   (after PyInstaller has produced dist\PALE)
;
; Per-user install (no admin prompt), desktop shortcut on by default, and a silent update path:
; the app runs  PALE-Setup-x.y.z.exe /VERYSILENT /UPDATE=1  and quits; this installer replaces the
; files and relaunches PALE.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E2B9C1D-3F4A-4E8B-9A5C-7D1E2F3A4B5C}
AppName=PALE
AppVersion={#AppVersion}
AppVerName=PALE {#AppVersion}
AppPublisher=TCsM
AppPublisherURL=https://github.com/TCsM23/TCsMachine
AppSupportURL=https://github.com/TCsM23/TCsMachine/issues
AppUpdatesURL=https://github.com/TCsM23/TCsMachine/releases
DefaultDirName={localappdata}\Programs\PALE
DefaultGroupName=PALE
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=PALE-Setup-{#AppVersion}
SetupIconFile=..\..\pale\desktop\assets\pale.ico
UninstallDisplayIcon={app}\PALE.exe
UninstallDisplayName=PALE
WizardStyle=modern
WizardImageFile=wizard-side.bmp,wizard-side@2x.bmp
WizardSmallImageFile=wizard-small.bmp,wizard-small@2x.bmp
Compression=lzma2/max
SolidCompression=yes
CloseApplications=force
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion={#AppVersion}
VersionInfoProductName=PALE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel1=Deploy PALE
WelcomeLabel2=This will install PALE {#AppVersion}, an operator support harness, on your computer.%n%nThe unit works only inside the sector folder you choose on first launch, and asks before it changes anything.%n%nClose other applications before continuing.
FinishedHeadingLabel=PALE is deployed
FinishedLabel=The city is quiet. Launch PALE to establish the link.

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; start every update from a clean program folder so stale modules never linger
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\..\dist\PALE\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\PALE"; Filename: "{app}\PALE.exe"; Comment: "PALE operator support harness"
Name: "{autodesktop}\PALE"; Filename: "{app}\PALE.exe"; Tasks: desktopicon; Comment: "PALE operator support harness"

[Run]
Filename: "{app}\PALE.exe"; Description: "Launch PALE"; Flags: nowait postinstall skipifsilent
Filename: "{app}\PALE.exe"; Flags: nowait; Check: IsUpdate

[Code]
function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;
