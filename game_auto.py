#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# NTE-GameAutoRun v1.2
# Copyright (c) 2026 钻非恒久远
# SPDX-License-Identifier: MIT
# AI 声明：本文件在 AI 编程助手辅助下完成，详见 AI_DISCLOSURE.md
"""
游戏自动匹配脚本 —— 指定窗口 + 模板识别 + 自动点击

流程（与需求一一对应）：
    1. 右下角「前往」        出现即点击
    2. 等 3 秒
       右下角「开始匹配」    出现即点击
    3. 每 3 秒识别一次左上角「ESC」，出现即点击
    4. 等 3 秒
       中间区域「确认」      出现即点击
    5. 等 3 秒
       右下角「前往」        识别到 → 本轮结束（只识别，不点击）
       没有出现 → 重走：每 3 秒识别「ESC」→ 点击
                  → 等 3 秒 → 中间「确认」→ 点击
                  → 等 3 秒 → 再识别「前往」→ 本轮结束
    6. 回到第 1 步，点击「前往」开始下一轮

热键：
    F9   暂停 / 继续
    F10  停止
    鼠标移到屏幕左上角（0,0 附近）也可紧急停止
    Ctrl+C 停止

运行：
    run.bat
    run.bat --window MuMu          # 按标题关键字选窗口
    run.bat --debug                # 保存未命中截图，方便调阈值
    run.bat --dry-run --once       # 只识别不点击，跑一轮看看
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import math
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from version import __author__, __github__, __license__, __project__, __version__

try:
    import cv2
    import mss
    import numpy as np
    import win32api
    import win32con
    import win32gui
    import win32process
    import win32security
except ImportError as exc:  # pragma: no cover - 用户环境问题
    print(f"缺少依赖：{exc}")
    print(r"请先运行 setup.bat，或执行：.venv\Scripts\python.exe -m pip install -r requirements.txt")
    raise SystemExit(1)


# --------------------------------------------------------------------------- #
# 路径与常量
# --------------------------------------------------------------------------- #

if getattr(sys, "frozen", False):
    # PyInstaller 打包后，配置/模板/日志放在 exe 同目录
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

TEMPLATE_DIR = BASE_DIR / "templates"
TEMPLATE_META = TEMPLATE_DIR / "meta.json"
LOG_DIR = BASE_DIR / "logs"
DEBUG_DIR = BASE_DIR / "debug"
CONFIG_PATH = BASE_DIR / "config.json"

VK_F9 = 0x78
VK_F10 = 0x79

log = logging.getLogger("game_auto")


def set_dpi_awareness() -> None:
    """让进程按物理像素工作，避免 125%/150% 缩放下坐标错位。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def get_integrity_rid(pid: int) -> int | None:
    """读取进程完整性级别（8192=普通，12288=管理员，16384=系统）。"""
    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        try:
            token = win32security.OpenProcessToken(handle, win32security.TOKEN_QUERY)
            try:
                sid, _ = win32security.GetTokenInformation(
                    token, win32security.TokenIntegrityLevel
                )
                return int(win32security.ConvertSidToStringSid(sid).rsplit("-", 1)[-1])
            finally:
                token.Close()
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return None


def fmt_duration(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} 小时 {m} 分 {s} 秒"
    if m:
        return f"{m} 分 {s} 秒"
    return f"{s} 秒"


# --------------------------------------------------------------------------- #
# 窗口枚举与选择
# --------------------------------------------------------------------------- #

@dataclass
class WindowInfo:
    hwnd: int
    title: str
    cls: str
    width: int
    height: int


def list_windows() -> list[WindowInfo]:
    """列出所有可见、有标题、尺寸正常的顶层窗口。"""
    windows: list[WindowInfo] = []

    def _enum(hwnd, _extra):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return True
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if ex_style & win32con.WS_EX_TOOLWINDOW:
                return True
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width, height = right - left, bottom - top
            if width < 320 or height < 240:
                return True
            windows.append(
                WindowInfo(hwnd, title, win32gui.GetClassName(hwnd), width, height)
            )
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_enum, None)
    return windows


def select_window(keyword: str | None = None) -> WindowInfo | None:
    """按关键字选择窗口；没有关键字或匹配多个时交互选择。"""
    windows = list_windows()
    if not windows:
        print("没有找到可见窗口。请先打开游戏。")
        return None

    if keyword:
        matched = [w for w in windows if keyword.lower() in w.title.lower()]
        if len(matched) == 1:
            return matched[0]
        if matched:
            windows = matched
            print(f"\n标题包含 “{keyword}” 的窗口有 {len(matched)} 个，请选择：")
        else:
            print(f"\n没有标题包含 “{keyword}” 的窗口，下面是全部窗口：")

    print()
    print(f"{'序号':<6}{'尺寸':<14}窗口标题")
    print("-" * 96)
    for index, win in enumerate(windows, 1):
        title = win.title if len(win.title) <= 60 else win.title[:57] + "..."
        print(f"[{index:>3}]  {win.width:>4}x{win.height:<6}  {title}")
    print("-" * 96)

    while True:
        raw = input("请输入窗口序号（q 退出）：").strip()
        if raw.lower() in ("q", "quit", "exit"):
            return None
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(windows):
                return windows[index - 1]
        print("输入无效，请重新输入序号。")


def activate_window(hwnd: int) -> None:
    """把窗口恢复到前台。"""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.3)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        try:
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 图片读写（兼容中文路径）
# --------------------------------------------------------------------------- #

