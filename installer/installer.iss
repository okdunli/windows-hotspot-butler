; Inno Setup script for Windows Hotspot Butler.
; Compiled twice by the release workflow (once per architecture):
;   ISCC.exe /DMyAppArch=amd64 /DMyAppVersion=1.2.0 /DMyAppSource=dist\...\WiFiHotspotManager.exe installer.iss
; The output is WindowsHotspotButler-<version>-windows-<arch>-setup.exe.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef MyAppArch
  #define MyAppArch "x64"
#endif

; 文件名用的是 amd64/arm64（行业惯例），Inno 的架构标识符是 x64/arm64
#ifndef MyInnoArch
  #if MyAppArch == "amd64"
    #define MyInnoArch "x64"
  #else
    #define MyInnoArch MyAppArch
  #endif
#endif
#ifndef MyAppSource
  #define MyAppSource "..\python-app\dist\WiFiHotspotManager.exe"
#endif

[Setup]
AppId={{8E3C1B36-4F2A-4D7B-9C0E-5A6B7C8D9E01}
AppName=Windows Hotspot Butler
AppVersion={#MyAppVersion}
AppPublisher=Windows Hotspot Butler
DefaultDirName={autopf}\Windows Hotspot Butler
DefaultGroupName=Windows Hotspot Butler
DisableProgramGroupPage=yes
OutputDir=..\dist-installer
OutputBaseFilename=WindowsHotspotButler-{#MyAppVersion}-windows-{#MyAppArch}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed={#MyInnoArch}
ArchitecturesInstallIn64BitMode={#MyInnoArch}
UninstallDisplayIcon={app}\WiFiHotspotManager.exe
PrivilegesRequired=admin

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "开机自动启动 (Start with Windows)"; GroupDescription: "Startup"; Flags: unchecked

[Files]
Source: "{#MyAppSource}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Windows Hotspot Butler"; Filename: "{app}\WiFiHotspotManager.exe"
Name: "{group}\Uninstall Windows Hotspot Butler"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Windows Hotspot Butler"; Filename: "{app}\WiFiHotspotManager.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "WiFiHotspotManager"; ValueData: """{app}\WiFiHotspotManager.exe"""; \
  Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\WiFiHotspotManager.exe"; Description: "{cm:LaunchProgram,Windows Hotspot Butler}"; \
  Flags: nowait postinstall skipifsilent
