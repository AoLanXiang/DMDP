DMDP 三种产物打包指南
====================
单一 Python 目录结构，三个外壳：

1) 绿色版（文件夹，exe 入口）
   DMDP-portable\{DMDP.exe, runtime\, app\, 说明文件}
   DMDP.exe 由 launcher.c 编出：
     x86_64-w64-mingw32-gcc -O2 -municode -mwindows -static -o DMDP.exe launcher.c

2) 单文件版（DMDP.exe，自解压）
   sfx.nsi 用 makensis 打包，payload\ = DMDP.exe + runtime\ + app\ ...
   首次运行解到 $EXEDIR\DMDP 并写 VERSION；自更新换掉外层 exe 后，
   VERSION 不匹配会自动重解压，避免继续跑旧代码。

3) 安装版（DMDP-1.1.0-setup.exe）
   installer.nsi 用 makensis 打包，同样 payload 结构，
   装到 %LOCALAPPDATA%\Programs\DMDP，建桌面/开始菜单快捷方式与卸载器。

路径规则（launcher.c）：
  程序目录 = DMDP.exe 所在目录
  游戏目录 = 若 exe 在名为 DMDP 的目录且其上级有 DMDP.exe（单文件版布局）→ 取上级；
             否则就是 exe 所在目录；DMDP_HOME 环境变量可覆盖
