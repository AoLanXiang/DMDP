#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DMDP 下载引擎
=============

与界面完全解耦，可单独运行（见文件末尾 `python dmdp_engine.py --plan 1.20.1`）。

下载模型（也就是"多线程到底怎么分工"的答案）：

1. **文件级并行**：先把整个版本要下的文件枚举成一张清单（客户端 jar、
   全部 libraries、全部 assets 对象、资源索引、日志配置……），再交给
   `max_workers` 个线程去抢任务 —— 一个线程一个文件，互不重叠。
2. **文件内分片**：当清单里只剩少量（尤其是 1 个）大文件时，文件级并行
   已经吃不满带宽。此时对 >= `chunk_threshold` 的文件按 `chunk_size` 切
   成若干字节区间（HTTP Range），各区间用不同连接并发下载，最后按序拼回。
3. **总连接数恒定**：文件线程与分片任务共用同一个"连接额度"
   （`threading.BoundedSemaphore(max_workers)`）。所以 8 线程时永远是
   最多 8 条连接 —— 8 个小文件就 8 条各下一个文件；1 个 300MB 的大文件
   就 8 条各下一段。不会因为分片而把连接数翻倍打爆。

其它：断点续传（分片各自续传）、失败重试、全局限速、SHA1 校验、
字节级进度与实时速度。
"""

import os
import re
import sys
import json
import time
import shutil
import hashlib
import threading
import traceback
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

import minecraft_launcher_lib as mll
from minecraft_launcher_lib import install as mll_install
from minecraft_launcher_lib import utils as mll_utils
from minecraft_launcher_lib import runtime as mll_runtime
from minecraft_launcher_lib import java_utils as mll_java
from minecraft_launcher_lib import mod_loader as mll_mod_loader
from minecraft_launcher_lib._helper import (
    parse_rule_list,
    inherit_json,
    check_path_inside_minecraft_directory,
    get_sha1_hash,
    empty,
)
from minecraft_launcher_lib.natives import get_natives, extract_natives_file

# ═══════════════════════════════════════════════════════════════
#  常量
# ═══════════════════════════════════════════════════════════════

APP_NAME = "DMDP"
APP_VERSION = "1.1.3"
USER_AGENT = f"DMDP/{APP_VERSION}"

SOURCE_BMCLAPI = "bmclapi"
SOURCE_MOJANG = "mojang"

BMCLAPI_BASE = "https://bmclapi2.bangbang93.com"
MOJANG_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
BMCLAPI_MANIFEST = f"{BMCLAPI_BASE}/mc/game/version_manifest_v2.json"

# 下载源（全局，由界面在设置变更时写入）
_download_source = SOURCE_BMCLAPI

# 分片最小粒度：低于 256KB 的片没有意义，反而增加请求开销
MIN_PART_SIZE = 256 * 1024


def set_download_source(source: str):
    global _download_source
    _download_source = source if source in (SOURCE_BMCLAPI, SOURCE_MOJANG) else SOURCE_BMCLAPI


def get_download_source() -> str:
    return _download_source


# ═══════════════════════════════════════════════════════════════
#  异常
# ═══════════════════════════════════════════════════════════════

class DownloadCancelled(Exception):
    """用户取消"""


class DownloadFailed(Exception):
    """下载失败（重试后仍失败）"""


class _NoRangeSupport(Exception):
    """服务器不支持 Range（返回 200 而不是 206）"""


class LoaderError(Exception):
    """Mod 加载器安装失败"""


# ═══════════════════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════════════════

@dataclass
class DownloadConfig:
    source: str = SOURCE_BMCLAPI
    max_workers: int = 8          # 同时下载的文件数 = 同时使用的连接总数
    chunk_size: int = 4           # 单个大文件切成几段
    chunk_threshold_mb: int = 8   # 大于多少 MB 才分片
    retries: int = 3
    speed_limit: int = 0          # MB/s，0 = 不限
    verify_sha1: bool = True
    java_path: str = ""
    auto_install_java: bool = True


@dataclass
class FileItem:
    url: str
    path: str
    size: Optional[int] = None
    sha1: Optional[str] = None
    kind: str = "file"
    label: str = ""
    optional: bool = False
    counted: bool = False

    @property
    def name(self) -> str:
        return self.label or os.path.basename(self.path)


# ═══════════════════════════════════════════════════════════════
#  BMCLAPI 地址改写
# ═══════════════════════════════════════════════════════════════

_MIRROR_PREFIXES = [
    ("https://libraries.minecraft.net", f"{BMCLAPI_BASE}/maven"),
    ("http://libraries.minecraft.net", f"{BMCLAPI_BASE}/maven"),
    ("https://maven.minecraftforge.net", f"{BMCLAPI_BASE}/maven"),
    ("http://files.minecraftforge.net/maven", f"{BMCLAPI_BASE}/maven"),
    ("https://files.minecraftforge.net/maven", f"{BMCLAPI_BASE}/maven"),
    ("https://maven.neoforged.net/releases", f"{BMCLAPI_BASE}/maven"),
    ("https://resources.download.minecraft.net", f"{BMCLAPI_BASE}/assets"),
    ("http://resources.download.minecraft.net", f"{BMCLAPI_BASE}/assets"),
    ("https://meta.fabricmc.net", f"{BMCLAPI_BASE}/fabric-meta"),
    ("https://meta.quiltmc.org", f"{BMCLAPI_BASE}/quilt-meta"),
]


def official_fallback(url: str) -> Optional[str]:
    """镜像地址对应的官方地址。镜像 503 时用它兜底。"""
    if not url.startswith(BMCLAPI_BASE):
        return None
    rest = url[len(BMCLAPI_BASE):]
    if rest.startswith("/assets/"):
        return "https://resources.download.minecraft.net/" + rest[len("/assets/"):]
    if rest.startswith("/maven/"):
        return "https://libraries.minecraft.net/" + rest[len("/maven/"):]
    return None


def rewrite_url(url: str) -> str:
    """把 Mojang / Forge / NeoForge / Fabric / Quilt 的地址改写到 BMCLAPI。

    只改写 BMCLAPI 真正有镜像的路径；其余（如 piston-meta 的 assets index、
    piston-data 的日志配置）保持官方地址，避免多一跳。
    """
    if _download_source != SOURCE_BMCLAPI or not url:
        return url
    for prefix, repl in _MIRROR_PREFIXES:
        if url.startswith(prefix):
            return repl + url[len(prefix):]
    return url


def version_json_url(version: str) -> str:
    """版本 JSON：BMCLAPI 有 /version/{id}/{id}.json 直出镜像"""
    if _download_source == SOURCE_BMCLAPI:
        return f"{BMCLAPI_BASE}/version/{version}/{version}.json"
    return ""


def client_jar_url(version: str, fallback: str) -> str:
    """客户端 jar：BMCLAPI 有 /version/{id}/client 直出镜像"""
    if _download_source == SOURCE_BMCLAPI:
        return f"{BMCLAPI_BASE}/version/{version}/client"
    return fallback


# ═══════════════════════════════════════════════════════════════
#  HTTP 小工具
# ═══════════════════════════════════════════════════════════════

def _new_session() -> requests.Session:
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({"User-Agent": USER_AGENT})
    return s


_shared_session = _new_session()


def http_get(url: str, timeout=(10, 60), retries: int = 2, **kwargs) -> requests.Response:
    """带重试的 GET（用于小文件/JSON）。"""
    last = None
    for attempt in range(retries + 1):
        try:
            r = _shared_session.get(url, timeout=timeout, **kwargs)
            if r.status_code >= 400:
                raise DownloadFailed(f"HTTP {r.status_code}: {url}")
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    raise DownloadFailed(f"{url} 请求失败: {last}")


def get_json(url: str, timeout=(10, 60), retries: int = 2) -> dict:
    return http_get(url, timeout=timeout, retries=retries).json()


def _dump_json_atomic(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _download_small(url: str, path: str, sha1: Optional[str] = None):
    """无 manager 时的小文件下载（版本 JSON / 资源索引）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    r = http_get(url, stream=True, timeout=(10, 120))
    try:
        with open(tmp, "wb") as f:
            for block in r.iter_content(256 * 1024):
                if block:
                    f.write(block)
    finally:
        r.close()
    if sha1 and get_sha1_hash(tmp) != sha1:
        os.remove(tmp)
        raise DownloadFailed(f"SHA1 校验失败: {path}")
    os.replace(tmp, path)


