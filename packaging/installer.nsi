Unicode true
!include "MUI2.nsh"
Name "DMDP 1.1.1"
OutFile "DMDP-1.1.1-setup.exe"
InstallDir "$LOCALAPPDATA\Programs\DMDP"
InstallDirRegKey HKCU "Software\DMDP" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "payload\*"
  WriteRegStr HKCU "Software\DMDP" "InstallDir" "$INSTDIR"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  CreateShortCut "$DESKTOP\DMDP.lnk" "$INSTDIR\DMDP.exe"
  CreateDirectory "$SMPROGRAMS\DMDP"
  CreateShortCut "$SMPROGRAMS\DMDP\DMDP.lnk" "$INSTDIR\DMDP.exe"
  CreateShortCut "$SMPROGRAMS\DMDP\卸载 DMDP.lnk" "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DMDP" "DisplayName" "DMDP"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DMDP" "DisplayVersion" "1.1.1"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DMDP" "Publisher" "AoLanXiang"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DMDP" "DisplayIcon" "$INSTDIR\DMDP.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DMDP" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DMDP" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DMDP" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\DMDP.lnk"
  Delete "$SMPROGRAMS\DMDP\DMDP.lnk"
  Delete "$SMPROGRAMS\DMDP\卸载 DMDP.lnk"
  RMDir "$SMPROGRAMS\DMDP"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DMDP"
  DeleteRegKey HKCU "Software\DMDP"
  RMDir /r "$INSTDIR"
SectionEnd
