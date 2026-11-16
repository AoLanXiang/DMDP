#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DMDP - Duck Minecraft Downloader for Plain Craft Launcher
使用 PyQt6 + minecraft-launcher-lib 构建的 Minecraft 原版下载器
仿 PCL2 风格，支持 BMCLAPI 镜像加速、多线程下载
"""

import sys
import os
import re
import json
import time
import hashlib
import threading
import traceback
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
from collections import deque

import requests

# PyQt6
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QStackedWidget,
    QScrollArea, QSpinBox, QCheckBox, QLineEdit, QFileDialog,
    QComboBox, QFrame, QSizePolicy, QMenu, QSystemTrayIcon, QMessageBox,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QGraphicsDropShadowEffect,
    QStyle, QStyleFactory
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QSize, QRect, QPoint, QPropertyAnimation,
    QEasingCurve, QObject, QEvent
)
from PyQt6.QtGui import (
    QColor, QPainter, QFont, QIcon, QPixmap, QPen, QBrush,
    QLinearGradient, QPalette, QFontMetrics, QMouseEvent, QPaintEvent,
    QRegion
)

# 本地下载引擎：BMCLAPI 镜像 / 多文件并发 / 单文件分片 / 字节级进度 / Mod 加载器
import dmdp_engine as engine

# ═══════════════════════════════════════════════════════════════
#  常量
# ═══════════════════════════════════════════════════════════════

APP_NAME = "DMDP"
APP_FULL_NAME = "Duck Minecraft Downloader for Plain Craft Launcher"
APP_VERSION = engine.APP_VERSION

# 主题色
COLOR_BLUE = "#4A90D9"
COLOR_BLUE_DARK = "#3A7BC8"
COLOR_BLUE_LIGHT = "#6BA5E0"
COLOR_BLUE_BG = "#E8F1FB"
COLOR_ORANGE = "#F5A623"
COLOR_ORANGE_DARK = "#E89312"
COLOR_BG_WHITE = "#FFFFFF"
COLOR_BG_GRAY = "#F5F6F8"
COLOR_BG_GRAY_DARK = "#EAEDF2"
COLOR_TEXT_PRIMARY = "#2C3E50"
COLOR_TEXT_SECONDARY = "#7F8C9A"
COLOR_TEXT_TERTIARY = "#B0B8C5"
COLOR_BORDER = "#E0E4EA"
COLOR_GREEN = "#27AE60"
COLOR_RED = "#E74C3C"

# 下载源（与引擎保持一致）
SOURCE_BMCLAPI = engine.SOURCE_BMCLAPI
SOURCE_MOJANG = engine.SOURCE_MOJANG

# 设置文件
SETTINGS_FILE = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "DMDP", "settings.json"
) if sys.platform == "win32" else os.path.join(
    os.path.expanduser("~"), ".config", "dmdp", "settings.json"
)


# ═══════════════════════════════════════════════════════════════
#  程序目录 / 默认 .minecraft
# ═══════════════════════════════════════════════════════════════

def app_dir() -> str:
    """程序所在目录。打包后就是 exe 所在目录 —— 通常也正是 PCL2 所在目录。"""
    env_home = os.environ.get("DMDP_HOME")
    if env_home and os.path.isdir(env_home):
        return env_home
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    # 绿色包布局：DMDP.exe + runtime\ + app\dmdp.py，游戏目录要放在 exe 旁边
    if os.path.isdir(os.path.join(parent, "runtime")) and \
            os.path.isfile(os.path.join(parent, "DMDP.exe")):
        return parent
    return here


def default_mc_directory() -> str:
    """默认 .minecraft 路径：和程序（PCL.exe）放在同一个文件夹里。"""
    return os.path.join(app_dir(), ".minecraft")


def system_mc_directory() -> str:
    """系统用户目录下的 .minecraft，仅作为程序目录不可写时的兜底。"""
    try:
        return str(engine.mll_utils.get_minecraft_directory())
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".minecraft")


def _is_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".dmdp-write-test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


# ── PCL2 目录探测 ─────────────────────────────────────────────
# 安装版默认装在 %LOCALAPPDATA%\Programs\DMDP，程序自己不知道 PCL2 装在哪。
# 这里扫描常见位置里的 PCL2 主程序（正式文件名为「Plain Craft Launcher 2.exe」），
# 找到后就能把 .minecraft 指到它旁边。

PCL_EXE_NAMES = {
    "plain craft launcher 2.exe": 0,   # PCL2 正式文件名，优先
    "pcl2.exe": 1,
    "pcl.exe": 2,
}


def _pcl_exe_rank(filename: str):
    """匹配 PCL2 主程序文件名，返回优先级（越小越优先）；不匹配返回 None。"""
    name = filename.lower()
    if name in PCL_EXE_NAMES:
        return PCL_EXE_NAMES[name]
    # 允许「Plain Craft Launcher 2 (1).exe」这类重名副本
    if name.startswith("plain craft launcher 2") and name.endswith(".exe"):
        return 0
    return None


def pcl_exe_in_dir(path: str) -> str:
    """返回目录里匹配到的 PCL2 主程序文件名（没有则返回空串）。"""
    best = None
    try:
        for entry in os.scandir(path):
            try:
                if not entry.is_file():
                    continue
            except OSError:
                continue
            rank = _pcl_exe_rank(entry.name)
            if rank is not None and (best is None or rank < best[0]):
                best = (rank, entry.name)
    except OSError:
        return ""
    return best[1] if best else ""


_SKIP_DIR_NAMES = {
    "windows", "$recycle.bin", "system volume information", "programdata",
    "appdata", "node_modules", ".git", "__pycache__", "temp", "tmp",
    "program files", "program files (x86)", "perflogs", "recovery",
    "winsxs", "driverstore", "installer", "assembly", "servicing",
    "intel", "amd", "nvidia",
}


def _drive_roots() -> list:
    """Windows 下枚举所有存在的盘符根目录。"""
    if os.name != "nt":
        return []
    roots = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if os.path.isdir(root):
            roots.append(root)
    return roots


def _pcl_search_roots() -> list:
    """PCL2 最可能出现的几个起点：程序目录、用户目录及其常用子目录、各盘根目录。"""
    roots = [app_dir()]
    home = os.path.expanduser("~")
    for sub in ("Desktop", "Downloads", "Documents", "桌面", "下载", "文档"):
        p = os.path.join(home, sub)
        if os.path.isdir(p):
            roots.append(p)
    roots.append(home)
    roots.extend(_drive_roots())

    seen, out = set(), []
    for r in roots:
        try:
            key = os.path.normcase(os.path.abspath(r))
        except Exception:
            continue
        if key not in seen and os.path.isdir(r):
            seen.add(key)
            out.append(r)
    return out


def find_pcl2_dirs(max_depth: int = 2, time_budget: float = 8.0) -> list:
    """广度优先扫描，返回包含 PCL2 主程序的目录列表。

    max_depth 控制向下几层；time_budget 是总时间上限（秒），到点就返回已找到的结果，
    避免在文件很多的盘上卡住界面。结果按「Plain Craft Launcher 2.exe」优先排序。
    """
    deadline = time.monotonic() + max(1.0, time_budget)
    found = {}          # 目录 -> (优先级, 命中文件名)
    seen = set()
    queue = deque((r, 0) for r in _pcl_search_roots())

    while queue:
        if time.monotonic() > deadline:
            break
        path, depth = queue.popleft()
        try:
            key = os.path.normcase(os.path.abspath(path))
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for entry in entries:
            try:
                name = entry.name.lower()
                if entry.is_file():
                    rank = _pcl_exe_rank(entry.name)
                    if rank is not None:
                        old = found.get(path)
                        if old is None or rank < old[0]:
                            found[path] = (rank, entry.name)
                elif entry.is_dir(follow_symlinks=False):
                    if name in _SKIP_DIR_NAMES or name.startswith("$"):
                        continue
                    if depth < max_depth:
                        queue.append((entry.path, depth + 1))
            except OSError:
                continue

    # 去重：junction / 软链接指向同一目录时只保留最短的那个写法
    uniq = {}
    for d, info in found.items():
        try:
            key = os.path.normcase(os.path.realpath(d))
        except Exception:
            key = os.path.normcase(d)
        old = uniq.get(key)
        if old is None or (info[0], len(d)) < (old[0], len(old[1])):
            uniq[key] = (info[0], d)
    return [d for _, d in sorted(uniq.values(), key=lambda x: (x[0], len(x[1])))]


def resolve_mc_directory(settings: "DMDPSettings") -> str:
    """把设置里的「游戏目录」解析成真实路径。

    - 空值 = 跟随程序目录（默认，与 PCL.exe 同目录）
    - 旧版本遗留的默认值（%APPDATA%\\.minecraft）同样视为跟随程序目录，自动迁移
    - 程序目录不可写（例如装在 Program Files）时回退到用户目录
    """
    raw = (settings.mc_directory or "").strip()
    legacy = system_mc_directory()
    if not raw or (legacy and os.path.normcase(os.path.abspath(raw)) ==
                   os.path.normcase(os.path.abspath(legacy))):
        target = default_mc_directory()
        if _is_writable(target):
            return target
        fallback = system_mc_directory()
        _is_writable(fallback)
        return fallback
    return raw


def fmt_size(num: float) -> str:
    """把字节数格式化成 MB / GB"""
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "0 MB"
    if num >= 1024 ** 3:
        return f"{num / 1024 ** 3:.2f} GB"
    return f"{num / 1024 ** 2:.1f} MB"


def build_config(settings: "DMDPSettings") -> engine.DownloadConfig:
    return engine.DownloadConfig(
        source=settings.download_source,
        max_workers=settings.max_workers,
        chunk_size=settings.chunk_size,
        chunk_threshold_mb=settings.chunk_threshold_mb,
        retries=settings.retries,
        speed_limit=settings.speed_limit,
        verify_sha1=settings.verify_sha1,
        java_path=settings.java_path,
        auto_install_java=settings.auto_install_java,
    )


# ═══════════════════════════════════════════════════════════════
#  设置管理
# ═══════════════════════════════════════════════════════════════

@dataclass
class DMDPSettings:
    download_source: str = SOURCE_BMCLAPI
    mc_directory: str = ""           # 空 = 跟随程序目录（与 PCL.exe 同目录）
    max_workers: int = 8             # 同时下载的文件数 = 总连接数
    chunk_size: int = 4              # 单个大文件切几段
    chunk_threshold_mb: int = 8      # 大文件阈值
    retries: int = 3
    speed_limit: int = 0             # 0 = 不限制
    verify_sha1: bool = True
    java_path: str = ""              # 空 = 自动检测
    auto_install_java: bool = True   # 找不到 Java 时下载 Mojang 运行时（仅装加载器需要）
    auto_check_update: bool = True
    update_channel: str = "stable"  # stable / beta
    window_width: int = 1100
    window_height: int = 700

    def to_dict(self) -> dict:
        return {
            "download_source": self.download_source,
            "mc_directory": self.mc_directory,
            "max_workers": self.max_workers,
            "chunk_size": self.chunk_size,
            "chunk_threshold_mb": self.chunk_threshold_mb,
            "retries": self.retries,
            "speed_limit": self.speed_limit,
            "verify_sha1": self.verify_sha1,
            "java_path": self.java_path,
            "auto_install_java": self.auto_install_java,
            "auto_check_update": self.auto_check_update,
            "update_channel": self.update_channel,
            "window_width": self.window_width,
            "window_height": self.window_height,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DMDPSettings":
        s = cls()
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s

    def save(self):
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls) -> "DMDPSettings":
        if os.path.isfile(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    return cls.from_dict(json.load(f))
            except Exception:
                pass
        return cls()


# ═══════════════════════════════════════════════════════════════
#  下载 Worker（QThread）
# ═══════════════════════════════════════════════════════════════

class PCLScanWorker(QThread):
    """后台扫描 PCL2 目录，避免卡住界面。"""

    found = pyqtSignal(list)

    def __init__(self, deep: bool = True, parent=None):
        super().__init__(parent)
        self._deep = deep

    def run(self):
        try:
            dirs = find_pcl2_dirs(
                max_depth=3 if self._deep else 1,
                time_budget=15.0 if self._deep else 5.0,
            )
        except Exception:
            dirs = []
        self.found.emit(dirs)


class DownloadWorker(QThread):
    """后台执行：原版安装（+ 可选 Mod 加载器）"""

    status_changed = pyqtSignal(str)
    progress_changed = pyqtSignal(int, int)      # 已下载字节 / 总字节
    speed_updated = pyqtSignal(float)            # MB/s
    detail_changed = pyqtSignal(str, int, int)   # 当前文件, 已完成文件数, 文件总数
    download_finished = pyqtSignal(str, bool)    # 任务版本, 是否成功
    error_occurred = pyqtSignal(str, str)
    log_message = pyqtSignal(str)

    def __init__(self, version_id, mc_dir, settings,
                 loader_id="", loader_version="", parent=None):
        super().__init__(parent)
        self.version_id = version_id
        self.mc_dir = mc_dir
        self.settings = settings
        self.loader_id = loader_id
        self.loader_version = loader_version
        self.installed_version = version_id
        self._manager = None
        self._last_speed = 0.0
        self._current_file = ""
        self._files_done = 0
        self._files_total = 0

    # 兼容旧代码里读 worker._current / worker._total 的地方
    @property
    def _total(self):
        return self._files_total

    @property
    def _current(self):
        return self._files_done

    def stop(self):
        if self._manager is not None:
            self._manager.cancel()

    def run(self):
        settings = self.settings
        engine.set_download_source(settings.download_source)
        manager = engine.DownloadManager(
            build_config(settings),
            on_progress=self._on_progress,
            on_status=lambda text: self.status_changed.emit(str(text)),
            on_log=lambda text: self.log_message.emit(str(text)),
        )
        self._manager = manager
        engine.install_patches(lambda: self._manager)
        try:
            self.status_changed.emit(f"开始安装 Minecraft {self.version_id}")
            data = engine.install_version(self.version_id, self.mc_dir, manager, status=None)

            if self.loader_id and not manager.stop_event.is_set():
                name = engine.LOADER_NAMES.get(self.loader_id, self.loader_id)
                ver = self.loader_version or "最新版"
                self.status_changed.emit(f"安装 {name} {ver}")
                self.installed_version = engine.install_mod_loader(
                    self.loader_id, self.version_id, self.mc_dir,
                    self.loader_version, manager, data)

            if manager.stop_event.is_set():
                self.download_finished.emit(self.version_id, False)
            else:
                self.status_changed.emit("安装完成")
                self.download_finished.emit(self.version_id, True)
        except engine.DownloadCancelled:
            self.log_message.emit("已取消")
            self.download_finished.emit(self.version_id, False)
        except Exception as e:  # noqa: BLE001
            self.error_occurred.emit(self.version_id, f"{type(e).__name__}: {e}")
            self.log_message.emit(traceback.format_exc())
        finally:
            manager.close()
            self._manager = None

    def _on_progress(self, snap: dict):
        self._files_done = snap["files_done"]
        self._files_total = snap["files_total"]
        self._current_file = snap["current"]
        self._last_speed = snap["speed"] / 1048576.0
        self.progress_changed.emit(int(snap["done"]), int(snap["total"]))
        self.speed_updated.emit(self._last_speed)
        self.detail_changed.emit(self._current_file, self._files_done, self._files_total)


class LoaderVersionWorker(QThread):
    """查询某个 Minecraft 版本可用的加载器版本（走 BMCLAPI 镜像）"""
    loaded = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, loader_id, mc_version, parent=None):
        super().__init__(parent)
        self.loader_id = loader_id
        self.mc_version = mc_version

    def run(self):
        try:
            self.loaded.emit(engine.list_loader_versions(self.loader_id, self.mc_version))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════
#  版本清单加载 Worker
# ═══════════════════════════════════════════════════════════════

class VersionListWorker(QThread):
    versions_loaded = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, source: str = SOURCE_BMCLAPI, parent=None):
        super().__init__(parent)
        self.source = source

    def run(self):
        try:
            engine.set_download_source(self.source)
            self.versions_loaded.emit(engine.list_versions())
        except Exception as e:
            self.error_occurred.emit(f"获取版本列表失败: {e}")


# ═══════════════════════════════════════════════════════════════
#  自更新检查 Worker
# ═══════════════════════════════════════════════════════════════

class UpdateChecker(QThread):
    # version, 页面地址, 附件直链, 字节数, 类型（zip 或 exe）
    update_found = pyqtSignal(str, str, str, int, str)
    no_update = pyqtSignal()
    error_occurred = pyqtSignal(str)

    # 仓库路径（GitHub 与 Gitee 同名）：AoLanXiang/DMDP
    REPO = os.environ.get("DMDP_REPO", "AoLanXiang/DMDP")
    # 先查 GitHub，失败（被墙/超时/仓库还不存在）再退回 Gitee
    SOURCES = (
        ("GitHub", "https://api.github.com/repos/{repo}/releases/latest", 8),
        ("Gitee", "https://gitee.com/api/v5/repos/{repo}/releases/latest", 15),
    )

    def __init__(self, channel: str = "stable", parent=None):
        super().__init__(parent)
        self.channel = channel

    def fetch_release(self):
        """按 GitHub → Gitee 顺序取最新 release；返回 (tag, url, 来源) 或抛最后一次异常"""
        import requests
        last_err = None
        for name, url_tpl, timeout in self.SOURCES:
            try:
                url = url_tpl.format(repo=self.REPO)
                r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
                if r.status_code != 200:
                    last_err = RuntimeError(f"{name} 返回 HTTP {r.status_code}")
                    continue
                data = r.json()
                tag = str(data.get("tag_name") or data.get("tag") or "")
                link = data.get("html_url") or data.get("url") or ""
                if not tag:
                    last_err = RuntimeError(f"{name} 没有 tag_name")
                    continue
                return tag, link, self.find_update_asset(data.get("assets") or [])
            except Exception as e:
                last_err = RuntimeError(f"{name}: {e}")
                continue
        raise last_err or RuntimeError("所有更新源都不可用")

    @staticmethod
    def find_update_asset(assets):
        """从 release 附件里挑更新包。

        优先级：绿色包 zip（名字含 portable 的最优先）> 其它 zip > DMDP.exe。
        zip 里带完整 runtime/app，替换后连依赖一起升级，不会出现"外层新、内层旧"。
        返回 (直链, 字节数, 类型)，挑不到返回 ("", 0, "")。
        """
        best = None
        for a in assets:
            name = str(a.get("name") or "").lower()
            url = a.get("browser_download_url") or a.get("download_url") or ""
            if not url:
                continue
            if name.endswith(".zip"):
                kind, score = "zip", (0 if "portable" in name else 1)
            elif name.endswith(".exe"):
                kind, score = "exe", (2 if name == "dmdp.exe" else 3)
            else:
                continue
            if best is None or score < best[0]:
                best = (score, url, int(a.get("size") or 0), kind)
        if best:
            return best[1], best[2], best[3]
        return "", 0, ""

    def run(self):
        if not self.REPO:
            self.no_update.emit()
            return
        try:
            tag, link, asset = self.fetch_release()
            latest = self.normalize_version(tag)
            if not latest:
                self.error_occurred.emit(f"无法解析标签：{tag!r}")
                return
            if self._compare_versions(latest, APP_VERSION) > 0:
                a_url, a_size, kind = asset
                self.update_found.emit(latest, link, a_url, a_size, kind)
            else:
                self.no_update.emit()
        except Exception as e:
            self.error_occurred.emit(str(e))

    @staticmethod
    def normalize_version(tag: str) -> str:
        """把 v1.1.0 / V1.1.0 / 1.1.0-beta.1 / release-1.1.0 统一成 1.1.0（保留预发布标记）"""
        t = (tag or "").strip()
        t = re.sub(r"^[vV]", "", t)                    # 去掉开头的 v/V
        m = re.search(r"(\d+(?:\.\d+)*)(?:[-_. ]?(\w[\w.]*)?)?", t)
        if not m:
            return ""
        core = m.group(1)
        pre = m.group(2) or ""
        return f"{core}-{pre}" if pre else core

    @staticmethod
    def _parse(v: str):
        """1.1.0-beta.1 → ((1,1,0), "beta.1")；1.1 → ((1,1), "")"""
        v = UpdateChecker.normalize_version(v)
        core, _, pre = v.partition("-")
        nums = [int(x) for x in re.findall(r"\d+", core)][:4]
        return nums, pre

    @staticmethod
    def _compare_versions(v1: str, v2: str) -> int:
        """返回 1 表示 v1 更新、-1 表示 v2 更新、0 表示相同。能处理 v 前缀与预发布标记。"""
        a, pa = UpdateChecker._parse(v1)
        b, pb = UpdateChecker._parse(v2)
        n = max(len(a), len(b))
        a += [0] * (n - len(a))
        b += [0] * (n - len(b))
        for x, y in zip(a, b):
            if x > y:
                return 1
            if x < y:
                return -1
        if pa == pb:
            return 0
        if not pa:      # 正式版 > 预发布版
            return 1
        if not pb:
            return -1
        return 1 if pa > pb else -1


class UpdateDownloader(QThread):
    """下载更新包（绿色包 zip 或 exe）到程序目录下的 .update 临时目录"""

    progress = pyqtSignal(int, int)   # 已下载字节, 总字节
    finished_ok = pyqtSignal(str)     # 本地文件路径
    failed = pyqtSignal(str)

    def __init__(self, url: str, size: int = 0, kind: str = "zip", parent=None):
        super().__init__(parent)
        self.url = url
        self.size = size
        self.kind = kind or "zip"

    def run(self):
        try:
            import requests
            target_dir = os.path.join(app_dir(), ".update")
            os.makedirs(target_dir, exist_ok=True)
            tmp = os.path.join(
                target_dir, "update.zip" if self.kind == "zip" else "DMDP-new.exe")
            got = 0
            total = self.size
            with requests.get(self.url, stream=True, timeout=(15, 120),
                              headers={"User-Agent": USER_AGENT}) as r:
                if r.status_code != 200:
                    self.failed.emit(f"下载失败：HTTP {r.status_code}")
                    return
                total = int(r.headers.get("Content-Length") or 0) or self.size
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(256 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        got += len(chunk)
                        self.progress.emit(got, total)
            if total and got != total:
                self.failed.emit(f"下载不完整：{got}/{total} 字节")
                return
            if self.kind == "zip":
                import zipfile
                if not zipfile.is_zipfile(tmp):
                    self.failed.emit("下载回来的文件不是有效的 zip")
                    return
                with zipfile.ZipFile(tmp) as zf:
                    names = zf.namelist()
                if not any(n.replace("\\", "/").split("/")[-1].lower() == "dmdp.exe"
                           for n in names):
                    self.failed.emit("压缩包里没有 DMDP.exe，可能不是绿色包")
                    return
            else:
                with open(tmp, "rb") as f:
                    if f.read(2) != b"MZ":
                        self.failed.emit("下载回来的文件不是有效的 exe")
                        return
            self.finished_ok.emit(tmp)
        except Exception as e:
            self.failed.emit(str(e))


# ═══════════════════════════════════════════════════════════════
#  自定义标题栏（嵌入标签页）
# ═══════════════════════════════════════════════════════════════

class TitleBar(QWidget):
    """PCL2 风格标题栏：左侧 Logo+名称，中间标签页，右侧窗口按钮"""

    page_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(48)
        self._dragging = False
        self._drag_pos = None
        self._buttons = []
        self._current_idx = 0

        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 左侧：Logo + 名称
        left_widget = QWidget()
        left_layout = QHBoxLayout(left_widget)
        left_layout.setContentsMargins(16, 0, 0, 0)
        left_layout.setSpacing(8)

        # 鸭子 Logo（纯绘制）
        logo_label = QLabel("🦆")
        logo_label.setFixedSize(28, 28)
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_label.setStyleSheet("""
            QLabel {
                background: #F5A623;
                border-radius: 14px;
                font-size: 14px;
            }
        """)
        left_layout.addWidget(logo_label)

        name_label = QLabel(f" {APP_NAME}")
        name_label.setStyleSheet(f"""
            QLabel {{
                color: white;
                font-size: 14px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }}
        """)
        left_layout.addWidget(name_label)

        ver_label = QLabel(f" v{APP_VERSION}")
        ver_label.setStyleSheet("""
            QLabel {
                color: rgba(255,255,255,0.5);
                font-size: 11px;
            }
        """)
        left_layout.addWidget(ver_label)

        layout.addWidget(left_widget)

        # 中间：标签页按钮
        tabs_widget = QWidget()
        tabs_layout = QHBoxLayout(tabs_widget)
        tabs_layout.setContentsMargins(20, 0, 0, 0)
        tabs_layout.setSpacing(2)

        tab_defs = [
            ("下载", "download"),
            ("下载队列", "queue"),
            ("设置", "settings"),
            ("关于", "about"),
        ]

        for i, (text, key) in enumerate(tab_defs):
            btn = TabButton(text, i)
            btn.clicked.connect(lambda checked, idx=i: self._on_tab_clicked(idx))
            tabs_layout.addWidget(btn)
            self._buttons.append(btn)

        tabs_layout.addStretch()
        layout.addWidget(tabs_widget, 1)

        # 右侧：下载源状态 + 更新检查 + 窗口按钮
        right_widget = QWidget()
        right_layout = QHBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 8, 0)
        right_layout.setSpacing(12)

        # BMCLAPI 状态
        self.source_label = QLabel("● BMCLAPI")
        self.source_label.setStyleSheet("""
            QLabel {
                color: rgba(255,255,255,0.7);
                font-size: 11px;
            }
        """)
        right_layout.addWidget(self.source_label)

        # 更新检查
        self.update_btn = QPushButton("⟳ 检查更新")
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.15);
                color: rgba(255,255,255,0.9);
                border: none;
                border-radius: 10px;
                padding: 4px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.25);
            }
        """)
        right_layout.addWidget(self.update_btn)

        # 分隔
        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setStyleSheet("background: rgba(255,255,255,0.15);")
        right_layout.addWidget(sep)

        # 窗口控制按钮
        self.min_btn = self._make_win_btn("—", "#F5C53D")
        self.max_btn = self._make_win_btn("□", "#42C84E")
        self.close_btn = self._make_win_btn("✕", "#E74C3C")

        right_layout.addWidget(self.min_btn)
        right_layout.addWidget(self.max_btn)
        right_layout.addWidget(self.close_btn)

        layout.addWidget(right_widget)

        # 应用渐变背景
        self._update_bg()

    def _make_win_btn(self, text: str, color: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedSize(28, 28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: rgba(255,255,255,0.7);
                border: none;
                font-size: 12px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.2);
                color: white;
            }}
        """)
        return btn

    def _update_bg(self):
        self.setStyleSheet(f"""
            TitleBar {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 {COLOR_BLUE},
                    stop:1 {COLOR_BLUE_DARK}
                );
            }}
        """)

    def _on_tab_clicked(self, idx: int):
        self.set_current(idx)
        self.page_changed.emit(idx)

    def set_current(self, idx: int):
        self._current_idx = idx
        for i, btn in enumerate(self._buttons):
            btn.set_active(i == idx)

    def update_source_label(self, source: str):
        if source == SOURCE_BMCLAPI:
            self.source_label.setText("● BMCLAPI")
            self.source_label.setStyleSheet("""
                QLabel {
                    color: #6EFFA0;
                    font-size: 11px;
                }
            """)
        else:
            self.source_label.setText("● Mojang")
            self.source_label.setStyleSheet("""
                QLabel {
                    color: rgba(255,255,255,0.7);
                    font-size: 11px;
                }
            """)

    # 窗口拖拽
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            # 检查是否点在按钮上
            widget = self.childAt(event.pos())
            if widget is None or not isinstance(widget, (QPushButton, TabButton, QLabel)):
                self._dragging = True
                self._drag_pos = event.globalPosition().toPoint()
                event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self._drag_pos = event.globalPosition().toPoint()
            window = self.window()
            window.move(window.pos() + delta)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._dragging = False


class TabButton(QPushButton):
    """标题栏中的标签页按钮"""

    def __init__(self, text: str, index: int):
        super().__init__(text)
        self._index = index
        self._active = False
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        self._update_style()

    def set_active(self, active: bool):
        self._active = active
        self.setChecked(active)
        self._update_style()

    def _update_style(self):
        if self._active:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.2);
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 4px 20px;
                    font-size: 13px;
                    font-weight: 600;
                }}
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: rgba(255,255,255,0.7);
                    border: none;
                    border-radius: 6px;
                    padding: 4px 20px;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background: rgba(255,255,255,0.1);
                    color: rgba(255,255,255,0.9);
                }
            """)