def imread_unicode(path: Path) -> np.ndarray | None:
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def save_png(path: Path, image: np.ndarray) -> None:
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError(f"PNG 编码失败：{path}")
    buffer.tofile(str(path))


_ocr_engine = None


def _get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def is_progress_full(
    frame: np.ndarray, region: tuple[float, float, float, float]
) -> bool | None:
    """
    用 OCR 读取“已获得 X/Y”的完整数字，比较 X 与 Y。
    返回 True=已满，False=未满，None=识别失败。
    """
    height, width = frame.shape[:2]
    left, top, right, bottom = region
    pad_x = max(4, int(width * 0.01))
    pad_y = max(2, int(height * 0.006))
    x1 = max(0, int(left * width) - pad_x)
    y1 = max(0, int(top * height) - pad_y)
    x2 = min(width, int(right * width) + pad_x)
    y2 = min(height, int(bottom * height) + pad_y)
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)
    binary = 255 - binary  # 白底黑字，OCR 更稳
    binary = cv2.resize(binary, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_NEAREST)
    try:
        result, _ = _get_ocr_engine()(
            binary, use_det=False, use_cls=False, use_rec=True
        )
    except Exception as exc:
        log.debug("OCR 失败：%s", exc)
        return None

    texts = [str(item[0]) for item in result] if result else []
    text = " ".join(texts).replace(" ", "")
    log.debug("进度 OCR：%s", text)

    match = re.search(r"(\d[\d,]*)/(\d[\d,]*)", text)
    if not match:
        return None
    current_text, total_text = match.group(1), match.group(2)
    current = int(current_text.replace(",", ""))
    total = int(total_text.replace(",", ""))
    if total <= 0:
        return None
    log.info("任务进度：%s / %s", current_text, total_text)
    return current >= total


def create_sct():
    """创建 mss 截图对象，兼容新旧版本。"""
    factory = getattr(mss, "MSS", None)
    if factory is not None:
        try:
            return factory()
        except TypeError:
            pass
    return mss.mss()


# --------------------------------------------------------------------------- #
# 按钮、区域与配置
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ButtonSpec:
    key: str
    region: str
    hint: str
    required: bool = True
    threshold: float | None = None


BUTTONS: dict[str, ButtonSpec] = {
    "前往": ButtonSpec("前往", "bottom_right", "右下角"),
    "开始匹配": ButtonSpec("开始匹配", "bottom_right", "右下角"),
    # ESC 是半透明按钮，背景不固定，单独放宽阈值
    "ESC": ButtonSpec("ESC", "top_left", "左上角", threshold=0.78),
    "确认": ButtonSpec("确认", "center_right", "中间偏右（避开取消）"),
}

# 区域用窗口客户区的比例表示：(left, top, right, bottom)，取值 0~1
DEFAULT_REGIONS: dict[str, tuple[float, float, float, float]] = {
    "bottom_right": (0.50, 0.50, 1.00, 1.00),   # 右下象限
    "top_left": (0.00, 0.00, 0.35, 0.30),       # 左上角
    "center": (0.20, 0.20, 0.80, 0.80),         # 中间区域
    # 确认按钮在弹窗右侧；只搜右半，避开左边的“取消”
    "center_right": (0.50, 0.20, 0.85, 0.80),
    # 右下角“已获得 X/Y”的任务进度数字区域（只取黑色条内部）
    "progress": (0.858, 0.831, 0.965, 0.864),
}


@dataclass
class Config:
    threshold: float = 0.85          # 模板相似度阈值
    poll_interval: float = 0.30      # 查找按钮的轮询间隔（秒）
    wait_after_click: float = 1.0    # 每次点击后的等待（秒）
    esc_interval: float = 5.0        # ESC 检测间隔（秒）
    esc_timeout: float = 0.0         # ESC 最多等多久，0 = 一直等
    step_timeout: float = 30.0       # 单步卡住超时（秒），超时进入特殊轮；0 = 不限
    special_interval: float = 1.0    # 特殊轮快速扫描的间隔（秒）
    confirm_timeout: float = 3.0     # 点 ESC 后等「确认」出现的时长，超时则重按 ESC
    finish_timeout: float = 90.0     # 收尾阶段最多等多久（覆盖加载/结算），超时重开一轮
    round_gap: float = 2.0           # 一轮结束到下一轮之间的间隔（秒）
    max_rounds: int = 0              # 最多跑几轮，0 = 无限
    click_mode: str = "front"        # front = 前台鼠标点击，message = 后台消息点击
    restore_cursor: bool = False     # False = 点击后鼠标留在按钮上
    click_jitter: int = 8            # 点击位置随机偏移的最大像素（太大可能点出按钮）
    debug_save: bool = False         # 保存未命中截图
    enable_failsafe: bool = True     # 鼠标移到左上角急停
    check_progress: bool = True      # 每轮开始时检查“已获得 X/Y”是否已满
    ignore_progress_full: bool = False  # 进度已满也继续（测试用）
    expected_width: int = 1920       # 声明支持的游戏客户区宽度
    expected_height: int = 1080      # 声明支持的游戏客户区高度
    size_tolerance: float = 0.1      # 窗口尺寸容差（±10%）
    regions: dict[str, tuple[float, float, float, float]] = field(
        default_factory=lambda: dict(DEFAULT_REGIONS)
    )


