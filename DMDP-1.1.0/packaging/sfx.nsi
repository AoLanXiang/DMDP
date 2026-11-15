Unicode true
Name "DMDP 1.1.0"
OutFile "DMDP.exe"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SilentInstall silent
AutoCloseWindow true
CRCCheck on

Section "Main"
  IfFileExists "$EXEDIR\DMDP\runtime\pythonw.exe" 0 extract
    Goto launch
  extract:
    SetOutPath "$EXEDIR\DMDP"
    File /r "payload\*"
  launch:
    SetOutPath "$EXEDIR"
    Exec '"$EXEDIR\DMDP\DMDP.exe"'
SectionEnd