# ═══════════════════════════════════════════════════════════════
#  通用 UI 组件
# ═══════════════════════════════════════════════════════════════

class CardWidget(QFrame):
    """卡片容器"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            CardWidget {{
                background: {COLOR_BG_WHITE};
                border: 1px solid {COLOR_BORDER};
                border-radius: 12px;
            }}
        """)


class VersionCard(QFrame):
    """最新版本卡片"""
    clicked = pyqtSignal(str, str)  # version_id, version_type

    def __init__(self, label: str, version: str, date: str,
                 color: str, icon: str, parent=None):
        super().__init__(parent)
        self._version = version
        self._version_type = "release" if "正式" in label else "snapshot"
        self.setFixedHeight(90)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._color = color
        self._build_ui(label, version, date, icon)

    def _build_ui(self, label, version, date, icon):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(0)

        text_widget = QWidget()
        text_layout = QVBoxLayout(text_widget)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)

        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: rgba(255,255,255,0.8); font-size: 11px; letter-spacing: 1px;")
        text_layout.addWidget(lbl)

        ver = QLabel(version)
        ver.setStyleSheet("color: white; font-size: 24px; font-weight: 700;")
        text_layout.addWidget(ver)

        dt = QLabel(f"  {date}")
        dt.setStyleSheet("color: rgba(255,255,255,0.6); font-size: 11px;")
        text_layout.addWidget(dt)

        layout.addWidget(text_widget)
        layout.addStretch()

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 36px; opacity: 0.3;")
        layout.addWidget(icon_lbl)

        self.setStyleSheet(f"""
            VersionCard {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 {self._color},
                    stop:1 {self._color}DD
                );
                border-radius: 12px;
            }}
            VersionCard:hover {{
                border: 2px solid {self._color}AA;
            }}
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._version, self._version_type)


class ToggleSwitch(QWidget):
    """开关组件"""
    toggled = pyqtSignal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 22)
        self._checked = checked
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def isChecked(self):
        return self._checked

    def setChecked(self, checked):
        self._checked = checked
        self.toggled.emit(checked)
        self.update()

    def mousePressEvent(self, event):
        self._checked = not self._checked
        self.toggled.emit(self._checked)
        self.update()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg_color = QColor(COLOR_BLUE) if self._checked else QColor(COLOR_BORDER)
        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, 40, 22, 11, 11)

        knob_color = QColor("white")
        painter.setBrush(QBrush(knob_color))
        x = 20 if self._checked else 2
        painter.drawEllipse(x, 2, 18, 18)


class GroupBox(QFrame):
    """设置分组"""
    def __init__(self, title: str, icon: str = "", parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            GroupBox {{
                background: {COLOR_BG_WHITE};
                border: 1px solid {COLOR_BORDER};
                border-radius: 12px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setStyleSheet(f"background: {COLOR_BG_GRAY}; border-top-left-radius: 12px; border-top-right-radius: 12px;")
        header.setFixedHeight(42)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 0, 16, 0)

        if icon:
            icon_lbl = QLabel(icon)
            icon_lbl.setStyleSheet(f"color: {COLOR_BLUE}; font-size: 16px;")
            h_layout.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {COLOR_TEXT_PRIMARY};")
        h_layout.addWidget(title_lbl)
        h_layout.addStretch()

        layout.addWidget(header)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(16, 0, 16, 8)
        self.content_layout.setSpacing(0)
        layout.addWidget(self.content)

    def add_row(self, title: str, desc: str, control: QWidget):
        row = QFrame()
        row.setStyleSheet(f"border-bottom: 1px solid {COLOR_BG_GRAY};")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 12, 0, 12)

        info = QWidget()
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(f"font-size: 13px; font-weight: 500; color: {COLOR_TEXT_PRIMARY};")
        info_layout.addWidget(t)
        d = QLabel(desc)
        d.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_TERTIARY};")
        info_layout.addWidget(d)
        row_layout.addWidget(info)
        row_layout.addStretch()
        row_layout.addWidget(control)

        self.content_layout.addWidget(row)
        return row


# ═══════════════════════════════════════════════════════════════
#  侧边栏
# ═══════════════════════════════════════════════════════════════

class SidebarItem(QPushButton):
    def __init__(self, icon: str, text: str, badge: str = ""):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        self._active = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)

        icon_lbl = QLabel(icon)
        icon_lbl.setFixedSize(20, 20)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_lbl)

        text_lbl = QLabel(text)
        layout.addWidget(text_lbl)
        layout.addStretch()

        if badge:
            b = QLabel(badge)
            b.setStyleSheet(f"""
                background: {COLOR_ORANGE};
                color: white;
                font-size: 10px;
                font-weight: 700;
                padding: 2px 8px;
                border-radius: 10px;
            """)
            layout.addWidget(b)

        self._update_style()

    def set_active(self, active: bool):
        self._active = active
        self._update_style()

    def _update_style(self):
        if self._active:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {COLOR_BLUE};
                    color: white;
                    border: none;
                    border-radius: 8px;
                    text-align: left;
                    font-size: 13px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {COLOR_TEXT_SECONDARY};
                    border: none;
                    border-radius: 8px;
                    text-align: left;
                    font-size: 13px;
                }}
                QPushButton:hover {{
                    background: rgba(0,0,0,0.04);
                }}
            """)


