单文件绿色版（DMDP.exe）的打包材料
==================================

launcher.c   启动器源码：算路径、校验 runtime/app、设 DMDP_HOME、启动 pythonw
             编译（Linux 交叉编译）：
               x86_64-w64-mingw32-gcc -O2 -municode -mwindows -static -o DMDP.exe launcher.c

sfx.nsi      NSIS 自解压脚本：把 payload 压进一个 exe；首次运行解到 $EXEDIR\DMDP，
             之后再运行跳过解压直接启动
             payload 结构要求：
               payload\DMDP.exe
               payload\runtime\
               payload\app\
               payload\README.md、LICENSE、使用说明.txt、两个排错 bat
             编译：makensis sfx.nsi   → 产出 DMDP.exe（约 32 MB）

运行后目录：
  .minecraft\  PCL\  Plain Craft Launcher 2.exe  DMDP.exe  DMDP\（runtime + app）

路径规则（launcher.c 与 app\dmdp.py 里各一份，彼此呼应）：
  - 程序文件目录 = DMDP.exe 自己所在目录
  - 游戏目录 = 若 exe 位于名为 DMDP 的目录里，取该目录的上级；否则就是 exe 目录
  - 环境变量 DMDP_HOME 可覆盖游戏目录