# ═══════════════════════════════════════════════════════════════
#  限速器（全局令牌桶，所有连接共享一个额度）
# ═══════════════════════════════════════════════════════════════

class RateLimiter:
    def __init__(self, bytes_per_second: int):
        self.rate = float(bytes_per_second)
        self.capacity = max(self.rate, 512 * 1024)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, n: int):
        if self.rate <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                wait = (n - self._tokens) / self.rate
            time.sleep(min(max(wait, 0.01), 0.5))


# ═══════════════════════════════════════════════════════════════
#  统计
# ═══════════════════════════════════════════════════════════════

class DownloadStats:
    def __init__(self):
        self.lock = threading.Lock()
        self.total_bytes = 0
        self.done_bytes = 0
        self.files_total = 0
        self.files_done = 0
        self.current_file = ""
        self.speed = 0.0
        self.stage_name = ""
        self.stage_total = 0
        self.stage_done = 0
        self.stage_start_files = 0
        self.start_time = time.time()
        self._samples = deque(maxlen=16)

    def add_total(self, n: int):
        if n and n > 0:
            with self.lock:
                self.total_bytes += int(n)

    def add_done(self, n: int):
        if n and n > 0:
            with self.lock:
                self.done_bytes += int(n)

    def set_current(self, name: str):
        with self.lock:
            self.current_file = name

    def begin_file(self):
        with self.lock:
            self.files_total += 1

    def end_file(self):
        with self.lock:
            self.files_done += 1

    def tick_speed(self):
        now = time.monotonic()
        with self.lock:
            self._samples.append((now, self.done_bytes))
            if len(self._samples) >= 2:
                t0, b0 = self._samples[0]
                t1, b1 = self._samples[-1]
                dt = t1 - t0
                self.speed = (b1 - b0) / dt if dt > 0.3 else self.speed

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "total": self.total_bytes,
                "done": self.done_bytes,
                "files_total": self.files_total,
                "files_done": self.files_done,
                "current": self.current_file,
                "speed": self.speed,
                "elapsed": time.time() - self.start_time,
                "stage": self.stage_name,
                "stage_total": self.stage_total,
                "stage_done": max(0, self.files_done - self.stage_start_files),
            }


# ═══════════════════════════════════════════════════════════════
#  下载管理器
# ═══════════════════════════════════════════════════════════════