# ═══════════════════════════════════════════════════════════════
#  下载页面
# ═══════════════════════════════════════════════════════════════

class DownloadPage(QWidget):
    """版本列表下载页"""
    request_download = pyqtSignal(str, str, str, str)  # version_id, mc_dir, loader_id, loader_version

    def __init__(self, settings: DMDPSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._versions = []
        self._filtered_type = "all"
        self._detail_version = None
        self._loader_worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(16)

        # 页面标题
        header = QHBoxLayout()
        title = QLabel("游戏下载 · 支持 Mod 加载器")
        title.setStyleSheet(f"font-size: 20px; font-weight: 600; color: {COLOR_TEXT_PRIMARY};")
        header.addWidget(title)
        subtitle = QLabel(
            "原版 / Forge / NeoForge / Fabric / Quilt，可一次装好"
        )
        subtitle.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_TERTIARY};")
        header.addWidget(subtitle)
        header.addStretch()
        layout.addLayout(header)

        # 刷新按钮
        refresh_btn = QPushButton("⟳ 刷新版本列表")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLOR_BG_GRAY};
                color: {COLOR_TEXT_SECONDARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 8px;
                padding: 6px 16px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {COLOR_BG_GRAY_DARK};
            }}
        """)
        refresh_btn.clicked.connect(self._on_refresh)
        header.addWidget(refresh_btn)

        # 最新版本卡片
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(12)

        self.card_release = VersionCard("最新正式版", "加载中...", "", COLOR_BLUE, "✓")
        self.card_release.clicked.connect(self._show_detail)
        cards_layout.addWidget(self.card_release)

        self.card_snapshot = VersionCard("最新预览版", "加载中...", "", COLOR_ORANGE, "⚗")
        self.card_snapshot.clicked.connect(self._show_detail)
        cards_layout.addWidget(self.card_snapshot)

        layout.addLayout(cards_layout)

        # 详情面板（默认隐藏）
        self.detail_frame = QFrame()
        self.detail_frame.setStyleSheet(f"""
            QFrame {{
                background: {COLOR_BG_GRAY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 12px;
            }}
        """)
        self.detail_frame.setVisible(False)
        detail_layout = QVBoxLayout(self.detail_frame)
        detail_layout.setContentsMargins(20, 16, 20, 16)
        detail_layout.setSpacing(12)

        detail_header = QHBoxLayout()
        self.detail_title = QLabel("")
        self.detail_title.setStyleSheet(f"font-size: 18px; font-weight: 600;")
        detail_header.addWidget(self.detail_title)
        detail_header.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet("border: none; font-size: 16px;")
        close_btn.clicked.connect(self._hide_detail)
        detail_header.addWidget(close_btn)
        detail_layout.addLayout(detail_header)

        # 详情信息网格
        info_grid = QHBoxLayout()
        info_grid.setSpacing(24)
        self.detail_labels = {}
        for key in ["类型", "发布时间", "下载源", "目标路径"]:
            col = QVBoxLayout()
            col.setSpacing(2)
            lbl = QLabel(key)
            lbl.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_TERTIARY};")
            val = QLabel("-")
            val.setStyleSheet(f"font-size: 13px; font-weight: 500;")
            col.addWidget(lbl)
            col.addWidget(val)
            self.detail_labels[key] = val
            info_grid.addLayout(col)
        detail_layout.addLayout(info_grid)

        # ── Mod 加载器选择 ──
        loader_row = QHBoxLayout()
        loader_row.setSpacing(10)
        loader_label = QLabel("Mod 加载器")
        loader_label.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_SECONDARY};")
        loader_row.addWidget(loader_label)

        self.loader_combo = QComboBox()
        self.loader_combo.setFixedHeight(32)
        self.loader_combo.setMinimumWidth(150)
        self.loader_combo.setStyleSheet(f"""
            QComboBox {{
                background: {COLOR_BG_WHITE};
                border: 1px solid {COLOR_BORDER};
                border-radius: 8px;
                padding: 4px 10px;
                font-size: 13px;
                color: {COLOR_TEXT_PRIMARY};
            }}
            QComboBox:hover {{ border-color: {COLOR_BLUE_LIGHT}; }}
            QComboBox::drop-down {{ border: none; width: 22px; }}
        """)
        self.loader_combo.addItem("不安装（仅原版）", "")
        for lid, name in engine.LOADER_NAMES.items():
            self.loader_combo.addItem(name, lid)
        self.loader_combo.currentIndexChanged.connect(self._on_loader_changed)
        loader_row.addWidget(self.loader_combo)

        self.loader_ver_combo = QComboBox()
        self.loader_ver_combo.setFixedHeight(32)
        self.loader_ver_combo.setMinimumWidth(170)
        self.loader_ver_combo.setStyleSheet(self.loader_combo.styleSheet())
        self.loader_ver_combo.addItem("自动（最新版）", "")
        self.loader_ver_combo.setEnabled(False)
        loader_row.addWidget(self.loader_ver_combo)

        self.loader_hint = QLabel("")
        self.loader_hint.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_TERTIARY};")
        loader_row.addWidget(self.loader_hint)
        loader_row.addStretch()
        detail_layout.addLayout(loader_row)

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self.download_btn = QPushButton("⬇ 开始下载")
        self.download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLOR_BLUE};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 28px;
                font-size: 14px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: {COLOR_BLUE_DARK};
            }}
        """)
        self.download_btn.clicked.connect(self._start_download)
        btn_row.addWidget(self.download_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLOR_BG_WHITE};
                color: {COLOR_TEXT_SECONDARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 8px;
                padding: 10px 20px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background: {COLOR_BG_GRAY};
            }}
        """)
        cancel_btn.clicked.connect(self._hide_detail)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        detail_layout.addLayout(btn_row)

        layout.addWidget(self.detail_frame)

        # 版本列表区域
        self.groups_container = QVBoxLayout()
        self.groups_container.setSpacing(8)
        self._group_widgets = {}

        for group_name, group_type in [
            ("正式版", "release"),
            ("预览版", "snapshot"),
            ("远古版", "old"),
            ("愚人节版", "april"),
        ]:
            group = self._create_group(group_name, group_type)
            self.groups_container.addWidget(group)
            self._group_widgets[group_type] = group

        layout.addLayout(self.groups_container)
        layout.addStretch()

    def _create_group(self, name: str, vtype: str) -> QFrame:
        """创建折叠版本组"""
        group = QFrame()
        group.setStyleSheet(f"""
            QFrame {{
                background: {COLOR_BG_WHITE};
                border: 1px solid {COLOR_BORDER};
                border-radius: 12px;
            }}
        """)

        g_layout = QVBoxLayout(group)
        g_layout.setContentsMargins(0, 0, 0, 0)
        g_layout.setSpacing(0)

        header = QPushButton(f"  {name}  (0)")
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setFixedHeight(44)
        header.setStyleSheet(f"""
            QPushButton {{
                background: {COLOR_BG_GRAY};
                border: none;
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
                text-align: left;
                font-size: 13px;
                font-weight: 600;
                color: {COLOR_TEXT_PRIMARY};
                padding-left: 20px;
            }}
            QPushButton:hover {{
                background: {COLOR_BG_GRAY_DARK};
            }}
        """)

        self._group_headers = getattr(self, "_group_headers", {})
        self._group_contents = getattr(self, "_group_contents", {})

        content = QWidget()
        content.setVisible(False)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # 使用闭包切换
        def toggle():
            visible = content.isVisible()
            content.setVisible(not visible)
            header.setText(
                header.text().replace("▼" if not visible else "▶", "▶" if not visible else "▼")
                if "▼" in header.text() or "▶" in header.text()
                else header.text()
            )

        # 更简洁的切换
        def toggle_simple():
            content.setVisible(not content.isVisible())

        header.clicked.connect(toggle_simple)
        g_layout.addWidget(header)
        g_layout.addWidget(content)

        self._group_contents[vtype] = content
        return group

    def set_versions(self, versions: list):
        """设置版本列表数据"""
        self._versions = versions

        # 找最新正式版和预览版
        latest_release = None
        latest_snapshot = None

        # 分类
        categorized = {"release": [], "snapshot": [], "old": [], "april": []}
        for v in versions:
            vtype = v.get("type", "release")
            if vtype == "release":
                categorized["release"].append(v)
                if latest_release is None or v["releaseTime"] > latest_release["releaseTime"]:
                    latest_release = v
            elif vtype == "snapshot":
                categorized["snapshot"].append(v)
                if latest_snapshot is None or v["releaseTime"] > latest_snapshot["releaseTime"]:
                    latest_snapshot = v
            elif vtype in ("old_beta", "old_alpha"):
                categorized["old"].append(v)
            elif vtype == "april":
                categorized["april"].append(v)

        # 更新卡片
        if latest_release:
            date_str = latest_release["releaseTime"].strftime("%Y/%m/%d")
            self.card_release._version = latest_release["id"]
            # 更新卡片文本
            labels = self.card_release.findChildren(QLabel)
            if len(labels) >= 3:
                labels[1].setText(latest_release["id"])
                labels[2].setText(f"  发布于 {date_str}")

        if latest_snapshot:
            date_str = latest_snapshot["releaseTime"].strftime("%Y/%m/%d")
            self.card_snapshot._version = latest_snapshot["id"]
            labels = self.card_snapshot.findChildren(QLabel)
            if len(labels) >= 3:
                labels[1].setText(latest_snapshot["id"])
                labels[2].setText(f"  发布于 {date_str}")

        # 更新各组
        type_names = {
            "release": "正式版",
            "snapshot": "预览版",
            "old": "远古版",
            "april": "愚人节版",
        }

        for vtype, vlist in categorized.items():
            content = self._group_contents.get(vtype)
            if not content:
                continue

            # 清空
            while content.layout().count():
                item = content.layout().takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            # 排序：新的在前
            vlist.sort(key=lambda x: x["releaseTime"], reverse=True)

            # 只显示前 50 条
            for v in vlist[:50]:
                row = self._create_version_row(v)
                content.layout().addWidget(row)

            # 更新标题计数
            group_widget = content.parent()
            header = group_widget.findChild(QPushButton)
            if header:
                header.setText(f"  {type_names.get(vtype, vtype)}  ({len(vlist)})")

    def _create_version_row(self, version: dict) -> QFrame:
        """创建版本行"""
        row = QFrame()
        row.setFixedHeight(44)
        row.setCursor(Qt.CursorShape.PointingHandCursor)

        vtype = version.get("type", "release")
        type_colors = {
            "release": ("R", "#E8F5E9", "#27AE60"),
            "snapshot": ("S", "#FFF3E0", "#F57C00"),
            "old_beta": ("β", "#F3E5F5", "#8E24AA"),
            "old_alpha": ("α", "#F3E5F5", "#8E24AA"),
            "april": ("A", "#FCE4EC", "#EC407A"),
        }
        icon_char, icon_bg, icon_color = type_colors.get(vtype, ("?", "#F5F5F5", "#999"))

        row.setStyleSheet(f"""
            QFrame {{
                background: {COLOR_BG_WHITE};
                border-bottom: 1px solid {COLOR_BG_GRAY};
            }}
            QFrame:hover {{
                background: {COLOR_BLUE_BG};
            }}
        """)

        layout = QHBoxLayout(row)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        # 类型图标
        icon = QLabel(icon_char)
        icon.setFixedSize(28, 28)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"background: {icon_bg}; color: {icon_color}; border-radius: 6px; font-size: 12px; font-weight: 700;")
        layout.addWidget(icon)

        # 版本信息
        info = QWidget()
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(0)
        vid = QLabel(version["id"])
        vid.setStyleSheet(f"font-size: 13px; font-weight: 500; color: {COLOR_TEXT_PRIMARY};")
        info_layout.addWidget(vid)
        date = version["releaseTime"].strftime("%Y-%m-%d")
        vdate = QLabel(date)
        vdate.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_TERTIARY};")
        info_layout.addWidget(vdate)
        layout.addWidget(info)
        layout.addStretch()

        # 检查是否已下载
        version_dir = os.path.join(
            resolve_mc_directory(self.settings),
            "versions", version["id"]
        )
        is_downloaded = os.path.isfile(
            os.path.join(version_dir, f"{version['id']}.jar")
        )

        btn = QPushButton("✓ 已下载" if is_downloaded else "⬇ 下载")
        btn.setFixedSize(80, 28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if is_downloaded:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLOR_BG_GRAY};
                    color: {COLOR_TEXT_SECONDARY};
                    border: none;
                    border-radius: 6px;
                    font-size: 12px;
                }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLOR_BLUE};
                    color: white;
                    border: none;
                    border-radius: 6px;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background: {COLOR_BLUE_DARK};
                }}
            """)
            btn.clicked.connect(
                lambda checked, vid=version["id"]: self._show_detail_for(vid)
            )
        layout.addWidget(btn)

        # 点击行也可查看详情
        row.mousePressEvent = lambda e, vid=version["id"]: (
            self._show_detail_for(vid) if e.button() == Qt.MouseButton.LeftButton else None
        )

        return row

    def _show_detail(self, version_id: str, version_type: str):
        self._show_detail_for(version_id)

    def _show_detail_for(self, version_id: str):
        """显示下载详情"""
        self._detail_version = version_id
        self.detail_title.setText(f"Minecraft {version_id}")

        # 查找版本信息
        version_info = None
        for v in self._versions:
            if v["id"] == version_id:
                version_info = v
                break

        if version_info:
            vtype = version_info.get("type", "release")
            type_names = {
                "release": "正式版 (Release)",
                "snapshot": "预览版 (Snapshot)",
                "old_beta": "远古版 (Beta)",
                "old_alpha": "远古版 (Alpha)",
                "april": "愚人节版 (April Fools)",
            }
            self.detail_labels["类型"].setText(type_names.get(vtype, vtype))
            self.detail_labels["发布时间"].setText(
                version_info["releaseTime"].strftime("%Y-%m-%d %H:%M")
            )

        source_text = "BMCLAPI 镜像" if self.settings.download_source == SOURCE_BMCLAPI else "Mojang 官方"
        self.detail_labels["下载源"].setText(source_text)
        target = resolve_mc_directory(self.settings)
        self.detail_labels["目标路径"].setText(os.path.join(target, "versions", version_id, ""))

        # 重置加载器选择
        self.loader_combo.setCurrentIndex(0)
        self.loader_ver_combo.clear()
        self.loader_ver_combo.addItem("自动（最新版）", "")
        self.loader_ver_combo.setEnabled(False)
        self.loader_hint.setText("")

        self.detail_frame.setVisible(True)

    def _hide_detail(self):
        self.detail_frame.setVisible(False)
        self._detail_version = None

    # ── Mod 加载器 ──

    def _on_loader_changed(self):
        loader_id = self.loader_combo.currentData() or ""
        self.loader_ver_combo.clear()
        self.loader_ver_combo.addItem("自动（最新版）", "")
        self.loader_ver_combo.setEnabled(False)

        if not loader_id or not self._detail_version:
            if not loader_id:
                self.loader_hint.setText("仅安装原版；之后也可以回来单独装加载器")
            return

        name = engine.LOADER_NAMES.get(loader_id, loader_id)
        self.loader_hint.setText(f"正在获取 {name} 版本列表…")

        if self._loader_worker is not None and self._loader_worker.isRunning():
            self._loader_worker.terminate()
            self._loader_worker.wait(2000)

        worker = LoaderVersionWorker(loader_id, self._detail_version)
        worker.loaded.connect(self._on_loader_versions)
        worker.failed.connect(self._on_loader_versions_failed)
        worker.finished.connect(worker.deleteLater)
        self._loader_worker = worker
        worker.start()

    def _on_loader_versions(self, versions: list):
        if not versions:
            self.loader_hint.setText("该版本没有可用的加载器版本")
            return
        self.loader_ver_combo.setEnabled(True)
        for v in versions:
            self.loader_ver_combo.addItem(v, v)
        self.loader_hint.setText(f"共 {len(versions)} 个版本，默认取最新")

    def _on_loader_versions_failed(self, message: str):
        self.loader_hint.setText(f"获取失败：{message[:60]}")

    def _selected_loader(self):
        loader_id = self.loader_combo.currentData() or ""
        loader_ver = (self.loader_ver_combo.currentData() or "") if loader_id else ""
        return loader_id, loader_ver

    def _start_download(self):
        if self._detail_version:
            loader_id, loader_ver = self._selected_loader()
            self.request_download.emit(
                self._detail_version,
                resolve_mc_directory(self.settings),
                loader_id,
                loader_ver,
            )
            self._hide_detail()

    def _on_refresh(self):
        """刷新版本列表"""
        # 由主窗口处理
        window = self.window()
        if hasattr(window, "refresh_versions"):
            window.refresh_versions()

    def show_loading(self):
        """显示加载状态"""
        labels = self.card_release.findChildren(QLabel)
        if len(labels) >= 3:
            labels[1].setText("加载中...")
            labels[2].setText("正在获取版本列表")
        labels = self.card_snapshot.findChildren(QLabel)
        if len(labels) >= 3:
            labels[1].setText("加载中...")
            labels[2].setText("正在获取版本列表")


