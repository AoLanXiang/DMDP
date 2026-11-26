Unicode true
Name "DMDP 1.1.1"
OutFile "DMDP.exe.new"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SilentInstall silent
AutoCloseWindow true
CRCCheck on

Section "Main"
  IfFileExists "$EXEDIR\DMDP\VERSION" 0 extract
    FileOpen $0 "$EXEDIR\DMDP\VERSION" r
    FileRead $0 $1
    FileClose $0
    StrCmp $1 "1.1.1" 0 reextract
    Goto launch
  reextract:
    RMDir /r "$EXEDIR\DMDP"
  extract:
    SetOutPath "$EXEDIR\DMDP"
    File /r "payload\*"
    FileOpen $0 "$EXEDIR\DMDP\VERSION" w
    FileWrite $0 "1.1.1"
    FileClose $0
  launch:
    SetOutPath "$EXEDIR"
    Exec '"$EXEDIR\DMDP\DMDP.exe"'
SectionEnd