def validate_config(cfg: Config) -> None:
    if not 0.5 <= cfg.threshold <= 0.99:
        raise ValueError("threshold 需要在 0.5 ~ 0.99 之间")
    if cfg.poll_interval < 0.05:
        raise ValueError("poll_interval 不能小于 0.05 秒")
    if cfg.wait_after_click < 0 or cfg.esc_interval < 0 or cfg.round_gap < 0:
        raise ValueError("等待时间不能为负数")
    if cfg.finish_timeout < 0 or cfg.confirm_timeout < 0 or cfg.step_timeout < 0:
        raise ValueError("等待时间不能为负数")
    if cfg.special_interval < 0.1:
        raise ValueError("special_interval 不能小于 0.1 秒")
    if cfg.max_rounds < 0:
        raise ValueError("max_rounds 不能为负数")
    if cfg.click_jitter < 0:
        raise ValueError("click_jitter 不能为负数")
    if cfg.expected_width <= 0 or cfg.expected_height <= 0:
        raise ValueError("expected_width/expected_height 必须是正数")
    if not 0 <= cfg.size_tolerance <= 0.5:
        raise ValueError("size_tolerance 需要在 0 ~ 0.5 之间")
    if cfg.click_mode not in ("front", "message"):
        raise ValueError("click_mode 只能是 front 或 message")
    for name, region in cfg.regions.items():
        if len(region) != 4:
            raise ValueError(f"区域 {name} 需要 4 个数字")
        left, top, right, bottom = region
        if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
            raise ValueError(f"区域 {name} 的比例不合法：{region}")


def load_config(path: Path) -> Config:
    cfg = Config()
    if not path.exists():
        return cfg

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"配置文件读取失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("配置文件顶层必须是 JSON 对象")

    regions = data.pop("regions", None)
    if regions is not None:
        if not isinstance(regions, dict):
            raise ValueError("regions 必须是对象")
        for name, value in regions.items():
            if name not in DEFAULT_REGIONS:
                log.warning("配置文件中的未知区域 “%s” 已忽略", name)
                continue
            if not isinstance(value, (list, tuple)) or len(value) != 4:
                raise ValueError(f"区域 {name} 需要 4 个数字")
            cfg.regions[name] = tuple(float(v) for v in value)

    for key, value in data.items():
        if not hasattr(cfg, key):
            log.warning("配置文件中的未知项 “%s” 已忽略", key)
            continue
        default = getattr(cfg, key)
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f"配置项 {key} 需要 true / false")
        elif isinstance(default, int):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"配置项 {key} 需要整数")
            value = int(value)
        elif isinstance(default, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"配置项 {key} 需要数字")
            value = float(value)
        elif isinstance(default, str):
            if not isinstance(value, str):
                raise ValueError(f"配置项 {key} 需要字符串")
        setattr(cfg, key, value)

    return cfg


def apply_cli(cfg: Config, args: argparse.Namespace) -> None:
    if args.threshold is not None:
        cfg.threshold = args.threshold
    if args.click_mode:
        cfg.click_mode = args.click_mode
    if args.restore_cursor:
        cfg.restore_cursor = True
    if args.max_rounds is not None:
        cfg.max_rounds = args.max_rounds
    if args.once:
        cfg.max_rounds = 1
    if args.debug:
        cfg.debug_save = True


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #

def setup_logging(debug: bool) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if debug:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    log_path = LOG_DIR / f"bot_{datetime.now():%Y%m%d_%H%M%S}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if debug else logging.INFO)
    console.setFormatter(formatter)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    root.addHandler(console)
    root.addHandler(file_handler)
    return log_path


# --------------------------------------------------------------------------- #
# 模板检查
# --------------------------------------------------------------------------- #

def check_window_size(hwnd: int, cfg: Config) -> tuple[bool, int, int]:
    """检查窗口客户区是否接近声明尺寸（默认 1920x1080，允许 ±10%）。"""
    rect = win32gui.GetClientRect(hwnd)
    width, height = rect[2], rect[3]
    tolerance = cfg.size_tolerance
    ok = (
        abs(width - cfg.expected_width) <= cfg.expected_width * tolerance
        and abs(height - cfg.expected_height) <= cfg.expected_height * tolerance
    )
    return ok, width, height


def load_template_meta() -> dict | None:
    """读取模板采集时的窗口尺寸，用于按当前窗口大小缩放模板。"""
    try:
        data = json.loads(TEMPLATE_META.read_text(encoding="utf-8"))
        width = int(data.get("width", 0))
        height = int(data.get("height", 0))
        if width > 0 and height > 0:
            return {"width": width, "height": height}
    except Exception:
        pass
    return None


def check_templates() -> dict[str, list[tuple[Path, np.ndarray]]] | None:
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    templates: dict[str, list[tuple[Path, np.ndarray]]] = {}
    missing: list[str] = []

    for key, spec in BUTTONS.items():
        files = sorted(
            set(TEMPLATE_DIR.glob(f"{key}.png")) | set(TEMPLATE_DIR.glob(f"{key}_*.png"))
        )
        items: list[tuple[Path, np.ndarray]] = []
        for path in files:
            image = imread_unicode(path)
            if image is None:
                log.warning("模板 %s 读取失败，已跳过", path.name)
                continue
            if image.ndim != 3:
                log.warning("模板 %s 不是彩色图片，已跳过", path.name)
                continue
            if float(image.std()) < 2.0:
                log.warning("模板 %s 几乎是纯色，匹配会不稳定，建议重新采集", path.name)
            items.append((path, image))
        if not items and spec.required:
            missing.append(key)
        templates[key] = items

    if missing:
        log.error("")
        log.error("缺少以下按钮模板：")
        for key in missing:
            log.error("  - %s.png", key)
        log.error("")
        log.error("发布包应自带 templates 目录，请确认已完整解压；缺失请重新下载。")
        log.error("模板目录：%s", TEMPLATE_DIR)
        return None
    return templates