# ═══════════════════════════════════════════════════════════════
#  下载队列页面
# ═══════════════════════════════════════════════════════════════

class QueuePage(QWidget):
    """下载队列页"""
    cancel_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("下载队列")
        title.setStyleSheet(f"font-size: 20px; font-weight: 600;")
        header.addWidget(title)
        subtitle = QLabel("多线程并发下载 · 使用 BMCLAPI 镜像加速")
        subtitle.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_TERTIARY};")
        header.addWidget(subtitle)
        header.addStretch()
        layout.addLayout(header)

        # 下载任务容器
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background: {COLOR_BG_WHITE};
            }}
        """)

        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.setSpacing(12)
        self.container_layout.addStretch()
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll)

        # 任务卡片字典
        self._task_widgets = {}

    def add_task(self, version: str, subtitle: str = ""):
        """添加下载任务卡片"""
        if version in self._task_widgets:
            return

        task = DownloadTaskCard(version, subtitle)
        task.cancel_clicked.connect(
            lambda v=version: self.cancel_requested.emit(v)
        )
        # 插入到 stretch 之前
        self.container_layout.insertWidget(
            self.container_layout.count() - 1, task
        )
        self._task_widgets[version] = task

    def update_task(self, version: str, status: str, progress: int,
                    total: int, speed: float, current_file: str = ""):
        """更新任务状态"""
        task = self._task_widgets.get(version)
        if task:
            task.update_state(status, progress, total, speed, current_file)

    def remove_task(self, version: str):
        task = self._task_widgets.pop(version, None)
        if task:
            task.setParent(None)
            task.deleteLater()


class DownloadTaskCard(QFrame):
    """单个下载任务卡片"""
    cancel_clicked = pyqtSignal(str)

    def __init__(self, version: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self._version = version
        self._subtitle = subtitle
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(f"""
            DownloadTaskCard {{
                background: {COLOR_BG_WHITE};
                border: 1px solid {COLOR_BORDER};
                border-radius: 12px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # 头部
        header = QHBoxLayout()
        icon = QLabel("⬇")
        icon.setFixedSize(32, 32)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"background: {COLOR_BLUE_BG}; color: {COLOR_BLUE}; border-radius: 8px; font-size: 16px;")
        header.addWidget(icon)

        info = QWidget()
        info_layout = QVBoxLayout(info)
        info_layout.setSpacing(2)
        name = QLabel(f"Minecraft {self._version}")
        if self._subtitle:
            name = QLabel(f"Minecraft {self._version} · {self._subtitle}")
        name.setStyleSheet("font-size: 14px; font-weight: 600;")
        info_layout.addWidget(name)
        self.date_label = QLabel("正在下载...")
        self.date_label.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_TERTIARY};")
        info_layout.addWidget(self.date_label)
        header.addWidget(info)
        header.addStretch()

        self.status_label = QLabel("下载中")
        self.status_label.setStyleSheet(f"""
            background: {COLOR_BLUE_BG};
            color: {COLOR_BLUE};
            font-size: 12px;
            font-weight: 500;
            padding: 4px 12px;
            border-radius: 20px;
        """)
        header.addWidget(self.status_label)

        layout.addLayout(header)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {COLOR_BG_GRAY_DARK};
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 {COLOR_BLUE},
                    stop:1 {COLOR_ORANGE}
                );
                border-radius: 4px;
            }}
        """)
        layout.addWidget(self.progress_bar)

        # 进度信息
        info_row = QHBoxLayout()
        self.file_info = QLabel("准备中...")
        self.file_info.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_SECONDARY};")
        info_row.addWidget(self.file_info)
        info_row.addStretch()
        self.speed_label = QLabel("0.0 MB/s")
        self.speed_label.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {COLOR_BLUE};")
        info_row.addWidget(self.speed_label)
        layout.addLayout(info_row)

        # 当前文件
        self.current_file_label = QLabel("")
        self.current_file_label.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_TERTIARY};")
        layout.addWidget(self.current_file_label)

        # 取消按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QPushButton("取消下载")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLOR_BG_WHITE};
                color: {COLOR_RED};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: #FDEAE8;
            }}
        """)
        self.cancel_btn.clicked.connect(
            lambda: self.cancel_clicked.emit(self._version)
        )
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

    def update_state(self, status: str, progress: int, total: int,
                     speed: float, current_file: str = ""):
        if total > 0:
            pct = int(progress / total * 100)
        else:
            pct = 0
        self.progress_bar.setValue(pct)

        self.file_info.setText(f"{fmt_size(progress)} / {fmt_size(total)} · {pct}%")
        self.speed_label.setText(f"{speed:.1f} MB/s")
        if current_file:
            self.current_file_label.setText(f"当前: {current_file}")
        elif status != "downloading":
            self.current_file_label.setText("")

        if status == "queued":
            self.status_label.setText("排队中")
            self.status_label.setStyleSheet(f"""
                background: {COLOR_BG_GRAY_DARK};
                color: {COLOR_TEXT_SECONDARY};
                font-size: 12px;
                font-weight: 500;
                padding: 4px 12px;
                border-radius: 20px;
            """)
            self.date_label.setText("等待前面的任务完成")
            self.file_info.setText("尚未开始")
            self.speed_label.setText("0.0 MB/s")
            return

        if status == "completed":
            self.status_label.setText("已完成")
            self.status_label.setStyleSheet(f"""
                background: #E8F5E9;
                color: {COLOR_GREEN};
                font-size: 12px;
                font-weight: 500;
                padding: 4px 12px;
                border-radius: 20px;
            """)
            self.date_label.setText("下载完成")
            self.cancel_btn.setText("关闭")
            self.cancel_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLOR_BG_GRAY};
                    color: {COLOR_TEXT_SECONDARY};
                    border: 1px solid {COLOR_BORDER};
                    border-radius: 6px;
                    padding: 6px 16px;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background: {COLOR_BG_GRAY_DARK};
                }}
            """)
        elif status == "error":
            self.status_label.setText("失败")
            self.status_label.setStyleSheet(f"""
                background: #FDEAE8;
                color: {COLOR_RED};
                font-size: 12px;
                font-weight: 500;
                padding: 4px 12px;
                border-radius: 20px;
            """)
            self.date_label.setText("下载出错")
        elif status == "cancelled":
            self.status_label.setText("已取消")
            self.status_label.setStyleSheet(f"""
                background: {COLOR_BG_GRAY_DARK};
                color: {COLOR_TEXT_SECONDARY};
                font-size: 12px;
                font-weight: 500;
                padding: 4px 12px;
                border-radius: 20px;
            """)


# ═══════════════════════════════════════════════════════════════
#  设置页面
# ═══════════════════════════════════════════════════════════════

class SettingsPage(QWidget):
    """设置页"""
    settings_changed = pyqtSignal()

    def __init__(self, settings: DMDPSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._build_ui()

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {COLOR_BG_WHITE}; }}")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(24, 16, 24, 16)
        container_layout.setSpacing(16)

        title = QLabel("设置")
        title.setStyleSheet(f"font-size: 20px; font-weight: 600;")
        container_layout.addWidget(title)

        # ── 下载源 ──
        source_group = GroupBox("下载源", "🌐")
        source_combo = QComboBox()
        source_combo.addItem("BMCLAPI（国内镜像）", SOURCE_BMCLAPI)
        source_combo.addItem("Mojang 官方源", SOURCE_MOJANG)
        source_combo.setCurrentIndex(
            0 if self.settings.download_source == SOURCE_BMCLAPI else 1
        )
        source_combo.currentIndexChanged.connect(
            lambda: self._on_source_changed(source_combo.currentData())
        )
        source_combo.setStyleSheet(self._combo_style())
        source_group.add_row(
            "下载源选择",
            "BMCLAPI 同时镜像游戏文件、资源与各类 Mod 加载器",
            source_combo
        )
        container_layout.addWidget(source_group)

        # ── 下载性能 ──
        dl_group = GroupBox("下载性能", "⬇")

        spin_workers = QSpinBox()
        spin_workers.setRange(1, 64)
        spin_workers.setValue(self.settings.max_workers)
        spin_workers.valueChanged.connect(
            lambda v: self._update_setting("max_workers", v)
        )
        spin_workers.setStyleSheet(self._spin_style())
        dl_group.add_row(
            "多线程数",
            "同时下载的文件数，也是总连接数上限；建议 8-16",
            spin_workers
        )

        spin_chunk = QSpinBox()
        spin_chunk.setRange(1, 16)
        spin_chunk.setValue(self.settings.chunk_size)
        spin_chunk.valueChanged.connect(
            lambda v: self._update_setting("chunk_size", v)
        )
        spin_chunk.setStyleSheet(self._spin_style())
        dl_group.add_row(
            "单文件分片数",
            "只剩一个大文件时，把它切成几段同时下（含断点续传）",
            spin_chunk
        )

        spin_threshold = QSpinBox()
        spin_threshold.setRange(1, 128)
        spin_threshold.setValue(self.settings.chunk_threshold_mb)
        spin_threshold.setSuffix(" MB")
        spin_threshold.valueChanged.connect(
            lambda v: self._update_setting("chunk_threshold_mb", v)
        )
        spin_threshold.setStyleSheet(self._spin_style())
        dl_group.add_row(
            "分片阈值",
            "大于该体积的文件才分片；小文件直接整包下",
            spin_threshold
        )

        spin_retries = QSpinBox()
        spin_retries.setRange(0, 10)
        spin_retries.setValue(self.settings.retries)
        spin_retries.valueChanged.connect(
            lambda v: self._update_setting("retries", v)
        )
        spin_retries.setStyleSheet(self._spin_style())
        dl_group.add_row(
            "失败重试次数",
            "镜像偶发 429/503 时会退避重试",
            spin_retries
        )

        spin_speed = QSpinBox()
        spin_speed.setRange(0, 9999)
        spin_speed.setValue(self.settings.speed_limit)
        spin_speed.setSuffix(" MB/s")
        spin_speed.valueChanged.connect(
            lambda v: self._update_setting("speed_limit", v)
        )
        spin_speed.setStyleSheet(self._spin_style())
        dl_group.add_row(
            "速度限制",
            "0 = 不限制",
            spin_speed
        )

        sha1_toggle = ToggleSwitch(checked=self.settings.verify_sha1)
        sha1_toggle.toggled.connect(
            lambda v: self._update_setting("verify_sha1", v)
        )
        dl_group.add_row(
            "校验文件完整性",
            "下载完成后比对 SHA1，失败的文件自动重下",
            sha1_toggle
        )
        container_layout.addWidget(dl_group)

        # ── 游戏目录 ──
        path_group = GroupBox("游戏目录", "📁")

        dir_edit = QLineEdit(self.settings.mc_directory)
        dir_edit.setPlaceholderText("留空 = 程序所在目录下的 .minecraft")
        dir_edit.setStyleSheet(self._input_style())
        dir_edit.setFixedWidth(300)
        dir_edit.editingFinished.connect(
            lambda: self._on_dir_edited(dir_edit)
        )
        browse_btn = QPushButton("浏览")
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.setStyleSheet(self._btn_secondary_style())
        browse_btn.clicked.connect(lambda: self._browse_dir(dir_edit))
        default_btn = QPushButton("跟随程序目录")
        default_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        default_btn.setStyleSheet(self._btn_secondary_style())
        default_btn.clicked.connect(lambda: self._use_default_dir(dir_edit))
        find_btn = QPushButton("查找 PCL2")
        find_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        find_btn.setStyleSheet(self._btn_secondary_style())
        find_btn.setToolTip("在程序目录、桌面、下载、我的文档和各盘根目录里找 PCL2 主程序（Plain Craft Launcher 2.exe）")
        find_btn.clicked.connect(lambda: self._find_pcl_dir(dir_edit, find_btn))
        self.dir_edit = dir_edit
        dir_widget = QWidget()
        dir_layout = QHBoxLayout(dir_widget)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.setSpacing(8)
        dir_layout.addWidget(dir_edit)
        dir_layout.addWidget(browse_btn)
        dir_layout.addWidget(default_btn)
        dir_layout.addWidget(find_btn)
        path_group.add_row(
            "游戏目录",
            ".minecraft 根目录；PCL 默认就把它放在 PCL.exe 旁边",
            dir_widget
        )

        self.dir_hint = QLabel("")
        self.dir_hint.setWordWrap(True)
        self.dir_hint.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_SECONDARY}; padding: 8px 0 4px 0;")
        path_group.content_layout.addWidget(self.dir_hint)
        self._refresh_dir_hint()
        container_layout.addWidget(path_group)

        # ── Java ──
        java_group = GroupBox("Java（安装 Mod 加载器时需要）", "☕")

        java_edit = QLineEdit(self.settings.java_path)
        java_edit.setPlaceholderText("留空 = 自动检测（Mojang 运行时 / 系统 Java）")
        java_edit.setStyleSheet(self._input_style())
        java_edit.setFixedWidth(300)
        java_edit.editingFinished.connect(
            lambda: self._update_setting("java_path", java_edit.text().strip())
        )
        java_browse = QPushButton("浏览")
        java_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        java_browse.setStyleSheet(self._btn_secondary_style())
        java_browse.clicked.connect(lambda: self._browse_java(java_edit))
        java_widget = QWidget()
        java_layout = QHBoxLayout(java_widget)
        java_layout.setContentsMargins(0, 0, 0, 0)
        java_layout.setSpacing(8)
        java_layout.addWidget(java_edit)
        java_layout.addWidget(java_browse)
        java_group.add_row(
            "Java 路径",
            "Forge / NeoForge 的安装器必须用 Java 运行",
            java_widget
        )

        auto_java_toggle = ToggleSwitch(checked=self.settings.auto_install_java)
        auto_java_toggle.toggled.connect(
            lambda v: self._update_setting("auto_install_java", v)
        )
        java_group.add_row(
            "自动下载 Java 运行库",
            "检测不到合适 Java 时，从 Mojang 下载对应运行时（约 50 MB）",
            auto_java_toggle
        )
        container_layout.addWidget(java_group)

        # ── 自更新 ──
        update_group = GroupBox("自更新", "🔄")

        auto_toggle = ToggleSwitch(checked=self.settings.auto_check_update)
        auto_toggle.toggled.connect(
            lambda v: self._update_setting("auto_check_update", v)
        )
        update_group.add_row(
            "启动时检查更新",
            "每次启动 DMDP 时自动检查新版本",
            auto_toggle
        )

        channel_combo = QComboBox()
        channel_combo.addItem("稳定版", "stable")
        channel_combo.addItem("测试版", "beta")
        channel_combo.setCurrentIndex(
            0 if self.settings.update_channel == "stable" else 1
        )
        channel_combo.currentIndexChanged.connect(
            lambda: self._update_setting(
                "update_channel", channel_combo.currentData()
            )
        )
        channel_combo.setStyleSheet(self._combo_style())
        update_group.add_row(
            "更新通道",
            "稳定版 / 测试版",
            channel_combo
        )

        check_btn = QPushButton("检查更新")
        check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        check_btn.setStyleSheet(self._btn_secondary_style())
        check_btn.clicked.connect(lambda: self._check_update())
        update_group.add_row(
            "当前版本",
            f"DMDP v{APP_VERSION}",
            check_btn
        )
        container_layout.addWidget(update_group)

        container_layout.addStretch()
        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _combo_style(self):
        return f"""
            QComboBox {{
                padding: 6px 12px;
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                font-size: 12px;
                background: white;
            }}
            QComboBox:hover {{
                border-color: {COLOR_BLUE};
            }}
        """

    def _spin_style(self):
        return f"""
            QSpinBox {{
                padding: 6px 10px;
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                font-size: 12px;
                text-align: center;
            }}
            QSpinBox:focus {{
                border-color: {COLOR_BLUE};
            }}
        """

    def _input_style(self):
        return f"""
            QLineEdit {{
                padding: 6px 12px;
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border-color: {COLOR_BLUE};
            }}
        """

    def _btn_secondary_style(self):
        return f"""
            QPushButton {{
                background: {COLOR_BG_GRAY};
                color: {COLOR_TEXT_SECONDARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {COLOR_BG_GRAY_DARK};
            }}
        """

    def _on_source_changed(self, source: str):
        self._update_setting("download_source", source)
        engine.set_download_source(source)
        window = self.window()
        if hasattr(window, "title_bar"):
            window.title_bar.update_source_label(source)
        self.settings_changed.emit()

    def _update_setting(self, key: str, value):
        setattr(self.settings, key, value)
        self.settings.save()
        if key in ("mc_directory", "java_path"):
            self._refresh_dir_hint()
        self.settings_changed.emit()

    def _refresh_dir_hint(self):
        if not hasattr(self, "dir_hint"):
            return
        resolved = resolve_mc_directory(self.settings)
        if (self.settings.mc_directory or "").strip():
            text = f"当前实际使用：{resolved}"
        else:
            text = f"跟随程序目录：{resolved}（与 PCL.exe 同级，PCL 能直接识别）"
        self.dir_hint.setText(text)

    def _on_dir_edited(self, edit: QLineEdit):
        self._update_setting("mc_directory", edit.text().strip())

    def _use_default_dir(self, edit: QLineEdit):
        edit.setText("")
        self._update_setting("mc_directory", "")
        self._refresh_dir_hint()

    def _find_pcl_dir(self, edit: QLineEdit, btn: QPushButton):
        """在后台线程里扫描 PCL2，找到就把游戏目录设成它的 .minecraft。"""
        btn.setEnabled(False)
        btn.setText("查找中…")
        self.dir_hint.setText("正在搜索 Plain Craft Launcher 2.exe（最多 15 秒）…")
        self._pcl_worker = PCLScanWorker(deep=True, parent=self)

        def done(dirs):
            btn.setEnabled(True)
            btn.setText("查找 PCL2")
            if dirs:
                pcl_dir = dirs[0]
                target = os.path.join(pcl_dir, ".minecraft")
                edit.setText(target)
                self._update_setting("mc_directory", target)
                extra = f"（另有 {len(dirs) - 1} 个）" if len(dirs) > 1 else ""
                exe_name = pcl_exe_in_dir(pcl_dir) or "PCL2 主程序"
                self.dir_hint.setText(
                    f"已找到 PCL2：{pcl_dir}（{exe_name}）{extra}\n游戏目录 → {target}"
                )
            else:
                self.dir_hint.setText(
                    "没找到 PCL2。可以把程序放到 PCL.exe 旁边，或点「浏览」手动选择 .minecraft 目录。"
                )

        self._pcl_worker.found.connect(done)
        self._pcl_worker.start()

    def _browse_dir(self, edit: QLineEdit):
        start = resolve_mc_directory(self.settings)
        d = QFileDialog.getExistingDirectory(self, "选择 .minecraft 目录", start)
        if d:
            edit.setText(d)
            self._update_setting("mc_directory", d)

    def _browse_java(self, edit: QLineEdit):
        f, _ = QFileDialog.getOpenFileName(self, "选择 java 可执行文件")
        if f:
            edit.setText(f)
            self._update_setting("java_path", f)

    def _check_update(self):
        window = self.window()
        if hasattr(window, "check_for_update"):
            window.check_for_update()


# ═══════════════════════════════════════════════════════════════
#  关于页面
# ═══════════════════════════════════════════════════════════════

class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 40, 24, 24)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        # Logo
        logo = QLabel("🦆")
        logo.setFixedSize(72, 72)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(f"background: {COLOR_ORANGE}; border-radius: 36px; font-size: 36px;")
        layout.addWidget(logo, alignment=Qt.AlignmentFlag.AlignCenter)

        name = QLabel("DMDP")
        name.setStyleSheet("font-size: 28px; font-weight: 700;")
        layout.addWidget(name, alignment=Qt.AlignmentFlag.AlignCenter)

        full = QLabel(APP_FULL_NAME)
        full.setStyleSheet(f"font-size: 13px; color: {COLOR_TEXT_SECONDARY};")
        layout.addWidget(full, alignment=Qt.AlignmentFlag.AlignCenter)

        ver = QLabel(f"版本 {APP_VERSION} · PyQt6 + 自研下载引擎")
        ver.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_TERTIARY};")
        layout.addWidget(ver, alignment=Qt.AlignmentFlag.AlignCenter)

        desc = QLabel(
            "DMDP 是一个仿 PCL 风格的 Minecraft 下载器：\n"
            "原版 + Forge / NeoForge / Fabric / Quilt 加载器一次装好，\n"
            "游戏文件、资源、加载器元数据全部走 BMCLAPI 镜像，\n"
            "多文件并发 + 单文件分片，SHA1 校验，装完 PCL 直接能启动。\n"
            "默认把 .minecraft 放在程序同级目录，与 PCL.exe 保持一致。"
        )
        desc.setStyleSheet(f"font-size: 13px; color: {COLOR_TEXT_SECONDARY}; line-height: 1.8;")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc)

        # 特性标签
        features = QHBoxLayout()
        features.setSpacing(40)
        for icon, text, color in [
            ("8", "多线程", COLOR_BLUE),
            ("BMCLAPI", "镜像加速", COLOR_ORANGE),
            ("SHA1", "完整性校验", COLOR_GREEN),
        ]:
            w = QWidget()
            wl = QVBoxLayout(w)
            wl.setSpacing(4)
            lbl1 = QLabel(icon)
            lbl1.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {color};")
            lbl1.setAlignment(Qt.AlignmentFlag.AlignCenter)
            wl.addWidget(lbl1)
            lbl2 = QLabel(text)
            lbl2.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_TERTIARY};")
            lbl2.setAlignment(Qt.AlignmentFlag.AlignCenter)
            wl.addWidget(lbl2)
            features.addWidget(w)
        layout.addLayout(features)


# ═══════════════════════════════════════════════════════════════
#  主窗口
# ═══════════════════════════════════════════════════════════════

class DMDPWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._first_run = not os.path.isfile(SETTINGS_FILE)
        self.settings = DMDPSettings.load()

        engine.set_download_source(self.settings.download_source)

        # 窗口设置
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Window
        )
        self.resize(self.settings.window_width, self.settings.window_height)
        self.setStyleSheet(f"background: {COLOR_BG_WHITE};")

        # 下载任务管理
        self._workers = {}          # version -> DownloadWorker（当前运行的那个）
        self._pending = deque()     # 等待中的任务
        self._active = None         # 正在运行的 worker
        self._active_version = None
        self._progress = {}         # version -> 进度快照

        self._build_ui()
        self._connect_signals()

        # 首次运行（还没有设置文件）时，试着找到 PCL2，把 .minecraft 指到它旁边
        QTimer.singleShot(500, self._auto_detect_pcl_for_first_run)

        # 自动加载版本列表
        QTimer.singleShot(200, self.refresh_versions)

        # 自动检查更新
        if self.settings.auto_check_update:
            QTimer.singleShot(2000, self.check_for_update)

    def _auto_detect_pcl_for_first_run(self):
        if not getattr(self, "_first_run", False) or os.name != "nt":
            return
        if (self.settings.mc_directory or "").strip():
            return
        self._pcl_auto_worker = PCLScanWorker(deep=False, parent=self)
        self._pcl_auto_worker.found.connect(self._on_auto_pcl_found)
        self._pcl_auto_worker.start()

    def _on_auto_pcl_found(self, dirs):
        if not dirs or (self.settings.mc_directory or "").strip():
            return
        pcl_dir = dirs[0]
        target = os.path.join(pcl_dir, ".minecraft")
        ans = QMessageBox.question(
            self,
            "找到 PCL2",
            f"检测到 PCL2 位于：\n{pcl_dir}\n（{pcl_exe_in_dir(pcl_dir) or 'PCL2 主程序'}）\n\n"
            f"要把游戏目录设成它的 .minecraft 吗？\n{target}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self.settings.mc_directory = target
            self.settings.save()
            if hasattr(self, "settings_page"):
                self.settings_page.dir_edit.setText(target)
                self.settings_page._refresh_dir_hint()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 标题栏（嵌入标签页）
        self.title_bar = TitleBar()
        main_layout.addWidget(self.title_bar)

        # 主内容区
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # 左侧边栏
        sidebar = QWidget()
        sidebar.setFixedWidth(180)
        sidebar.setStyleSheet(f"background: {COLOR_BG_GRAY}; border-right: 1px solid {COLOR_BORDER};")
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(8, 12, 8, 12)
        sb_layout.setSpacing(4)

        # 分类标签
        cat_label = QLabel("资源分类")
        cat_label.setStyleSheet(
            f"font-size: 11px; color: {COLOR_TEXT_TERTIARY}; "
            f"padding: 8px 14px 4px; text-transform: uppercase; letter-spacing: 1px;"
        )
        sb_layout.addWidget(cat_label)

        self.sidebar_items = []
        for icon, text, page_idx in [
            ("🎮", "原版游戏", 0),
            ("⭐", "收藏夹", 0),
        ]:
            item = SidebarItem(icon, text)
            item.set_active(page_idx == 0)
            item.clicked.connect(lambda checked, idx=page_idx: self._switch_page(idx))
            self.sidebar_items.append(item)
            sb_layout.addWidget(item)

        # 分隔线
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {COLOR_BORDER}; margin: 8px 12px;")
        sb_layout.addWidget(sep)

        tool_label = QLabel("工具")
        tool_label.setStyleSheet(
            f"font-size: 11px; color: {COLOR_TEXT_TERTIARY}; "
            f"padding: 8px 14px 4px; text-transform: uppercase; letter-spacing: 1px;"
        )
        sb_layout.addWidget(tool_label)

        for icon, text, page_idx, badge in [
            ("📋", "下载队列", 1, "0"),
            ("⚙", "设置", 2, ""),
        ]:
            item = SidebarItem(icon, text, badge=badge)
            item.clicked.connect(lambda checked, idx=page_idx: self._switch_page(idx))
            self.sidebar_items.append(item)
            sb_layout.addWidget(item)

        sb_layout.addStretch()
        content_layout.addWidget(sidebar)

        # 右侧堆叠页面
        self.stack = QStackedWidget()
        self.download_page = DownloadPage(self.settings)
        self.queue_page = QueuePage()
        self.settings_page = SettingsPage(self.settings)
        self.about_page = AboutPage()

        self.stack.addWidget(self.download_page)
        self.stack.addWidget(self.queue_page)
        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(self.about_page)

        content_layout.addWidget(self.stack, 1)

        # 底部状态栏
        self.status_bar = self._create_status_bar()
        content_layout.addWidget(self.status_bar)

        main_layout.addWidget(content, 1)

    def _create_status_bar(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(36)
        bar.setStyleSheet(f"background: {COLOR_BG_GRAY}; border-top: 1px solid {COLOR_BORDER};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(20)

        self.status_ready = QLabel("● 就绪")
        self.status_ready.setStyleSheet(
            f"font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
        )
        layout.addWidget(self.status_ready)

        # 下载进度
        self.status_progress_bar = QProgressBar()
        self.status_progress_bar.setFixedWidth(200)
        self.status_progress_bar.setFixedHeight(6)
        self.status_progress_bar.setRange(0, 100)
        self.status_progress_bar.setTextVisible(False)
        self.status_progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {COLOR_BORDER};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 {COLOR_BLUE},
                    stop:1 {COLOR_ORANGE}
                );
                border-radius: 3px;
            }}
        """)
        self.status_progress_bar.setVisible(False)
        layout.addWidget(self.status_progress_bar)

        self.status_progress_text = QLabel("")
        self.status_progress_text.setStyleSheet(
            f"font-size: 11px; color: {COLOR_TEXT_SECONDARY};"
        )
        layout.addWidget(self.status_progress_text)

        layout.addStretch()

        self.status_downloaded = QLabel("已下载: 0 个版本")
        self.status_downloaded.setStyleSheet(
            f"font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
        )
        layout.addWidget(self.status_downloaded)

        self.status_source = QLabel(
            "BMCLAPI" if self.settings.download_source == SOURCE_BMCLAPI else "Mojang"
        )
        self.status_source.setStyleSheet(
            f"font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
        )
        layout.addWidget(self.status_source)

        return bar

    def _connect_signals(self):
        self.title_bar.page_changed.connect(self._switch_page)
        self.title_bar.min_btn.clicked.connect(self.showMinimized)
        self.title_bar.max_btn.clicked.connect(self._toggle_maximize)
        self.title_bar.close_btn.clicked.connect(self.close)

        self.title_bar.update_btn.clicked.connect(self.check_for_update)

        self.download_page.request_download.connect(self._start_download)
        self.queue_page.cancel_requested.connect(self._cancel_download)
        self.settings_page.settings_changed.connect(self._on_settings_changed)

    def _switch_page(self, idx: int):
        self.stack.setCurrentIndex(idx)
        self.title_bar.set_current(idx)
        # 更新侧边栏
        for i, item in enumerate(self.sidebar_items):
            # 根据 idx 映射
            page_map = {0: 0, 1: 1, 2: 2, 3: 3}
            item.set_active(page_map.get(idx) == i if i < 2 else idx == (i + 1))

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ─── 版本列表 ───

    def refresh_versions(self):
        """刷新版本列表"""
        self.download_page.show_loading()
        self.status_ready.setText("● 正在获取版本列表...")

        self._version_worker = VersionListWorker(self.settings.download_source)
        self._version_worker.versions_loaded.connect(self._on_versions_loaded)
        self._version_worker.error_occurred.connect(self._on_versions_error)
        self._version_worker.start()

    def _on_versions_loaded(self, versions: list):
        self.download_page.set_versions(versions)
        self.status_ready.setText("● 就绪")
        count = len(versions)
        self.status_downloaded.setText(f"共 {count} 个版本可用")

    def _on_versions_error(self, error: str):
        self.status_ready.setText(f"● 获取失败: {error[:30]}")

    # ─── 下载管理 ───

    def _start_download(self, version: str, mc_dir: str,
                        loader_id: str = "", loader_version: str = ""):
        """加入下载队列：一次只跑一个任务，避免多个任务抢带宽、抢统计。"""
        pending_versions = [t["version"] for t in self._pending]
        if self._active_version == version or version in pending_versions:
            return

        try:
            os.makedirs(mc_dir, exist_ok=True)
        except OSError as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "目录不可用", f"无法创建目录：{mc_dir}\n{e}")
            return

        name = engine.LOADER_NAMES.get(loader_id, "") if loader_id else ""
        subtitle = f"{name} {loader_version or '最新版'}" if name else "仅原版"
        self._pending.append({
            "version": version, "mc_dir": mc_dir,
            "loader_id": loader_id, "loader_version": loader_version,
            "subtitle": subtitle,
        })
        self.queue_page.add_task(version, subtitle)
        self.queue_page.update_task(version, "queued", 0, 0, 0, "")
        self._switch_page(1)

        if self._active is None:
            self._start_next()

    def _start_next(self):
        if self._active is not None or not self._pending:
            return
        task = self._pending.popleft()
        version = task["version"]
        worker = DownloadWorker(
            version, task["mc_dir"], self.settings,
            task["loader_id"], task["loader_version"],
        )
        worker.status_changed.connect(
            lambda text, v=version: self._on_download_status(v, text)
        )
        worker.progress_changed.connect(
            lambda cur, total, v=version: self._on_download_progress(v, cur, total)
        )
        worker.speed_updated.connect(
            lambda speed, v=version: self._on_speed_update(v, speed)
        )
        worker.detail_changed.connect(
            lambda fname, done, total, v=version: self._on_file_info(v, fname, done, total)
        )
        worker.download_finished.connect(self._on_download_finished)
        worker.error_occurred.connect(self._on_download_error)

        self._workers[version] = worker
        self._active = worker
        self._active_version = version
        self._progress[version] = {"done": 0, "total": 0, "speed": 0.0, "file": ""}

        self.status_ready.setText(f"● 正在安装 {version}")
        self.status_progress_bar.setVisible(True)
        self.status_progress_bar.setValue(0)
        self.status_progress_text.setText("准备中…")
        worker.start()

    def _update_task_card(self, version: str, status: str = "downloading"):
        info = self._progress.get(version, {})
        self.queue_page.update_task(
            version, status,
            int(info.get("done", 0)), int(info.get("total", 0)),
            float(info.get("speed", 0.0)), info.get("file", ""),
        )

    def _on_download_status(self, version: str, text: str):
        if version == self._active_version:
            self.status_progress_text.setText(text)
        self._update_task_card(version)

    def _on_download_progress(self, version: str, current: int, total: int):
        info = self._progress.setdefault(
            version, {"done": 0, "total": 0, "speed": 0.0, "file": ""}
        )
        info["done"], info["total"] = current, total
        pct = int(current / total * 100) if total else 0
        if version == self._active_version:
            self.status_progress_bar.setValue(pct)
            self.status_progress_text.setText(
                f"{version}: {fmt_size(current)} / {fmt_size(total)} · {pct}%"
            )
        self._update_task_card(version)

    def _on_speed_update(self, version: str, speed: float):
        info = self._progress.setdefault(
            version, {"done": 0, "total": 0, "speed": 0.0, "file": ""}
        )
        info["speed"] = speed
        self._update_task_card(version)

    def _on_file_info(self, version: str, fname: str, done: int, total: int):
        info = self._progress.setdefault(
            version, {"done": 0, "total": 0, "speed": 0.0, "file": ""}
        )
        info["file"] = fname
        info["files_done"] = done
        info["files_total"] = total
        self._update_task_card(version)

    def _on_download_finished(self, version: str, success: bool):
        if success:
            total_bytes = int(self._progress.get(version, {}).get("total", 0))
            self.queue_page.update_task(version, "completed", total_bytes, total_bytes, 0, "")
            self.status_progress_text.setText(f"{version} 安装完成")
        else:
            self.queue_page.update_task(version, "cancelled", 0, 0, 0, "已取消")

        self._workers.pop(version, None)
        self._progress.pop(version, None)
        if version == self._active_version:
            self._active = None
            self._active_version = None
        self.status_progress_bar.setVisible(False)

        # 刷新「已下载」标记
        self.refresh_versions()

        if self._pending:
            QTimer.singleShot(300, self._start_next)
        else:
            self.status_ready.setText("● 就绪")

    def _on_download_error(self, version: str, error: str):
        self.queue_page.update_task(version, "error", 0, 0, 0, error[:120])
        self.status_ready.setText(f"● {version} 安装失败")
        self.status_progress_bar.setVisible(False)

        self._workers.pop(version, None)
        self._progress.pop(version, None)
        if version == self._active_version:
            self._active = None
            self._active_version = None

        if self._pending:
            QTimer.singleShot(300, self._start_next)

    def _cancel_download(self, version: str):
        for i, task in enumerate(self._pending):
            if task["version"] == version:
                del self._pending[i]
                self.queue_page.remove_task(version)
                return

        worker = self._workers.get(version)
        if worker and worker.isRunning():
            worker.stop()
            self.queue_page.update_task(version, "cancelled", 0, 0, 0, "正在取消…")
        else:
            self.queue_page.remove_task(version)

    # ─── 设置变更 ───

    def _on_settings_changed(self):
        # 更新标题栏源标签
        self.title_bar.update_source_label(self.settings.download_source)
        self.status_source.setText(
            "BMCLAPI" if self.settings.download_source == SOURCE_BMCLAPI else "Mojang"
        )

    # ─── 更新检查 ───

    def check_for_update(self):
        self.title_bar.update_btn.setText("⟳ 检查中...")
        self._update_checker = UpdateChecker(self.settings.update_channel)
        self._update_checker.update_found.connect(self._on_update_found)
        self._update_checker.no_update.connect(self._on_no_update)
        self._update_checker.error_occurred.connect(self._on_update_error)
        self._update_checker.start()

    def _on_update_found(self, version: str, page: str, asset: str, size: int, kind: str = "zip"):
        self.title_bar.update_btn.setText("✦ 有新版本")
        self._pending_update = (version, page, asset, size, kind)
        from PyQt6.QtWidgets import QMessageBox

        if not page:
            page = f"https://github.com/{UpdateChecker.REPO}/releases"
        box = QMessageBox(self)
        box.setWindowTitle("发现新版本")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"DMDP v{version} 已发布（当前 v{APP_VERSION}）")
        if asset:
            what = "绿色包（含完整程序文件）" if kind == "zip" else "单文件程序"
            box.setInformativeText(
                f"可以直接在程序里更新：下载{what}，退出后自动替换并重启。\n"
                f"下载大小约 {size/1024/1024:.1f} MB（如显示 0.0 则为未知）。\n"
                f"存档、设置和 Java 不会被覆盖。\n\n"
                f"下载页：{page}")
        else:
            box.setInformativeText(f"发布里没有找到可用的更新附件（zip 或 exe），请手动下载。\n\n下载页：{page}")
        auto_btn = box.addButton("下载并更新", QMessageBox.ButtonRole.AcceptRole) if asset else None
        open_btn = box.addButton("打开下载页", QMessageBox.ButtonRole.ActionRole)
        later_btn = box.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if auto_btn is not None and clicked is auto_btn:
            self._start_update_download(asset, size, kind)
        elif clicked is open_btn:
            from PyQt6.QtGui import QDesktopServices
            from PyQt6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(page))

    def _start_update_download(self, asset: str, size: int, kind: str = "zip"):
        from PyQt6.QtWidgets import QProgressDialog
        self._progress_dlg = QProgressDialog("正在下载新版本…", "取消", 0, 100, self)
        self._progress_dlg.setWindowTitle("更新 DMDP")
        self._progress_dlg.setMinimumDuration(0)
        self._progress_dlg.setAutoClose(False)

        self._downloader = UpdateDownloader(asset, size, kind)
        self._downloader.progress.connect(self._on_update_progress)
        self._downloader.finished_ok.connect(lambda p: self._on_update_downloaded(p, kind))
        self._downloader.failed.connect(self._on_update_download_failed)
        self._progress_dlg.canceled.connect(self._downloader.requestInterruption)
        self._progress_dlg.show()
        self._downloader.start()

    def _on_update_progress(self, got: int, total: int):
        if not hasattr(self, "_progress_dlg") or self._progress_dlg is None:
            return
        if total > 0:
            self._progress_dlg.setMaximum(100)
            self._progress_dlg.setValue(int(got * 100 / total))
            self._progress_dlg.setLabelText(
                f"正在下载新版本… {got/1024/1024:.1f} / {total/1024/1024:.1f} MB")
        else:
            self._progress_dlg.setLabelText(f"正在下载新版本… {got/1024/1024:.1f} MB")

    def _on_update_download_failed(self, msg: str):
        from PyQt6.QtWidgets import QMessageBox
        if getattr(self, "_progress_dlg", None):
            self._progress_dlg.close()
        QMessageBox.warning(self, "更新失败", f"{msg}\n\n可以在浏览器里手动下载替换。")

    def _on_update_downloaded(self, path: str, kind: str = "zip"):
        from PyQt6.QtWidgets import QMessageBox
        if getattr(self, "_progress_dlg", None):
            self._progress_dlg.close()
        if kind == "zip":
            self._apply_zip_update(path, self._pending_update[0], QMessageBox)
            return
        if not getattr(sys, "frozen", False):
            QMessageBox.information(self, "已下载",
                                    f"新版本已下载到：\n{path}\n\n（开发模式不执行自动替换）")
            return
        target = os.path.abspath(sys.executable)
        old = target + ".old"
        try:
            if os.path.exists(old):
                try:
                    os.remove(old)
                except OSError:
                    pass
            os.rename(target, old)          # Windows 允许重命名正在运行的 exe
            try:
                os.replace(path, target)    # 用新文件占位
            except Exception:
                os.rename(old, target)      # 失败回滚
                raise
        except Exception as e:
            QMessageBox.warning(
                self, "自动替换失败",
                f"无法替换程序文件：{e}\n\n"
                f"新版本已下载到：\n{path}\n"
                f"请退出程序后，用这个文件覆盖当前的 DMDP.exe。")
            return
        from PyQt6.QtCore import QProcess
        QProcess.startDetached(target, [])
        QApplication.quit()

    def _apply_zip_update(self, zip_path: str, version: str, QMessageBox):
        """绿色包更新：铺到 .update\new，启动 apply.cmd，退出后自动替换并重启"""
        import subprocess

        if not getattr(sys, "frozen", False):
            QMessageBox.information(
                self, "已下载",
                f"新版本已下载到：\n{zip_path}\n\n（开发模式不执行自动替换）")
            return

        prog = os.path.dirname(os.path.abspath(sys.executable))
        try:
            bat_path = stage_zip_update(zip_path, prog, version)
        except Exception as e:
            QMessageBox.warning(
                self, "更新包不可用",
                f"无法准备更新：{e}\n\n请手动下载绿色包解压覆盖。\n文件：{zip_path}")
            return

        try:
            subprocess.Popen(["cmd", "/c", bat_path],
                             creationflags=0x00000008 | 0x00000200,
                             close_fds=True)
        except Exception as e:
            QMessageBox.warning(
                self, "更新需要手动完成",
                f"无法启动替换脚本：{e}\n\n请手动用这个绿色包解压覆盖：\n{zip_path}")
            return

        QMessageBox.information(
            self, "即将更新",
            f"DMDP v{version} 已下载完成。\n\n程序现在会退出，几秒后自动重启到新版本。")
        QApplication.quit()

    def _on_no_update(self):
        self.title_bar.update_btn.setText("⟳ 检查更新")
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, "已是最新版本",
            f"DMDP v{APP_VERSION} 已是最新版本。"
        )

    def _on_update_error(self, error: str):
        self.title_bar.update_btn.setText("⟳ 检查更新")

    # ─── 窗口事件 ───

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.settings.window_width = self.width()
        self.settings.window_height = self.height()
        self.settings.save()

    def closeEvent(self, event):
        # 检查是否有正在进行的下载
        active = [v for v, w in self._workers.items() if w.isRunning()]
        if active:
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "确认退出",
                f"还有 {len(active)} 个下载任务正在进行，确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        # 停止所有 worker
        for worker in self._workers.values():
            if worker.isRunning():
                worker.stop()
                worker.quit()
                worker.wait(3000)

        self.settings.save()
        event.accept()