class DownloadManager:
    def __init__(self, config: DownloadConfig,
                 on_progress: Optional[Callable[[dict], None]] = None,
                 on_status: Optional[Callable[[str], None]] = None,
                 on_log: Optional[Callable[[str], None]] = None):
        self.config = config
        self.max_workers = max(1, min(64, int(config.max_workers or 8)))
        self.chunk_size = max(1, min(16, int(config.chunk_size or 4)))
        self.threshold = max(0, int(config.chunk_threshold_mb or 0)) * 1024 * 1024
        self.retries = max(0, min(10, int(config.retries or 0)))
        self.verify = bool(config.verify_sha1)
        self.allow_runtime_download = bool(config.auto_install_java)

        self.stats = DownloadStats()
        self.stop_event = threading.Event()
        self.on_progress = on_progress
        self.on_status = on_status
        self.on_log = on_log

        self.limiter = RateLimiter(int(config.speed_limit or 0) * 1024 * 1024)
        self._budget = threading.BoundedSemaphore(self.max_workers)
        self._tls = threading.local()
        # 同一个目标文件可能被多个线程同时请求（mll 的 libraries 列表里有重复项），
        # 按路径加锁，保证只有一个线程在写。
        self._path_locks: dict = {}
        self._path_locks_guard = threading.Lock()
        # 镜像返回 429/503 时全局限速冷却，避免继续把镜像打挂
        self._cool_until = 0.0
        self._cool_lock = threading.Lock()

        # 文件级线程池：比连接数稍大，避免"大文件线程等分片"把队列堵死
        self._file_pool = ThreadPoolExecutor(
            max_workers=max(4, self.max_workers + 2), thread_name_prefix="dmdp-file")
        self._chunk_pool = ThreadPoolExecutor(
            max_workers=max(2, self.max_workers * 2), thread_name_prefix="dmdp-chunk")

        self._closed = False
        self._reporter_stop = threading.Event()
        self._reporter = threading.Thread(target=self._report_loop, daemon=True)
        self._reporter.start()

    # ── 生命周期 ──

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._reporter_stop.set()
        try:
            self._file_pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._file_pool.shutdown(wait=False)
        try:
            self._chunk_pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._chunk_pool.shutdown(wait=False)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def cancel(self):
        self.stop_event.set()
        self._status("正在取消…")

    def begin_stage(self, name: str, total_files: int = 0, count_from_done: bool = True):
        """标记当前阶段，供界面显示「本阶段进度」。"""
        with self.stats.lock:
            self.stats.stage_name = str(name)
            self.stats.stage_total = int(total_files or 0)
            self.stats.stage_start_files = self.stats.files_done

    def _status(self, text: str):
        if self.on_status:
            try:
                self.on_status(text)
            except Exception:
                pass

    def _log(self, text: str):
        if self.on_log:
            try:
                self.on_log(text)
            except Exception:
                pass

    def _report_loop(self):
        interval = 0.35
        while not self._reporter_stop.wait(interval):
            self.stats.tick_speed()
            if self.on_progress:
                try:
                    self.on_progress(self.stats.snapshot())
                except Exception:
                    pass

    # ── session ──

    def _session(self) -> requests.Session:
        s = getattr(self._tls, "session", None)
        if s is None:
            s = _new_session()
            self._tls.session = s
        return s

    def _check_stop(self):
        if self.stop_event.is_set():
            raise DownloadCancelled()

    # ── 完整性判断 ──

    def _is_complete(self, item: FileItem) -> bool:
        if not os.path.isfile(item.path):
            return False
        if item.sha1 and self.verify:
            try:
                return get_sha1_hash(item.path) == item.sha1
            except Exception:
                return False
        if item.size:
            try:
                return os.path.getsize(item.path) == item.size
            except OSError:
                return False
        return True

    def _path_lock(self, path: str) -> threading.Lock:
        key = os.path.normcase(os.path.abspath(path))
        with self._path_locks_guard:
            lock = self._path_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._path_locks[key] = lock
            return lock

    def _respect_cooldown(self):
        while True:
            with self._cool_lock:
                wait = self._cool_until - time.monotonic()
            if wait <= 0:
                return
            self._check_stop()
            time.sleep(min(wait, 1.0))

    def _note_http_error(self, code: int, headers: Optional[dict] = None):
        if code not in (429, 500, 502, 503, 504):
            return
        pause = 1.5
        if headers:
            ra = headers.get("Retry-After")
            try:
                pause = max(pause, float(ra))
            except (TypeError, ValueError):
                pass
        pause = min(pause, 10.0)
        with self._cool_lock:
            self._cool_until = max(self._cool_until, time.monotonic() + pause)

    # ── 单个文件入口 ──

    def fetch(self, item: FileItem, overwrite: bool = False,
              allow_split: bool = True) -> bool:
        """下载一个文件（同一路径串行，避免并发写坏）。"""
        if not item.path:
            return self._fetch_unlocked(item, overwrite, allow_split)
        with self._path_lock(item.path):
            return self._fetch_unlocked(item, overwrite, allow_split)

    def _fetch_unlocked(self, item: FileItem, overwrite: bool = False,
                        allow_split: bool = True) -> bool:
        self._check_stop()
        if item.path:
            os.makedirs(os.path.dirname(item.path) or ".", exist_ok=True)

        self.stats.begin_file()
        finished = False
        try:
            if not overwrite and self._is_complete(item):
                size = item.size or (os.path.getsize(item.path) if os.path.isfile(item.path) else 0)
                if not item.counted:
                    self.stats.add_total(size)
                    item.counted = True
                self.stats.add_done(size)
                finished = True
                return False

            self.stats.set_current(item.name)
            if item.size and not item.counted:
                self.stats.add_total(item.size)
                item.counted = True

            # 未知大小：先用 Range:0-0 探一下（顺带拿到总大小和分片支持情况）
            range_ok = True
            if not item.size:
                item.size, range_ok = self._probe(item.url)
                if not item.counted and item.size:
                    self.stats.add_total(item.size)
                    item.counted = True

            tmp = item.path + ".dmdp-part"
            parts: list[str] = []
            use_split = (
                allow_split
                and range_ok
                and self.chunk_size > 1
                and item.size
                and item.size >= max(self.threshold, MIN_PART_SIZE * self.chunk_size)
            )

            attempt = 0
            while attempt <= self.retries:
                self._check_stop()
                try:
                    if use_split:
                        try:
                            parts = self._fetch_split(item)
                            self._merge_parts(parts, tmp)
                            parts = []
                        except _NoRangeSupport:
                            self._log(f"{item.name}: 服务器不支持分片，改为单连接下载")
                            self._cleanup(tmp, [])
                            use_split = False
                            self._fetch_stream(item, tmp)
                    else:
                        self._fetch_stream(item, tmp)

                    if item.size and os.path.getsize(tmp) != item.size:
                        raise DownloadFailed(f"文件大小不符（期望 {item.size}）: {item.name}")

                    if item.sha1 and self.verify:
                        if get_sha1_hash(tmp) != item.sha1:
                            os.remove(tmp)
                            raise DownloadFailed(f"SHA1 不匹配，重新下载: {item.name}")

                    os.replace(tmp, item.path)
                    finished = True
                    return True
                except DownloadCancelled:
                    self._cleanup(tmp, parts)
                    raise
                except Exception as e:  # noqa: BLE001
                    self._cleanup(tmp, parts)
                    parts = []
                    if attempt >= self.retries:
                        fb = official_fallback(item.url)
                        if fb and not getattr(item, "_tried_fallback", False):
                            item._tried_fallback = True
                            item.url = fb
                            attempt = 0
                            self._log(f"{item.name}: 镜像重试失败（{e}），改用官方源")
                            continue
                        if item.optional:
                            self._log(f"可选文件跳过: {item.name} ({e})")
                            return False
                        raise DownloadFailed(f"{item.name} 下载失败: {e}") from e
                    wait = min(2.5 * (2 ** attempt), 12)
                    self._log(f"{item.name} 失败({e})，{wait:.0f}s 后第 {attempt + 1} 次重试")
                    attempt += 1
                    time.sleep(wait)
        finally:
            if finished:
                self.stats.end_file()

    def _merge_parts(self, parts: list, tmp: str):
        with open(tmp, "wb") as out:
            for p in parts:
                with open(p, "rb") as f:
                    shutil.copyfileobj(f, out, 1024 * 1024)
                try:
                    os.remove(p)
                except OSError:
                    pass

    @staticmethod
    def _cleanup(tmp: str, parts: list):
        for p in [tmp] + list(parts):
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass

    # ── 探测大小 / 分片支持 ──

    def _probe(self, url: str):
        s = self._session()
        try:
            self._respect_cooldown()
            r = s.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=(10, 40))
            try:
                if r.status_code == 206:
                    cr = r.headers.get("Content-Range", "")
                    if "/" in cr:
                        return int(cr.rsplit("/", 1)[1]), True
                    return None, True
                if r.status_code == 200:
                    total = r.headers.get("Content-Length")
                    return (int(total) if total and total.isdigit() else None), False
                self._note_http_error(r.status_code, r.headers)
                return None, False
            finally:
                r.close()
        except Exception:
            return None, True

    # ── 单连接下载（带续传） ──

    def _fetch_stream(self, item: FileItem, tmp: str):
        url = item.url
        s = self._session()
        with self._budget:
            self._check_stop()
            self._respect_cooldown()
            r = s.get(url, stream=True, timeout=(10, 60))
            try:
                if r.status_code != 200:
                    self._note_http_error(r.status_code, r.headers)
                    raise DownloadFailed(f"HTTP {r.status_code}")
                with open(tmp, "wb") as f:
                    for block in r.iter_content(256 * 1024):
                        self._check_stop()
                        if not block:
                            continue
                        self.limiter.acquire(len(block))
                        f.write(block)
                        self.stats.add_done(len(block))
            finally:
                r.close()

    # ── 分片下载 ──

    def _fetch_split(self, item: FileItem):
        size = int(item.size)
        parts_n = min(self.chunk_size, max(1, size // MIN_PART_SIZE))
        if parts_n <= 1:
            raise _NoRangeSupport()
        span = size // parts_n
        ranges = []
        for i in range(parts_n):
            start = i * span
            end = size - 1 if i == parts_n - 1 else (start + span - 1)
            ranges.append((start, end))

        part_paths = [f"{item.path}.dmdp{i}.part" for i in range(parts_n)]
        futures = [
            self._chunk_pool.submit(self._fetch_range, item, start, end, part_paths[i])
            for i, (start, end) in enumerate(ranges)
        ]
        errors = []
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        if errors:
            for f in futures:
                f.cancel()
            for p in part_paths:
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                except OSError:
                    pass
            # 服务器不支持 Range 时优先回报，便于回退单连接
            for e in errors:
                if isinstance(e, _NoRangeSupport):
                    raise e
            raise errors[0]
        return part_paths

    def _fetch_range(self, item: FileItem, start: int, end: int, part_path: str):
        expect = end - start + 1
        url = item.url
        s = self._session()

        done = os.path.getsize(part_path) if os.path.isfile(part_path) else 0
        stalls = 0
        while done < expect:
            if stalls >= 2:
                raise DownloadFailed("分片多次未推进")
            self._check_stop()
            with self._budget:
                self._check_stop()
                self._respect_cooldown()
                start_at = start + done
                r = s.get(url, headers={"Range": f"bytes={start_at}-{end}"},
                          stream=True, timeout=(10, 60))
                try:
                    if r.status_code == 200:
                        raise _NoRangeSupport()
                    if r.status_code == 416:
                        return
                    if r.status_code != 206:
                        self._note_http_error(r.status_code, r.headers)
                        raise DownloadFailed(f"分片 HTTP {r.status_code}")
                    cr = r.headers.get("Content-Range", "")
                    if cr and not cr.startswith(f"bytes {start_at}-"):
                        raise DownloadFailed(f"分片区间不匹配: {cr}")
                    before = done
                    with open(part_path, "ab") as f:
                        for block in r.iter_content(256 * 1024):
                            self._check_stop()
                            if not block:
                                continue
                            self.limiter.acquire(len(block))
                            f.write(block)
                            done += len(block)
                            self.stats.add_done(len(block))
                    stalls = stalls + 1 if done == before else 0
                finally:
                    r.close()
            if done < expect:
                time.sleep(0.2)

    # ── 批量下载 ──

    def download_all(self, items: list, overwrite: bool = False):
        # 同一路径只下一次（libraries 里常有重复声明）
        uniq: "OrderedDict[str, FileItem]" = OrderedDict()
        for it in items:
            if it.path in uniq:
                old = uniq[it.path]
                if not old.sha1 and it.sha1:
                    old.sha1 = it.sha1
                if not old.size and it.size:
                    old.size = it.size
                if not old.counted and it.counted:
                    old.counted = True
                continue
            uniq[it.path] = it

        for it in uniq.values():
            if it.size and not it.counted:
                self.stats.add_total(it.size)
                it.counted = True

        self._log(f"共 {len(uniq)} 个文件，"
                  f"{self.max_workers} 线程并发"
                  f"{f'，单文件最大 {self.chunk_size} 分片' if self.chunk_size > 1 else ''}")
        # 阶段名由调用方设定，这里不再覆盖

        futures = {self._file_pool.submit(self.fetch, it, overwrite): it for it in uniq.values()}
        first_error = None
        try:
            for f in as_completed(futures):
                try:
                    f.result()
                except DownloadCancelled:
                    self.stop_event.set()
                except Exception as e:  # noqa: BLE001
                    if first_error is None:
                        first_error = e
                    self.stop_event.set()
        finally:
            if self.stop_event.is_set():
                for f in futures:
                    f.cancel()
        if first_error is not None:
            raise first_error
        if self.stop_event.is_set():
            raise DownloadCancelled()


# ═══════════════════════════════════════════════════════════════
#  版本清单 / 安装计划
# ═══════════════════════════════════════════════════════════════

_manifest_cache: dict = {"source": None, "data": None}


def get_version_manifest() -> dict:
    src = _download_source
    if _manifest_cache["source"] == src and _manifest_cache["data"]:
        return _manifest_cache["data"]
    url = BMCLAPI_MANIFEST if src == SOURCE_BMCLAPI else MOJANG_MANIFEST
    data = get_json(url, timeout=(10, 40))
    _manifest_cache.update({"source": src, "data": data})
    return data


def list_versions() -> list:
    """返回 [{id, type, releaseTime(datetime), complianceLevel}]"""
    from datetime import datetime
    data = get_version_manifest()
    out = []
    for v in data.get("versions", []):
        try:
            rt = datetime.fromisoformat(v["releaseTime"])
        except Exception:
            rt = datetime.fromtimestamp(0)
        out.append({
            "id": v["id"],
            "type": v.get("type", "release"),
            "releaseTime": rt,
            "complianceLevel": v.get("complianceLevel", 1),
        })
    return out


def _official_version_entry(version: str) -> dict:
    for v in get_version_manifest().get("versions", []):
        if v["id"] == version:
            return v
    raise mll_install.VersionNotFound(version)


def ensure_version_json(version: str, mc_dir: str,
                        manager: Optional[DownloadManager] = None,
                        status: Optional[Callable[[str], None]] = None) -> str:
    path = os.path.join(mc_dir, "versions", version, f"{version}.json")
    if os.path.isfile(path):
        return path

    if status:
        status(f"获取 {version} 版本信息")
    if _download_source == SOURCE_BMCLAPI:
        url, sha1 = version_json_url(version), None
    else:
        entry = _official_version_entry(version)
        url, sha1 = entry["url"], entry.get("sha1")

    item = FileItem(url=url, path=path, sha1=sha1, kind="version_json", label=f"{version}.json")
    if manager:
        manager.fetch(item)
    else:
        _download_small(url, path, sha1)
    return path


def _library_items(lib: dict, version_id: str, mc_dir: str):
    """把一个 library 条目转成待下载文件 + 可能的 natives 解压任务"""
    items: list[FileItem] = []
    natives = []

    if "rules" in lib:
        try:
            if not parse_rule_list(lib["rules"], {}):
                return items, natives
        except Exception:
            pass

    downloads = lib.get("downloads") or {}
    artifact = downloads.get("artifact") or {}
    native_key = get_natives(lib)

    if artifact.get("url"):
        items.append(FileItem(
            url=artifact["url"],
            path=os.path.join(mc_dir, "libraries", artifact.get("path", "").replace("/", os.sep)),
            size=artifact.get("size"),
            sha1=artifact.get("sha1"),
            kind="library",
        ))
    elif lib.get("name") and not downloads:
        # 老版本 library 没有 downloads 字段，需要自己拼 maven 路径
        try:
            group, name, lversion = lib["name"].split(":")[0:3]
        except ValueError:
            group = name = lversion = None
        if group:
            if "@" in lversion:
                lversion, fileend = lversion.split("@", 1)
            else:
                fileend = "jar"
            base = (lib.get("url") or "https://libraries.minecraft.net").rstrip("/")
            rel = group.replace(".", "/")
            jar = f"{name}-{lversion}.{fileend}"
            items.append(FileItem(
                url=f"{base}/{rel}/{name}/{lversion}/{jar}",
                path=os.path.join(mc_dir, "libraries", *group.split("."), name, lversion, jar),
                kind="library", optional=True,
            ))

    if native_key:
        classifier = (downloads.get("classifiers") or {}).get(native_key) or {}
        try:
            group, name, lversion = lib["name"].split(":")[0:3]
            if "@" in lversion:
                lversion = lversion.split("@", 1)[0]
        except ValueError:
            group = name = lversion = None

        if group:
            if classifier.get("path"):
                native_jar = os.path.join(mc_dir, "libraries",
                                          classifier["path"].replace("/", os.sep))
            else:
                native_jar = os.path.join(mc_dir, "libraries", *group.split("."), name, lversion,
                                          f"{name}-{lversion}-{native_key}.jar")
            if classifier.get("url"):
                native_url, native_size, native_sha1 = classifier["url"], classifier.get("size"), classifier.get("sha1")
                optional = False
            else:
                base = (lib.get("url") or "https://libraries.minecraft.net").rstrip("/")
                native_url = f"{base}/{group.replace('.', '/')}/{name}/{lversion}/{name}-{lversion}-{native_key}.jar"
                native_size = native_sha1 = None
                optional = True
            items.append(FileItem(url=native_url, path=native_jar, size=native_size,
                                  sha1=native_sha1, kind="native", optional=optional))
            natives.append((native_jar, os.path.join(mc_dir, "versions", version_id, "natives"),
                            lib.get("extract") or {"exclude": []}))

    return items, natives


def _asset_items(data: dict, mc_dir: str,
                 manager: Optional[DownloadManager] = None,
                 status: Optional[Callable[[str], None]] = None):
    items: list[FileItem] = []
    asset_index = data.get("assetIndex")
    if not asset_index:
        return items

    assets_id = data.get("assets") or asset_index.get("id") or "legacy"
    index_path = os.path.join(mc_dir, "assets", "indexes", f"{assets_id}.json")
    index_item = FileItem(
        url=asset_index["url"], path=index_path,
        size=asset_index.get("size"), sha1=asset_index.get("sha1"),
        kind="asset_index", label=f"{assets_id}.json",
    )
    if manager:
        manager.fetch(index_item)
    else:
        _download_small(asset_index["url"], index_path, asset_index.get("sha1"))

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    except Exception as e:
        raise DownloadFailed(f"资源索引解析失败: {e}")

    if status:
        status("解析资源文件清单")

    seen = set()
    for obj in (index.get("objects") or {}).values():
        h = obj.get("hash")
        if not h or h in seen:
            continue
        seen.add(h)
        items.append(FileItem(
            url=f"https://resources.download.minecraft.net/{h[:2]}/{h}",
            path=os.path.join(mc_dir, "assets", "objects", h[:2], h),
            size=obj.get("size"), sha1=h, kind="asset",
        ))
    return items


def build_install_plan(version: str, mc_dir: str,
                       manager: Optional[DownloadManager] = None,
                       status: Optional[Callable[[str], None]] = None,
                       depth: int = 0):
    """返回 (items, version_json, natives_jobs)"""
    if depth > 3:
        raise DownloadFailed("版本继承层级过深")

    json_path = ensure_version_json(version, mc_dir, manager, status)
    with open(json_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    items: list[FileItem] = []
    natives: list = []

    parent = raw.get("inheritsFrom")
    if parent:
        if status:
            status(f"补全前置版本 {parent}")
        parent_items, _, parent_natives = build_install_plan(parent, mc_dir, manager, status, depth + 1)
        items += parent_items
        natives += parent_natives
        data = inherit_json(raw, mc_dir)
    else:
        data = raw

    version_id = data.get("id") or version

    if status:
        status("下载游戏资源库")
    for lib in data.get("libraries") or []:
        lib_items, lib_natives = _library_items(lib, version_id, mc_dir)
        items += lib_items
        natives += lib_natives

    if status:
        status("下载游戏资源文件")
    items += _asset_items(data, mc_dir, manager, status)

    logging_cfg = (data.get("logging") or {}).get("client") or {}
    log_file = logging_cfg.get("file") or {}
    if log_file.get("url"):
        items.append(FileItem(
            url=log_file["url"],
            path=os.path.join(mc_dir, "assets", "log_configs", log_file.get("id", "client.xml")),
            size=log_file.get("size"), sha1=log_file.get("sha1"), kind="log_config",
        ))

    client = (data.get("downloads") or {}).get("client") or {}
    if client.get("url"):
        items.append(FileItem(
            url=client_jar_url(version_id, client["url"]),
            path=os.path.join(mc_dir, "versions", version_id, f"{version_id}.jar"),
            size=client.get("size"), sha1=client.get("sha1"), kind="client_jar",
            label=f"{version_id}.jar",
        ))

    return items, data, natives


def install_version(version: str, mc_dir: str, manager: DownloadManager,
                    status: Optional[Callable[[str], None]] = None) -> dict:
    """完整安装一个原版版本：解析清单 → 并发下载 → 解压 natives。"""
    os.makedirs(mc_dir, exist_ok=True)
    plan, data, natives = build_install_plan(version, mc_dir, manager, status)
    if status:
        status("下载游戏文件")
    manager.begin_stage("下载游戏文件", len(plan))
    manager.download_all(plan)

    if natives:
        manager.begin_stage("解压 natives", len(natives))
        manager._status("解压 natives")
        for jar, dest, rules in natives:
            if os.path.isfile(jar):
                try:
                    extract_natives_file(jar, dest, rules)
                except Exception as e:  # noqa: BLE001
                    manager._log(f"natives 解压失败 {os.path.basename(jar)}: {e}")

    manager._status("安装完成")
    return data


# ═══════════════════════════════════════════════════════════════
#  Java 定位（Forge / NeoForge / Fabric 安装器都需要 Java）
# ═══════════════════════════════════════════════════════════════

def _java_major(version: str) -> int:
    try:
        v = str(version).strip()
        if v.startswith("1."):
            return int(v.split(".")[1])
        return int(re.match(r"(\d+)", v).group(1))
    except Exception:
        return 0


def _normalize_java_path(path: str) -> str:
    if not path:
        return ""
    p = os.path.expanduser(path.strip().strip('"'))
    if os.path.isdir(p):
        for name in ("java.exe", "java"):
            cand = os.path.join(p, "bin", name)
            if os.path.isfile(cand):
                return cand
        return ""
    return p if os.path.isfile(p) else ""


def find_java(version_json: dict, mc_dir: str, config: DownloadConfig,
              manager: Optional[DownloadManager] = None) -> str:
    """按优先级找一个能用于安装器的 Java，找不到返回 ""。"""
    explicit = _normalize_java_path(config.java_path)
    if explicit:
        return explicit

    java_version = version_json.get("javaVersion") or {}
    required = int(java_version.get("majorVersion") or 8)
    component = java_version.get("component")

    # 1) 已下载好的 Mojang 运行时（版本对应，最可靠）
    if component:
        try:
            exe = mll_runtime.get_executable_path(component, mc_dir)
            if exe and os.path.isfile(exe):
                return exe
        except Exception:
            pass

    # 2) 系统 Java：优先主版本刚好匹配，其次选最小可满足的更高版本
    candidates = []
    try:
        for info in mll_java.find_system_java_versions_information():
            major = _java_major(info.get("version", ""))
            path = info.get("java_path") or ""
            if os.path.isfile(path):
                candidates.append((major, path))
    except Exception:
        candidates = []
    exact = [p for m, p in candidates if m == required]
    higher = sorted([(m, p) for m, p in candidates if m > required])
    if exact:
        return exact[0]
    if higher:
        return higher[0][1]

    # 3) 按需下载 Mojang 运行时
    if component and config.auto_install_java and manager is not None:
        manager._status(f"下载 Java {required} 运行库（仅安装加载器需要）")
        try:
            mll_install.install_jvm_runtime(component, mc_dir, callback={
                "setStatus": lambda s: manager._status(str(s)),
                "setProgress": lambda v: None,
                "setMax": lambda v: None,
            })
        except Exception as e:  # noqa: BLE001
            manager._log(f"Java 运行库下载失败: {e}")
        try:
            exe = mll_runtime.get_executable_path(component, mc_dir)
            if exe and os.path.isfile(exe):
                return exe
        except Exception:
            pass

    return ""


# ═══════════════════════════════════════════════════════════════
#  Mod 加载器清单（全部走 BMCLAPI 镜像）
# ═══════════════════════════════════════════════════════════════

LOADER_NAMES = {
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "neoforge": "NeoForge",
}

_loader_cache: dict = {}


def _cache_key(*parts):
    return "|".join(str(p) for p in parts)


def list_loader_versions(loader_id: str, mc_version: str) -> list:
    """返回该 Minecraft 版本可用的加载器版本（新→旧）。"""
    key = _cache_key(loader_id, mc_version)
    if key in _loader_cache:
        return _loader_cache[key]

    result: list = []
    base = BMCLAPI_BASE if _download_source == SOURCE_BMCLAPI else ""

    if loader_id == "fabric":
        url = (f"{base}/fabric-meta/v2/versions/loader/{mc_version}"
               if base else f"https://meta.fabricmc.net/v2/versions/loader/{mc_version}")
        for e in get_json(url, timeout=(10, 40)):
            loader = e.get("loader") or {}
            if loader.get("version"):
                result.append(loader["version"])
    elif loader_id == "quilt":
        mirrored = f"{base}/quilt-meta/v3/versions/loader/{mc_version}" if base else ""
        official = f"https://meta.quiltmc.org/v3/versions/loader/{mc_version}"
        try:
            data = get_json(mirrored, timeout=(10, 40)) if mirrored else get_json(official)
        except Exception:
            # BMCLAPI 没有 per-game 的 quilt loader 接口，回退官方
            data = get_json(official, timeout=(10, 60))
        for e in data:
            loader = e.get("loader") or {}
            if loader.get("version"):
                result.append(loader["version"])
    elif loader_id == "forge":
        url = (f"{base}/maven/net/minecraftforge/forge/maven-metadata.xml"
               if base else "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml")
        text = http_get(url, timeout=(10, 60)).text
        for v in re.findall(r"<version>([^<]+)</version>", text):
            if v.startswith(f"{mc_version}-"):
                result.append(v[len(mc_version) + 1:])
        result.sort(key=lambda s: [int(x) for x in re.findall(r"\d+", s)], reverse=True)
    elif loader_id == "neoforge":
        url = (f"{base}/maven/net/neoforged/neoforge/maven-metadata.xml"
               if base else "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml")
        text = http_get(url, timeout=(10, 60)).text
        for v in re.findall(r"<version>([^<]+)</version>", text):
            m = re.match(r"(\d+)\.(\d+)\.", v)
            if not m:
                continue
            target = f"1.{m.group(1)}.{m.group(2)}"
            if target.endswith(".0"):
                target = target[:-2]
            if target == mc_version:
                result.append(v)
        result.sort(key=lambda s: [int(x) for x in re.findall(r"\d+", s)], reverse=True)
    else:
        raise ValueError(f"未知加载器: {loader_id}")

    _loader_cache[key] = result
    return result


def loader_supports(loader_id: str, mc_version: str) -> bool:
    try:
        return bool(list_loader_versions(loader_id, mc_version))
    except Exception:
        return True  # 查询失败时不武断阻止，让安装阶段报真实错误


def install_mod_loader(loader_id: str, mc_version: str, mc_dir: str,
                       loader_version: str, manager: DownloadManager,
                       version_json: dict) -> str:
    """调用 minecraft-launcher-lib 的 mod_loader 安装加载器（带镜像 + Java 定位）。"""
    java = find_java(version_json, mc_dir, manager.config, manager)
    if not java:
        raise LoaderError(
            "没有找到可用的 Java 运行环境。\n"
            "Forge / NeoForge / Fabric / Quilt 的安装器都需要 Java。\n"
            "请在「设置 → Java」里指定 java.exe，或打开「自动下载 Java 运行库」。"
        )

    manager.begin_stage(f"安装 {LOADER_NAMES.get(loader_id, loader_id)}")
    manager._status(f"安装 {LOADER_NAMES.get(loader_id, loader_id)}")
    manager._log(f"使用 Java: {java}")

    callback = {
        "setStatus": lambda s: manager._status(str(s)),
        "setProgress": lambda v: None,
        "setMax": lambda v: None,
    }
    loader = mll_mod_loader.get_mod_loader(loader_id)
    # get_mod_loader 是按需 import 的，新导入的子模块还持有未打补丁的 download_file
    install_patches(_manager_provider)
    installed_id = loader.install(
        mc_version, mc_dir,
        loader_version=loader_version or None,
        callback=callback,
        java=java,
    )
    manager._log(f"加载器已安装为版本 {installed_id}")
    # 安装器会额外生成一个同名 profile 目录（versions/fabric-loader 等），
    # PCL2 会把它当成另一个版本，导致版本列表里出现重复项，这里清掉。
    _clean_loader_leftovers(loader_id, mc_dir, installed_id, manager)
    return installed_id


# 安装器残留的 profile 目录名（不带版本号），只有正式版本 id 存在时才删除
_LOADER_LEFTOVER_DIRS = {
    "fabric": ["fabric-loader"],
    "quilt": ["quilt-loader"],
}


def _clean_loader_leftovers(loader_id: str, mc_dir: str, installed_id: str,
                            manager: Optional[DownloadManager] = None):
    names = _LOADER_LEFTOVER_DIRS.get(loader_id) or []
    if not names or not installed_id:
        return
    versions_dir = os.path.join(mc_dir, "versions")
    official_dir = os.path.join(versions_dir, installed_id)
    if not os.path.isdir(official_dir):
        return  # 正式版本目录不存在，说明名字对不上，别乱删
    for name in names:
        if name == installed_id:
            continue
        d = os.path.join(versions_dir, name)
        if not os.path.isdir(d):
            continue
        try:
            shutil.rmtree(d)
            if manager:
                manager._log(f"已清理安装器残留目录 versions/{name}")
        except Exception as e:  # noqa: BLE001
            if manager:
                manager._log(f"清理 versions/{name} 失败: {e}")


def install_fabric_api(mc_version: str, mc_dir: str, manager: DownloadManager) -> bool:
    """给 Fabric 版本装 Fabric API（mods/fabric-api-*.jar），失败不致命。"""
    try:
        api = "https://api.modrinth.com/v2/project/fabric-api/version"
        params = {
            "game_versions": json.dumps([mc_version]),
            "loaders": json.dumps(["fabric"]),
        }
        r = manager._session().get(api, params=params, timeout=15)
        if r.status_code != 200:
            return False
        data = r.json()
        if not isinstance(data, list) or not data:
            return False
        files = (data[0] or {}).get("files") or []
        if not files:
            return False
        f = files[0]
        mods_dir = os.path.join(mc_dir, "mods")
        os.makedirs(mods_dir, exist_ok=True)
        dest = os.path.join(mods_dir, f.get("filename") or "fabric-api.jar")
        manager.fetch(FileItem(url=f["url"], path=dest,
                               size=f.get("size"), sha1=None), overwrite=True)
        manager._log(f"已安装 Fabric API: {os.path.basename(dest)}")
        return True
    except Exception as e:  # noqa: BLE001
        if manager:
            manager._log(f"Fabric API 安装跳过: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
#  minecraft-launcher-lib 补丁
#  （加载器安装器、Java 运行时等内部下载同样走镜像 / 分片 / 限速）
# ═══════════════════════════════════════════════════════════════

# ── 屏蔽子进程控制台窗口 ─────────────────────────────────────
# Forge/Fabric/Quilt 安装器、Java 运行时探测在 Windows 上会弹出黑框，
# 后段一次起几十个，能把资源管理器拖到无响应。这里统一给 subprocess 加上
# CREATE_NO_WINDOW + 隐藏 STARTUPINFO，任何库调用都被覆盖。

_NO_WINDOW_PATCHED = False


def patch_subprocess_no_window():
    global _NO_WINDOW_PATCHED
    if _NO_WINDOW_PATCHED or os.name != "nt":
        return
    import subprocess as _sp

    _orig_popen = _sp.Popen

    def _popen_no_window(*args, **kwargs):
        flags = kwargs.get("creationflags", 0)
        kwargs["creationflags"] = flags | 0x08000000  # CREATE_NO_WINDOW
        if os.name == "nt":
            si = kwargs.get("startupinfo") or _sp.STARTUPINFO()
            si.dwFlags |= _sp.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            kwargs["startupinfo"] = si
        return _orig_popen(*args, **kwargs)

    _sp.Popen = _popen_no_window
    _NO_WINDOW_PATCHED = True


patch_subprocess_no_window()


_manager_provider: Optional[Callable[[], Optional[DownloadManager]]] = None
_patched_modules: set = set()

_original_download_file = mll_install.download_file
_original_install_jvm_runtime = mll_install.install_jvm_runtime


def _patched_download_file(url, path, callback={}, sha1=None,
                           lzma_compressed=False, session=None,
                           minecraft_directory=None, overwrite=False):
    url = rewrite_url(url)
    manager = _manager_provider() if _manager_provider else None
    if manager is None or lzma_compressed:
        return _original_download_file(
            url=url, path=path, callback=callback, sha1=sha1,
            lzma_compressed=lzma_compressed, session=session,
            minecraft_directory=minecraft_directory, overwrite=overwrite)

    item = FileItem(url=url, path=path, sha1=sha1 if manager.verify else None)
    return manager.fetch(item, overwrite=bool(overwrite))


def _patched_install_jvm_runtime(jvm_version, minecraft_directory, callback=None):
    manager = _manager_provider() if _manager_provider else None
    if manager is not None and not manager.allow_runtime_download:
        if callback:
            callback.get("setStatus", empty)("跳过 Java 运行库下载")
        return None
    return _original_install_jvm_runtime(jvm_version, minecraft_directory, callback)


def _patched_install_libraries(versionid, libraries, path, callback, max_workers=None):
    manager = _manager_provider() if _manager_provider else None
    if max_workers is None and manager is not None:
        max_workers = manager.max_workers
    return _original_install_libraries(versionid, libraries, path, callback, max_workers)


_original_install_libraries = mll_install.install_libraries


def install_patches(provider: Callable[[], Optional[DownloadManager]]):
    """给所有 mll 子模块打补丁（各模块各自持有 download_file 的引用）。"""
    global _manager_provider
    _manager_provider = provider

    for name, module in list(sys.modules.items()):
        if not (name == "minecraft_launcher_lib" or name.startswith("minecraft_launcher_lib.")):
            continue
        if name.endswith("._helper"):
            continue
        if name in _patched_modules:
            continue
        if hasattr(module, "download_file"):
            setattr(module, "download_file", _patched_download_file)
        if name == "minecraft_launcher_lib.install":
            setattr(module, "install_jvm_runtime", _patched_install_jvm_runtime)
            setattr(module, "install_libraries", _patched_install_libraries)
        _patched_modules.add(name)


# ═══════════════════════════════════════════════════════════════
#  命令行自检
# ═══════════════════════════════════════════════════════════════

def _cli():
    import argparse

    parser = argparse.ArgumentParser(description="DMDP 下载引擎自检")
    parser.add_argument("--plan", metavar="VERSION", help="只解析清单，不下载")
    parser.add_argument("--install", metavar="VERSION", help="真实安装一个版本")
    parser.add_argument("--dir", default=os.path.join(os.getcwd(), ".minecraft"))
    parser.add_argument("--source", default=SOURCE_BMCLAPI, choices=[SOURCE_BMCLAPI, SOURCE_MOJANG])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--chunks", type=int, default=4)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--list-loaders", metavar="LOADER",
                        choices=["fabric", "quilt", "forge", "neoforge"],
                        help="列出某加载器在指定 MC 版本下的可用版本")
    parser.add_argument("--mc", default="1.20.1", help="配合 --list-loaders 使用")
    parser.add_argument("--loader", default="", choices=["", "fabric", "quilt", "forge", "neoforge"],
                        help="安装时顺带装加载器")
    parser.add_argument("--loader-version", default="", help="自动（最新版）")
    args = parser.parse_args()

    set_download_source(args.source)

    if args.list_loaders:
        versions = list_loader_versions(args.list_loaders, args.mc)
        print(f"{args.list_loaders} @ {args.mc}: {len(versions)} 个版本")
        for v in versions[:10]:
            print("  ", v)
        return

    if args.plan:
        items, data, natives = build_install_plan(args.plan, args.dir)
        total = sum(i.size or 0 for i in items)
        known = sum(1 for i in items if i.size)
        print(f"版本 {args.plan}：{len(items)} 个文件，{known} 个有大小，"
              f"合计约 {total / 1048576:.1f} MB，natives 任务 {len(natives)} 个")
        kinds = {}
        for i in items:
            kinds[i.kind] = kinds.get(i.kind, 0) + 1
        print("分类:", kinds)
        return

    if args.install:
        cfg = DownloadConfig(max_workers=args.workers, chunk_size=args.chunks, retries=args.retries)
        manager = DownloadManager(cfg, on_status=lambda s: print(f"[status] {s}", flush=True),
                                  on_log=lambda s: print(f"[log] {s}", flush=True),
                                  on_progress=lambda p: None)
        try:
            data = install_version(args.install, args.dir, manager)
            if args.loader:
                installed = install_mod_loader(args.loader, args.install, args.dir,
                                               args.loader_version, manager, data)
                print("已安装加载器版本:", installed)
            print("安装完成")
        finally:
            manager.close()
        return

    parser.print_help()


if __name__ == "__main__":
    _cli()
