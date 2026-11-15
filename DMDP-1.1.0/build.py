# -*- coding: utf-8 -*-
"""
DMDP 打包脚本（PyInstaller）

用法：
    pip install -r requirements.txt pyinstaller
    python build.py                 # 打正式版（无控制台）→ dist/DMDP.exe
    python build.py --console       # 打带控制台的自检版 → dist/DMDP-selftest.exe
    python build.py --name MyName   # 自定义输出名

说明：
- 单文件（--onefile），双击即用，不需要安装 Python。
- 打包后程序会把 .minecraft 建在自己旁边（和 PCL.exe 同级）。
"""

import os
import sys
import shutil
import subprocess
import hashlib

APP_NAME = "DMDP"
MAIN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dmdp.py")


def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("未检测到 PyInstaller，正在安装…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller>=6.6"])


def build(console: bool, name: str):
    ensure_pyinstaller()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--clean",
        "--noconfirm",
        f"--name={name}",
        "--distpath", "dist",
        "--workpath", "build",
        "--specpath", "build",
        # PyQt6 需要显式收集，否则单文件里容易缺插件
        "--collect-submodules", "PyQt6",
        "--hidden-import", "dmdp_engine",
        # minecraft-launcher-lib 的子模块是动态 import 的，全部收进来
        "--collect-submodules", "minecraft_launcher_lib",
        "--hidden-import", "minecraft_launcher_lib.mod_loader",
        "--hidden-import", "minecraft_launcher_lib.runtime",
        "--hidden-import", "minecraft_launcher_lib.java_installed",
        # 用不到的大件排掉，减小体积
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        "--exclude-module", "PIL",
        "--exclude-module", "pytest",
    ]
    if not console:
        cmd.append("--windowed")
    icon = os.path.join(os.path.dirname(MAIN_SCRIPT), "icon.ico")
    if os.path.isfile(icon):
        cmd += ["--icon", icon]
    cmd.append(MAIN_SCRIPT)

    print("执行:", " ".join(cmd), "\n")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("打包失败，返回码:", result.returncode)
        return None

    exe = os.path.join("dist", name + (".exe" if sys.platform == "win32" else ""))
    if not os.path.isfile(exe):
        print("没找到产物:", exe)
        return None

    size = os.path.getsize(exe) / 1024 / 1024
    print(f"打包成功: {exe} ({size:.1f} MB)")
    return exe


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    console = "--console" in sys.argv or "--selftest" in sys.argv
    name = APP_NAME if not console else APP_NAME + "-selftest"
    for i, arg in enumerate(sys.argv):
        if arg == "--name" and i + 1 < len(sys.argv):
            name = sys.argv[i + 1]

    exe = build(console=console, name=name)
    if not exe:
        sys.exit(1)
    print("SHA256:", sha256(exe))

    # 顺手清理 build 中间目录，保留 dist
    if os.path.isdir("build"):
        shutil.rmtree("build", ignore_errors=True)


if __name__ == "__main__":
    main()