# ═══════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════

def selftest() -> int:
    """打包自检：验证 Qt、引擎、界面都能正常加载（CI 里跑）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication(sys.argv[:1])
    window = DMDPWindow()
    cfg = build_config(window.settings)
    print(
        f"[selftest] DMDP {APP_VERSION} OK | workers={cfg.max_workers} "
        f"chunks={cfg.chunk_size} pages={window.stack.count()} "
        f"mc_dir={resolve_mc_directory(window.settings)}",
        flush=True,
    )
    window.close()
    app.quit()

    # 自更新链路自检（附件挑选 / 版本比较 / 解压校验 / 替换脚本 / 存档安全）
    rc = update_selftest(verbose=("--verbose" in sys.argv))
    return rc


# apply.cmd 里替换的文件清单：只动程序文件，绝不碰 .minecraft / PCL / 设置
UPDATE_KEEP_NAMES = (".minecraft", "PCL", "settings.json", "logs", "crash-reports")


def stage_zip_update(zip_path: str, prog_dir: str, version: str) -> str:
    """把绿色包解压到 <prog>/.update/new 并生成 apply.cmd，返回脚本路径。

    纯文件操作、不碰 Qt，便于自检。解压后校验：必须有 DMDP.exe、runtime/、app/。
    替换范围仅限 runtime/、app/、DMDP.exe 与三个说明文件，用户目录一律不动。
    """
    import shutil
    import zipfile

    upd_dir = os.path.join(prog_dir, ".update")
    new_dir = os.path.join(upd_dir, "new")
    if os.path.isdir(new_dir):
        shutil.rmtree(new_dir, ignore_errors=True)
    os.makedirs(new_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n and not n.endswith("/")]
        if not names:
            raise RuntimeError("压缩包是空的")
        tops = {n.replace("\\", "/").split("/")[0] for n in names}
        first = names[0].replace("\\", "/")
        strip = len(tops) == 1 and "/" in first
        for n in names:
            src = n.replace("\\", "/")
            if src.startswith("../") or src.startswith("/"):
                raise RuntimeError(f"压缩包里有非法路径：{n}")
            parts = src.split("/")
            rel = os.path.join(*parts[1:]) if strip and len(parts) > 1 else src
            dst = os.path.join(new_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with zf.open(n) as rf, open(dst, "wb") as wf:
                shutil.copyfileobj(rf, wf)

    if not os.path.isfile(os.path.join(new_dir, "DMDP.exe")):
        raise RuntimeError("解压后找不到 DMDP.exe")
    for sub in ("runtime", "app"):
        if not os.path.isdir(os.path.join(new_dir, sub)):
            raise RuntimeError(f"绿色包不完整（缺 {sub}\\）")

    # 单文件版布局（外层 DMDP.exe + 内层 DMDP\）要同步写 VERSION，
    # 否则外层自解压壳会认为版本没变、不去重新解压
    has_outer = os.path.isfile(os.path.join(os.path.dirname(prog_dir.rstrip("\\")), "DMDP.exe"))
    ver_line = f'echo {version}>"%DST%\\VERSION"' if has_outer else "rem VERSION 无需更新"

    lines = [
        "@echo off",
        "setlocal",
        "chcp 65001 >nul",
        'set "SRC=%~dp0new"',
        f'set "DST={prog_dir}"',
        ":wait",
        'tasklist /FI "IMAGENAME eq DMDP.exe" 2>nul | find /I "DMDP.exe" >nul',
        "if not errorlevel 1 (",
        "  timeout /t 1 /nobreak >nul",
        "  goto wait",
        ")",
        'if exist "%DST%\\DMDP.exe.old" del /f /q "%DST%\\DMDP.exe.old" >nul 2>nul',
        'robocopy "%SRC%\\runtime" "%DST%\\runtime" /MIR /NFL /NDL /NJH /NJS /NP >nul',
        'robocopy "%SRC%\\app" "%DST%\\app" /MIR /NFL /NDL /NJH /NJS /NP >nul',
        'if exist "%SRC%\\DMDP.exe" copy /Y "%SRC%\\DMDP.exe" "%DST%\\DMDP.exe" >nul',
        'if exist "%SRC%\\README.md" copy /Y "%SRC%\\README.md" "%DST%\\" >nul',
        'if exist "%SRC%\\使用说明.txt" copy /Y "%SRC%\\使用说明.txt" "%DST%\\" >nul',
        ver_line,
        'start "" "%DST%\\DMDP.exe"',
    ]
    bat_path = os.path.join(upd_dir, "apply.cmd")
    with open(bat_path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return bat_path


def update_selftest(verbose: bool = False) -> int:
    """自更新自检：附件挑选、版本比较、解压校验、替换脚本内容、用户目录不被碰。

    返回 0 表示全部通过，非 0 表示有失败项。用 --selftest 时会自动跑。
    """
    import shutil
    import tempfile
    import zipfile

    fails = []

    def check(name, cond, detail=""):
        if cond:
            if verbose:
                print(f"  [ok] {name}")
        else:
            fails.append(f"{name} {detail}".strip())
            print(f"  [FAIL] {name} {detail}")

    print("[update-selftest] 开始", flush=True)

    # 1) 附件优先级：绿色包 > 其它 zip > DMDP.exe > 别的 exe
    assets = [
        {"name": "DMDP.exe", "browser_download_url": "u1", "size": 1},
        {"name": "DMDP-1.2.0-win64-portable.zip", "browser_download_url": "u2", "size": 2},
        {"name": "DMDP-1.2.0-setup.exe", "browser_download_url": "u3", "size": 3},
        {"name": "DMDP-1.2.0.zip", "browser_download_url": "u4", "size": 4},
    ]
    url, size, kind = UpdateChecker.find_update_asset(assets)
    check("优先选绿色包", (url, kind) == ("u2", "zip"), f"got={url}/{kind}")
    url2, _, kind2 = UpdateChecker.find_update_asset([assets[0]])
    check("只有 exe 时回退 exe", (url2, kind2) == ("u1", "exe"), f"got={url2}/{kind2}")
    _, _, kind3 = UpdateChecker.find_update_asset([assets[2]])
    check("装不出 zip 也不误选安装包以外", kind3 == "exe", f"got={kind3}")
    check("空附件列表", UpdateChecker.find_update_asset([]) == ("", 0, ""))
    check("安装包不会被当更新包优先", UpdateChecker.find_update_asset([assets[2]])[0] == "u3")

    # 2) 版本比较
    cmp_ = UpdateChecker._compare_versions
    check("v1.2.0 > 1.1.0", cmp_("v1.2.0", "1.1.0") == 1)
    check("1.1.0 == V1.1.0", cmp_("V1.1.0", "1.1.0") == 0)
    check("beta < 正式", cmp_("1.1.0-beta.1", "1.1.0") == -1)
    check("release-1.3.0 解析", UpdateChecker.normalize_version("release-1.3.0") == "1.3.0")

    tmp = tempfile.mkdtemp(prefix="dmdp-selftest-")
    try:
        # 3) 造一个绿色包（带一层顶部目录，模拟真实打包）
        port = os.path.join(tmp, "DMDP-portable")
        os.makedirs(os.path.join(port, "runtime"))
        os.makedirs(os.path.join(port, "app"))
        open(os.path.join(port, "DMDP.exe"), "wb").write(b"MZfake")
        open(os.path.join(port, "runtime", "python312.dll"), "wb").write(b"dll")
        open(os.path.join(port, "app", "dmdp.py"), "w").write("# fake")
        open(os.path.join(port, "README.md"), "w").write("readme")
        zip_path = os.path.join(tmp, "pkg.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            for root, _, fs in os.walk(port):
                for f in fs:
                    full = os.path.join(root, f)
                    zf.write(full, os.path.relpath(full, tmp))

        # 4) 铺开：绿色版布局（prog 目录里直接有 exe/runtime/app）
        prog = os.path.join(tmp, "prog")
        os.makedirs(os.path.join(prog, ".minecraft", "saves"))
        open(os.path.join(prog, ".minecraft", "saves", "world.dat"), "w").write("save")
        os.makedirs(os.path.join(prog, "runtime"))
        open(os.path.join(prog, "runtime", "old-only.dll"), "wb").write(b"old")
        os.makedirs(os.path.join(prog, "app"))
        open(os.path.join(prog, "app", "dmdp.py"), "w").write("# old")

        bat = stage_zip_update(zip_path, prog, "1.2.0")
        new_dir = os.path.join(prog, ".update", "new")
        check("解压后 DMDP.exe 在", os.path.isfile(os.path.join(new_dir, "DMDP.exe")))
        check("顶层目录被剥掉", not os.path.isdir(os.path.join(new_dir, "DMDP-portable")))
        check("runtime 完整", os.path.isdir(os.path.join(new_dir, "runtime")))
        check("app 完整", os.path.isdir(os.path.join(new_dir, "app")))
        check("apply.cmd 已生成", os.path.isfile(bat))

        bat_text = open(bat, encoding="utf-8", newline="").read()
        check("脚本用 robocopy 镜像 runtime", "/MIR" in bat_text and "runtime" in bat_text)
        check("脚本会等待进程退出", "tasklist" in bat_text and "goto wait" in bat_text)
        check("脚本会重启程序", "start" in bat_text)
        check("脚本不碰 .minecraft", ".minecraft" not in bat_text)
        check("脚本没有整目录删除", "rd /s" not in bat_text.lower())
        check("绿色版布局不写 VERSION", "rem VERSION" in bat_text)
        check("CRLF 行尾", "\r\n" in bat_text)

        # 5) 单文件版布局：外层 exe + 内层 DMDP\
        outer = os.path.join(tmp, "outer")
        inner = os.path.join(outer, "DMDP")
        os.makedirs(inner)
        open(os.path.join(outer, "DMDP.exe"), "wb").write(b"MZouter")
        bat2 = stage_zip_update(zip_path, inner, "1.2.0")
        check("单文件版布局会写 VERSION",
              "VERSION" in open(bat2, encoding="utf-8", newline="").read())
        shutil.rmtree(os.path.join(inner, ".update"), ignore_errors=True)

        # 6) 模拟 robocopy /MIR 的替换结果：只改程序文件，存档保持
        def mirror(src, dst):
            if os.path.isdir(dst):
                for name in os.listdir(dst):
                    p = os.path.join(dst, name)
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
            os.makedirs(dst, exist_ok=True)
            for name in os.listdir(src):
                a = os.path.join(src, name)
                b = os.path.join(dst, name)
                if os.path.isdir(a):
                    mirror(a, b)
                else:
                    shutil.copyfile(a, b)

        mirror(os.path.join(new_dir, "runtime"), os.path.join(prog, "runtime"))
        mirror(os.path.join(new_dir, "app"), os.path.join(prog, "app"))
        shutil.copyfile(os.path.join(new_dir, "DMDP.exe"), os.path.join(prog, "DMDP.exe"))
        check("runtime 已升级", os.path.isfile(os.path.join(prog, "runtime", "python312.dll")))
        check("runtime 旧文件被清", not os.path.exists(os.path.join(prog, "runtime", "old-only.dll")))
        check("app 已升级", open(os.path.join(prog, "app", "dmdp.py")).read() == "# fake")
        check(".minecraft 存档还在",
              os.path.isfile(os.path.join(prog, ".minecraft", "saves", "world.dat")))

        # 7) 收尾清理
        cleanup_update_leftovers_in(prog)
        check(".update 已清理", not os.path.isdir(os.path.join(prog, ".update")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print(f"[update-selftest] 失败 {len(fails)} 项：" + "; ".join(fails), flush=True)
        return 1
    print("[update-selftest] 全部通过", flush=True)
    return 0


def cleanup_update_leftovers_in(base_dir: str):
    """收尾指定目录下的 .update 与 *.old（自检用；正式入口见 cleanup_update_leftovers）"""
    import shutil
    upd = os.path.join(base_dir, ".update")
    if os.path.isdir(upd):
        for name in os.listdir(upd):
            path = os.path.join(upd, name)
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
            except OSError:
                pass
        try:
            os.rmdir(upd)
        except OSError:
            pass


def cleanup_update_leftovers():
    """启动时收尾：删掉自更新留下的 DMDP.exe.old 和 .update 临时目录"""
    try:
        exe = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) else None
        if exe:
            old = exe + ".old"
            if os.path.isfile(old):
                try:
                    os.remove(old)
                except OSError:
                    pass
        upd = os.path.join(app_dir(), ".update")
        if os.path.isdir(upd):
            import shutil
            for name in os.listdir(upd):
                path = os.path.join(upd, name)
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        os.remove(path)
                except OSError:
                    pass
            try:
                os.rmdir(upd)
            except OSError:
                pass
    except Exception:
        pass


def main():
    cleanup_update_leftovers()
    if "--selftest" in sys.argv:
        sys.exit(selftest())

    if "--find-pcl" in sys.argv:
        dirs = find_pcl2_dirs(max_depth=3, time_budget=15.0)
        for d in dirs:
            print(f"{d}\t{pcl_exe_in_dir(d)}")
        sys.exit(0 if dirs else 2)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    # 设置 Fusion 风格 + 自定义调色板
    app.setStyle(QStyleFactory.create("Fusion"))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLOR_BG_WHITE))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLOR_BG_WHITE))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLOR_TEXT_PRIMARY))
    app.setPalette(palette)

    # 设置默认字体
    font = QFont()
    font.setFamily("Microsoft YaHei" if sys.platform == "win32" else "PingFang SC")
    font.setPointSize(9)
    app.setFont(font)

    window = DMDPWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