# --------------------------------------------------------------------------- #
# 异常
# --------------------------------------------------------------------------- #

class WindowGone(RuntimeError):
    """窗口关闭、最小化或无法截图。"""


class BotStopped(Exception):
    """用户主动停止。"""


@dataclass
class Hit:
    x: int
    y: int
    score: float
    template: Path
    width: int = 0
    height: int = 0


# --------------------------------------------------------------------------- #
# 自动化主体
# --------------------------------------------------------------------------- #

class GameBot:
    def __init__(
        self,
        window: WindowInfo,
        cfg: Config,
        templates: dict[str, list[tuple[Path, np.ndarray]]],
        dry_run: bool = False,
    ) -> None:
        self.window = window
        self.hwnd = window.hwnd
        self.cfg = cfg
        self.templates = templates
        self.template_meta = load_template_meta()
        self._scaled_templates: dict[str, list[tuple[Path, np.ndarray]]] | None = None
        self._scaled_for = 0
        self.dry_run = dry_run

        self.running = True
        self.paused = False
        self.round = 0
        self.clicks = 0
        self.started_at = 0.0
        self.last_scores: dict[str, float] = {}

        self.sct = None
        self._f9_prev = False
        self._f10_prev = False
        self._fg_warn_at = 0.0
        self._debug_at: dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        if self.sct is not None:
            try:
                self.sct.close()
            except Exception:
                pass
            self.sct = None

    def run(self) -> None:
        ok, width, height = check_window_size(self.hwnd, self.cfg)
        if not ok:
            log.error(
                "窗口客户区 %dx%d，不符合 %dx%d（允许 ±%.0f%%，窗口 / 无边框全屏均可）。请调整游戏窗口后重试。",
                width,
                height,
                self.cfg.expected_width,
                self.cfg.expected_height,
                self.cfg.size_tolerance * 100,
            )
            return
        self.sct = create_sct()
        self.started_at = time.monotonic()
        self._print_banner()

        try:
            while self.running:
                if self.cfg.max_rounds and self.round >= self.cfg.max_rounds:
                    break
                self.round += 1
                log.info("")
                log.info("========== 第 %d 轮 ==========", self.round)
                try:
                    self.play_round()
                except BotStopped:
                    raise
                except WindowGone as exc:
                    log.error("窗口不可用：%s", exc)
                    break
                except Exception:
                    log.exception("本轮出现异常，5 秒后重试")
                    self.smart_sleep(5)
                else:
                    if self.running and not (
                        self.cfg.max_rounds and self.round >= self.cfg.max_rounds
                    ):
                        self.smart_sleep(self.cfg.round_gap)
        finally:
            elapsed = time.monotonic() - self.started_at
            log.info("")
            log.info("已停止：共 %d 轮，点击 %d 次，运行 %s", self.round, self.clicks, fmt_duration(elapsed))
            self.close()

    def _print_banner(self) -> None:
        try:
            rect = win32gui.GetClientRect(self.hwnd)
            client_w, client_h = rect[2], rect[3]
        except Exception:
            client_w = client_h = 0
        log.info("=" * 62)
        log.info("%s v%s", __project__, __version__)
        log.info("窗口：%s", self.window.title)
        log.info("句柄：%s   客户区：%dx%d", self.hwnd, client_w, client_h)
        log.info("阈值：%.2f   点击模式：%s", self.cfg.threshold, self.cfg.click_mode)
        loaded = "，".join(f"{k}×{len(v)}" for k, v in self.templates.items() if v)
        log.info("模板：%s", loaded or "无")
        log.info("热键：F9 暂停/继续   F10 停止   鼠标移到左上角急停")
        log.info("=" * 62)
        if client_w and client_h:
            if client_w < 640 or client_h < 360:
                log.warning("窗口客户区偏小（%dx%d），模板匹配可能不稳定", client_w, client_h)

    # ------------------------------------------------------------------ #
    # 一轮流程
    # ------------------------------------------------------------------ #

    def play_round(self) -> None:
        self.activate()
        self.smart_sleep(0.2)

        step_timeout = self.cfg.step_timeout if self.cfg.step_timeout > 0 else math.inf
        step_desc = (
            f"{self.cfg.step_timeout:.0f} 秒" if self.cfg.step_timeout > 0 else "不限时"
        )
        confirm_timeout = math.inf if self.cfg.confirm_timeout <= 0 else self.cfg.confirm_timeout

        state = "goto"
        while self.running:
            if state == "goto":
                log.info("[1/5] 等待右下角「前往」（最多 %s）", step_desc)
                result = self.goto_step()
                if result == "stop":
                    return
                if result == "stuck":
                    log.warning("「前往」卡住超过 %s，进入特殊轮", step_desc)
                    state = self.special_round()
                    continue
                self.smart_sleep(self.cfg.wait_after_click)
                state = "match"

            elif state == "match":
                log.info("[2/5] 等待右下角「开始匹配」（最多 %s）", step_desc)
                if not self.find_and_click("开始匹配", timeout=step_timeout):
                    log.warning("「开始匹配」卡住超过 %s，进入特殊轮", step_desc)
                    state = self.special_round()
                    continue
                state = "esc"

            elif state == "esc":
                log.info("[3/5] 等待左上角「ESC」")
                if not self.wait_esc():
                    state = "goto"
                    continue
                self.smart_sleep(self.cfg.wait_after_click)
                state = "confirm"

            elif state == "confirm":
                log.info("[4/5] 等待中间区域「确认」")
                if self.find_and_click("确认", timeout=confirm_timeout):
                    self.smart_sleep(self.cfg.wait_after_click)
                    state = "finish"
                    continue
                # 对手先退出时，按 ESC 后可能直接加载回活动界面，不会弹确认
                if self.locate("前往") is not None:
                    log.info("按 ESC 后直接出现「前往」，本轮结束")
                    return
                log.warning("没出现「确认」，进入收尾阶段继续等「前往」/结算「ESC」")
                state = "finish"

            elif state == "finish":
                log.info("[5/5] 收尾：同时检测「前往」和结算界面的「ESC」")
                if self.finish_round():
                    log.info("本轮完成")
                    return
                log.warning("本轮未正常收尾，重新开始")
                state = "goto"

            else:
                state = "goto"

    def goto_step(self) -> str:
        """
        第 1 步：等待「前往」。点击之前先看“已获得 X/Y”：
        已满就停止整个循环，未满才点「前往」。
        """
        start = time.monotonic()
        step_timeout = self.cfg.step_timeout if self.cfg.step_timeout > 0 else math.inf
        while self.running:
            hit = self.locate("前往")
            if hit is not None:
                if self.cfg.check_progress and not self.cfg.ignore_progress_full:
                    full = self.progress_full()
                    if full is True:
                        log.info("任务进度已满，自动停止循环")
                        self.running = False
                        return "stop"
                    if full is False:
                        log.info("任务进度未满，继续执行")
                    else:
                        log.info("任务进度读取失败，按未满继续")
                elif self.cfg.ignore_progress_full:
                    log.info("已开启“进度已满仍继续”，跳过进度检查")
                if self.click_client(hit.x, hit.y, hit.width, hit.height):
                    return "ok"
                log.warning("「前往」点击失败，稍后重试")
            if time.monotonic() - start >= step_timeout:
                log.warning("「前往」卡住超过 %.0f 秒", self.cfg.step_timeout)
                return "stuck"
            self.smart_sleep(self.cfg.poll_interval)
        return "stop"

    def progress_full(self) -> bool | None:
        try:
            frame, _, _ = self.grab()
            return is_progress_full(frame, self.cfg.regions["progress"])
        except Exception as exc:
            log.debug("读取任务进度失败：%s", exc)
            return None

    def special_round(self) -> str:
        """
        卡住后的特殊轮：快速扫描 确认 → ESC → 开始匹配 → 前往，
        每个按钮每轮只识别一次，间隔 special_interval 秒，循环直到有一个命中。
        返回命中后正常轮应该进入的步骤：
          确认 -> finish，ESC -> confirm，开始匹配 -> esc，前往 -> match。
        """
        log.warning(
            "进入特殊轮：快速扫描按钮（每个只识别一次，间隔 %.1f 秒）",
            self.cfg.special_interval,
        )
        last_log = 0.0
        while self.running:
            hit = self.locate("确认")
            if hit is not None:
                log.info("特殊轮识别到「确认」")
                if self.click_client(hit.x, hit.y, hit.width, hit.height):
                    return "finish"

            hit = self.locate("ESC")
            if hit is not None:
                log.info("特殊轮识别到「ESC」")
                if self.press_escape():
                    return "confirm"

            hit = self.locate("开始匹配")
            if hit is not None:
                log.info("特殊轮识别到「开始匹配」")
                if self.click_client(hit.x, hit.y, hit.width, hit.height):
                    return "esc"

            hit = self.locate("前往")
            if hit is not None:
                log.info("特殊轮识别到「前往」")
                if self.click_client(hit.x, hit.y, hit.width, hit.height):
                    return "match"

            now = time.monotonic()
            if now - last_log >= 10.0:
                log.info("特殊轮仍在扫描当前界面...")
                last_log = now
            self.smart_sleep(self.cfg.special_interval)
        return "goto"

    def wait_esc(self, timeout: float | None = None) -> bool:
        limit = self.cfg.esc_timeout if timeout is None else timeout
        log.info("每 %.1f 秒识别一次左上角「ESC」", self.cfg.esc_interval)
        start = time.monotonic()
        paused_total = 0.0
        last_log = 0.0

        while True:
            paused_total += self._tick()
            hit = self.locate("ESC")
            if hit is not None:
                log.info("识别到「ESC」（相似度 %.3f，位置 %d,%d）", hit.score, hit.x, hit.y)
                if self.dry_run:
                    log.info("  演练模式：不按键")
                    return True
                if self.press_escape():
                    return True
                log.warning("发送 ESC 键失败，继续尝试")
                self.smart_sleep(1.0)
                continue

            elapsed = time.monotonic() - start - paused_total
            if limit > 0 and elapsed >= limit:
                log.warning("等待「ESC」超过 %.0f 秒，结束本次等待", limit)
                return False

            now = time.monotonic()
            if now - last_log >= 10.0:
                log.info(
                    "仍在等待「ESC」...（最高相似度 %.2f，已等 %.0f 秒）",
                    self.last_scores.get("ESC", 0.0),
                    elapsed,
                )
                last_log = now
            self.smart_sleep(self.cfg.esc_interval)

    def finish_round(self) -> bool:
        """
        收尾阶段优先处理结算界面的「ESC」（自己主动退出时这里是第二次），
        没有 ESC 才看「前往」；两个都没有说明还在加载，继续等。
        这样不会把结算界面右下角的「离开」误认成「前往」。
        """
        start = time.monotonic()
        while self.running:
            if self.locate("ESC") is not None:
                log.info("识别到「ESC」，按下 ESC 并等待「确认」")
                if not self.press_escape():
                    return False
                self.smart_sleep(self.cfg.wait_after_click)
                if self.find_and_click("确认", timeout=self.cfg.confirm_timeout):
                    log.info("已点击「确认」")
                else:
                    log.warning("按 ESC 后没有出现「确认」")
                self.smart_sleep(self.cfg.wait_after_click)
                continue

            if self.locate("前往") is not None:
                log.info("识别到「前往」，本轮结束")
                return True

            if time.monotonic() - start >= self.cfg.finish_timeout:
                log.warning("收尾等待超过 %.0f 秒，重新开始", self.cfg.finish_timeout)
                return False
            self.smart_sleep(0.5)
        return False

    # ------------------------------------------------------------------ #
    # 查找与点击
    # ------------------------------------------------------------------ #

    def find_and_click(self, key: str, timeout: float | None = None, do_click: bool = True) -> bool:
        """
        timeout=None  只检测一次
        timeout=inf   一直检测直到命中
        timeout=N     N 秒内检测

        do_click=False 时只识别、不点击。
        """
        label = key
        start = time.monotonic()
        paused_total = 0.0
        last_log = 0.0
        fail_count = 0
        first = True

        while True:
            paused_total += self._tick()
            hit = self.locate(key)

            if hit is not None:
                log.info(
                    "识别到「%s」（相似度 %.3f，位置 %d,%d）",
                    label,
                    hit.score,
                    hit.x,
                    hit.y,
                )
                if not do_click:
                    return True
                if self.dry_run:
                    log.info("  演练模式：不点击")
                    return True
                if self.click_client(hit.x, hit.y, hit.width, hit.height):
                    return True

                fail_count += 1
                log.warning("「%s」点击失败（%d/5）", label, fail_count)
                if fail_count >= 5:
                    log.error("连续点击失败，请检查窗口是否被遮挡、是否需要管理员权限")
                    return False
                self.smart_sleep(1.0)
                continue

            if first:
                log.info("等待「%s」出现...", label)
                first = False
                last_log = time.monotonic()
            elif time.monotonic() - last_log >= 5.0:
                elapsed = time.monotonic() - start - paused_total
                log.info(
                    "仍在等待「%s」...（最高相似度 %.2f，已等 %.0f 秒）",
                    label,
                    self.last_scores.get(key, 0.0),
                    elapsed,
                )
                last_log = time.monotonic()

            if timeout is None:
                return False

            elapsed = time.monotonic() - start - paused_total
            if timeout != math.inf and elapsed >= timeout:
                if timeout > 0 and do_click:
                    log.warning("等待「%s」超时（%.1f 秒）", label, timeout)
                return False

            self.smart_sleep(self.cfg.poll_interval)

    def wait_for(self, key: str, timeout: float = math.inf) -> bool:
        """只识别，不点击。"""
        return self.find_and_click(key, timeout=timeout, do_click=False)

    def _ensure_scaled_templates(self, width: int) -> None:
        """按当前窗口宽度缩放模板，适配不同分辨率/窗口大小。"""
        if self._scaled_templates is not None and self._scaled_for == width:
            return
        meta = self.template_meta
        if not meta:
            self._scaled_templates = self.templates
            self._scaled_for = width
            return
        scale = width / meta["width"]
        if abs(scale - 1.0) < 0.01:
            self._scaled_templates = self.templates
        else:
            scaled: dict[str, list[tuple[Path, np.ndarray]]] = {}
            for key, items in self.templates.items():
                scaled[key] = []
                for path, template in items:
                    h, w = template.shape[:2]
                    new_w = max(8, int(round(w * scale)))
                    new_h = max(8, int(round(h * scale)))
                    resized = cv2.resize(
                        template, (new_w, new_h), interpolation=cv2.INTER_AREA
                    )
                    scaled[key].append((path, resized))
            self._scaled_templates = scaled
        self._scaled_for = width
        if abs(scale - 1.0) >= 0.01:
            log.info("窗口宽度 %d，模板按 %.3f 缩放", width, scale)

    def locate(self, key: str) -> Hit | None:
        frame, _, _ = self.grab()
        self._ensure_scaled_templates(frame.shape[1])
        frame_h, frame_w = frame.shape[:2]
        left, top, right, bottom = self.region_px(BUTTONS[key].region, frame_w, frame_h)
        left = max(0, min(left, frame_w - 1))
        top = max(0, min(top, frame_h - 1))
        right = max(left + 1, min(right, frame_w))
        bottom = max(top + 1, min(bottom, frame_h))
        roi = frame[top:bottom, left:right]

        best: tuple[float, tuple[int, int], int, int, Path] | None = None
        for path, template in self._scaled_templates[key]:
            tpl_h, tpl_w = template.shape[:2]
            if tpl_h > roi.shape[0] or tpl_w > roi.shape[1]:
                continue
            result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
            _, max_value, _, max_location = cv2.minMaxLoc(result)
            if max_value != max_value:  # NaN
                continue
            if best is None or max_value > best[0]:
                best = (float(max_value), max_location, tpl_w, tpl_h, path)

        if best is None:
            self.last_scores[key] = 0.0
            return None

        score, (match_x, match_y), tpl_w, tpl_h, path = best
        self.last_scores[key] = score

        threshold = BUTTONS[key].threshold
        if threshold is None:
            threshold = self.cfg.threshold
        if score < threshold:
            if self.cfg.debug_save:
                self._save_debug(key, roi, score)
            return None

        center_x = left + match_x + tpl_w // 2
        center_y = top + match_y + tpl_h // 2
        return Hit(center_x, center_y, score, path, tpl_w, tpl_h)

    def region_px(self, name: str, width: int, height: int) -> tuple[int, int, int, int]:
        left, top, right, bottom = self.cfg.regions[name]
        return int(left * width), int(top * height), int(right * width), int(bottom * height)

    def grab(self) -> tuple[np.ndarray, int, int]:
        if not win32gui.IsWindow(self.hwnd):
            raise WindowGone("窗口已关闭")
        if win32gui.IsIconic(self.hwnd):
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            log.info("窗口已最小化，已自动恢复")
            time.sleep(0.5)

        rect = win32gui.GetClientRect(self.hwnd)
        width, height = rect[2], rect[3]
        if width <= 0 or height <= 0:
            raise WindowGone("窗口客户区尺寸为 0")
        left, top = win32gui.ClientToScreen(self.hwnd, (0, 0))
        if self.sct is None:
            raise WindowGone("截图器未初始化")

        try:
            raw = self.sct.grab(
                {"left": left, "top": top, "width": width, "height": height}
            )
        except Exception as exc:
            raise WindowGone(f"截图失败（窗口可能被最小化）：{exc}") from exc

        frame = cv2.cvtColor(np.asarray(raw), cv2.COLOR_BGRA2BGR)
        return frame, left, top

    def press_escape(self) -> bool:
        """按键盘 ESC。设计视角下鼠标可能点不到左上角按钮，按键更可靠。"""
        try:
            win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
            time.sleep(0.05)
            win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
        except Exception as exc:
            log.warning("发送 ESC 键失败：%s（请确认脚本以管理员权限运行）", exc)
            return False
        log.info("  已按下键盘 ESC")
        return True

    def click_client(self, x: int, y: int, width: int = 0, height: int = 0) -> bool:
        """点击窗口客户区坐标 (x, y)。点击位置带小幅随机偏移，点击后鼠标留在按钮上。"""
        jitter = self.cfg.click_jitter
        if jitter > 0:
            if width > 0:
                jitter = min(jitter, max(1, width // 4))
            if height > 0:
                jitter = min(jitter, max(1, height // 4))
            x += random.randint(-jitter, jitter)
            y += random.randint(-jitter, jitter)

        if not win32gui.IsWindow(self.hwnd):
            raise WindowGone("窗口已关闭")

        rect = win32gui.GetClientRect(self.hwnd)
        if not (0 <= x < rect[2] and 0 <= y < rect[3]):
            log.warning("点击坐标 (%d,%d) 超出客户区 %dx%d", x, y, rect[2], rect[3])
            return False

        if self.cfg.click_mode == "message":
            lparam = win32api.MAKELONG(x, y)
            try:
                win32gui.PostMessage(
                    self.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam
                )
                time.sleep(0.04)
                win32gui.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, lparam)
            except Exception as exc:
                log.warning("后台点击失败：%s", exc)
                return False
            self.clicks += 1
            log.info("  已发送后台点击（客户区 %d,%d）", x, y)
            return True

        if not self._ensure_foreground():
            return False

        left, top = win32gui.ClientToScreen(self.hwnd, (0, 0))
        screen_x, screen_y = left + x, top + y
        old_pos = None
        try:
            if self.cfg.restore_cursor:
                old_pos = win32api.GetCursorPos()
            win32api.SetCursorPos((screen_x, screen_y))
            time.sleep(0.03)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.05)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.05)
        except Exception as exc:
            log.warning("鼠标点击失败：%s（如游戏以管理员运行，请以管理员身份运行本脚本）", exc)
            return False
        finally:
            if self.cfg.restore_cursor and old_pos is not None:
                try:
                    win32api.SetCursorPos(old_pos)
                except Exception:
                    pass

        self.clicks += 1
        log.info("  已点击屏幕坐标（%d,%d）", screen_x, screen_y)
        return True

    def _ensure_foreground(self) -> bool:
        try:
            foreground = win32gui.GetForegroundWindow()
            if foreground == self.hwnd:
                return True
            try:
                if win32gui.GetAncestor(foreground, win32con.GA_ROOT) == self.hwnd:
                    return True
            except Exception:
                pass
        except Exception:
            pass

        self.activate()
        time.sleep(0.25)
        try:
            foreground = win32gui.GetForegroundWindow()
            if foreground == self.hwnd:
                return True
            try:
                return win32gui.GetAncestor(foreground, win32con.GA_ROOT) == self.hwnd
            except Exception:
                return False
        except Exception:
            return False

    def activate(self) -> None:
        activate_window(self.hwnd)

    # ------------------------------------------------------------------ #
    # 热键、暂停与等待
    # ------------------------------------------------------------------ #

    def _tick(self) -> float:
        """检查热键/暂停，返回本次因暂停消耗的秒数。"""
        self.poll_hotkeys()
        if not self.running:
            raise BotStopped
        if self.paused:
            started = time.monotonic()
            self._wait_resume()
            if not self.running:
                raise BotStopped
            return time.monotonic() - started
        return 0.0

    def _wait_resume(self) -> None:
        log.info("已暂停（F9 继续 / F10 停止）")
        while self.paused:
            self.poll_hotkeys()
            if not self.running:
                raise BotStopped
            time.sleep(0.1)
        log.info("继续运行")

    def smart_sleep(self, seconds: float) -> None:
        if seconds <= 0:
            self._tick()
            return
        end = time.monotonic() + seconds
        while True:
            end += self._tick()
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def poll_hotkeys(self) -> None:
        try:
            f9 = bool(ctypes.windll.user32.GetAsyncKeyState(VK_F9) & 0x8000)
            f10 = bool(ctypes.windll.user32.GetAsyncKeyState(VK_F10) & 0x8000)
        except Exception:
            return

        if f9 and not self._f9_prev:
            self.paused = not self.paused
            log.info("F9：%s", "暂停" if self.paused else "继续")
        if f10 and not self._f10_prev:
            log.warning("F10：停止脚本")
            self.running = False
        self._f9_prev, self._f10_prev = f9, f10

        if self.cfg.enable_failsafe and self.started_at:
            try:
                x, y = win32api.GetCursorPos()
            except Exception:
                return
            if time.monotonic() - self.started_at > 2.0 and x <= 1 and y <= 1:
                log.warning("鼠标位于屏幕左上角，紧急停止")
                self.running = False

    def _save_debug(self, key: str, roi: np.ndarray, score: float) -> None:
        now = time.monotonic()
        if now - self._debug_at.get(key, 0.0) < 3.0:
            return
        self._debug_at[key] = now
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{key}_未命中_{score:.2f}_{datetime.now():%H%M%S}.png"
        try:
            save_png(DEBUG_DIR / name, roi)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 命令行与入口
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="游戏自动匹配脚本（窗口模板识别 + 自动点击）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  run.bat                         交互选择窗口\n"
            "  run.bat --window MuMu           按标题关键字选窗口\n"
            "  run.bat --debug --max-rounds 1  跑一轮并保存调试截图\n"
            "  run.bat --dry-run --once        只识别不点击\n"
        ),
    )
    parser.add_argument("--window", "-w", help="窗口标题关键字；不填则交互选择")
    parser.add_argument(
        "--version",
        action="version",
        version=f"{__project__} {__version__}",
    )
    parser.add_argument("--threshold", type=float, help="模板相似度阈值（默认 0.85）")
    parser.add_argument(
        "--click-mode",
        choices=["front", "message"],
        help="front = 前台鼠标点击（默认），message = 后台消息点击",
    )
    parser.add_argument("--restore-cursor", action="store_true", help="点击后把鼠标移回原处（默认留在按钮上）")
    parser.add_argument("--max-rounds", type=int, help="最多执行轮数，0 = 无限")
    parser.add_argument("--once", action="store_true", help="只跑一轮（等同 --max-rounds 1）")
    parser.add_argument("--debug", action="store_true", help="保存未命中截图并输出详细日志")
    parser.add_argument("--dry-run", action="store_true", help="只识别不点击")
    parser.add_argument("--list", action="store_true", help="列出可见窗口后退出")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="配置文件路径")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    set_dpi_awareness()
    args = parse_args(argv)

    if args.list:
        windows = list_windows()
        if not windows:
            print("没有找到可见窗口。")
            return 0
        print(f"{'序号':<6}{'尺寸':<14}窗口标题")
        print("-" * 96)
        for index, win in enumerate(windows, 1):
            print(f"[{index:>3}]  {win.width:>4}x{win.height:<6}  {win.title}")
        return 0

    try:
        cfg = load_config(Path(args.config))
        apply_cli(cfg, args)
        validate_config(cfg)
    except ValueError as exc:
        print(f"配置错误：{exc}")
        return 2

    log_path = setup_logging(cfg.debug_save)
    log.info("日志文件：%s", log_path)

    templates = check_templates()
    if templates is None:
        return 1

    window = select_window(args.window)
    if window is None:
        log.info("已取消")
        return 0

    log.info("已选择窗口：%s（%s）", window.title, window.hwnd)

    ok, width, height = check_window_size(window.hwnd, cfg)
    if not ok:
        log.error(
            "窗口客户区 %dx%d，不符合 %dx%d（允许 ±%.0f%%，窗口 / 无边框全屏均可）。请调整游戏窗口后重试。",
            width,
            height,
            cfg.expected_width,
            cfg.expected_height,
            cfg.size_tolerance * 100,
        )
        return 3

    # 游戏若以管理员权限运行，普通权限脚本会被 UIPI 拦住，鼠标根本动不了
    if not is_admin():
        try:
            game_pid = win32process.GetWindowThreadProcessId(window.hwnd)[1]
            game_rid = get_integrity_rid(game_pid)
            self_rid = get_integrity_rid(os.getpid())
        except Exception:
            game_rid = self_rid = None
        if game_rid and self_rid and game_rid > self_rid:
            log.error("=" * 62)
            log.error("检测到游戏以管理员权限运行，而本脚本是普通权限。")
            log.error("Windows 的 UIPI 会阻止本脚本移动鼠标、点击游戏窗口。")
            log.error("请关闭本程序，右键选择“以管理员身份运行”后再试。")
            log.error("=" * 62)
            return 2

    bot = GameBot(window, cfg, templates, dry_run=args.dry_run)
    try:
        bot.run()
    except KeyboardInterrupt:
        log.info("收到 Ctrl+C，停止")
    except BotStopped:
        pass
    except Exception:
        log.exception("脚本异常退出")
        return 1
    finally:
        bot.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
