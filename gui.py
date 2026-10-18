#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# NTE-GameAutoRun v1.0
# Copyright (c) 2026 钻非恒久远
# SPDX-License-Identifier: MIT
# AI 声明：本文件在 AI 编程助手辅助下完成，详见 AI_DISCLOSURE.md
"""
游戏自动匹配脚本 - 图形界面

双击 NTE-GameAutoRun.exe 启动。
界面上可以：选择窗口、开始/暂停/停止、调参数、看实时日志。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import win32process

from game_auto import (
    BASE_DIR,
    BUTTONS,
    LOG_DIR,
    TEMPLATE_DIR,
    Config,
    GameBot,
    activate_window,
    check_templates,
    check_window_size,
    get_integrity_rid,
    is_admin,
    list_windows,
    set_dpi_awareness,
)
from version import __author__, __github__, __license__, __project__, __version__


SETTINGS_PATH = BASE_DIR / "gui_settings.json"


def load_gui_settings() -> dict:
    defaults = {
        "threshold": Config().threshold,
        "click_jitter": Config().click_jitter,
        "wait_after_click": Config().wait_after_click,
        "esc_interval": Config().esc_interval,
        "step_timeout": Config().step_timeout,
        "ignore_progress_full": Config().ignore_progress_full,
    }
    if not SETTINGS_PATH.exists():
        return defaults
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        for key in defaults:
            if key in data:
                defaults[key] = type(defaults[key])(data[key])
    except Exception:
        pass
    return defaults


def save_gui_settings(values: dict) -> None:
    try:
        SETTINGS_PATH.write_text(
            json.dumps(values, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logging.warning("保存参数失败：%s", exc)


class QueueHandler(logging.Handler):
    """把日志放进队列，由主线程取出显示到界面。"""

    def __init__(self, log_queue: queue.Queue) -> None:
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.log_queue.put(self.format(record))
        except Exception:
            pass


class BotGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{__project__} v{__version__}")
        self.root.geometry("1000x860")
        self.root.minsize(880, 760)

        self.log_queue: queue.Queue = queue.Queue()
        self.bot: GameBot | None = None
        self.thread: threading.Thread | None = None
        self.windows = []
        self.settings = load_gui_settings()
        self.save_after_id: str | None = None
        self.window_var = tk.StringVar()
        self.round_var = tk.StringVar(value="第 0 轮")
        self.clicks_var = tk.StringVar(value="点击 0 次")

        self._setup_logging()
        self._build_ui()
        self.refresh_windows()
        self.refresh_templates()
        self._poll_logs()
        self._update_status()
        logging.info(
            "图形界面已启动。管理员权限：%s。选择窗口后点“开始”。",
            "是" if self.admin_ok else "否",
        )

    # ------------------------------------------------------------------ #
    # 日志与界面
    # ------------------------------------------------------------------ #

    def _setup_logging(self) -> None:
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)

        formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
        queue_handler = QueueHandler(self.log_queue)
        queue_handler.setFormatter(formatter)
        root_logger.addHandler(queue_handler)

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"gui_{datetime.now():%Y%m%d_%H%M%S}.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        self.log_path = log_path

    def _build_ui(self) -> None:
        self.root.option_add("*Font", ("Microsoft YaHei", 10))
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass
        style.configure("Big.TCheckbutton", font=("Microsoft YaHei", 12))

        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        self.admin_ok = is_admin()
        if not self.admin_ok:
            warning = tk.Label(
                main,
                text="⚠ 当前不是管理员权限，点击会被 Windows 拦截。请关闭本程序，右键选择“以管理员身份运行”。",
                fg="#c0392b",
                anchor="w",
                justify="left",
                wraplength=940,
            )
            warning.pack(fill="x", pady=(0, 8))

        # 游戏窗口
        win_frame = ttk.LabelFrame(main, text="游戏窗口", padding=8)
        win_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(win_frame, text="窗口：").pack(side="left")
        self.combo = ttk.Combobox(
            win_frame, textvariable=self.window_var, state="readonly", width=56
        )
        self.combo.pack(side="left", padx=(0, 8))
        ttk.Button(win_frame, text="刷新", command=self.refresh_windows).pack(
            side="left", padx=2
        )
        ttk.Button(win_frame, text="置前", command=self.bring_front).pack(
            side="left", padx=2
        )

        ttk.Label(
            main,
            text="启动前：游戏停在「咔滋！黄金双脆」活动页面，右下角可见「前往」按钮。",
            foreground="#2d7ff9",
        ).pack(fill="x", pady=(0, 8))

        # 状态与控制
        status_frame = ttk.LabelFrame(main, text="状态", padding=8)
        status_frame.pack(fill="x", pady=(0, 8))
        self.status_label = tk.Label(
            status_frame,
            text="未启动",
            font=("Microsoft YaHei", 14, "bold"),
            fg="#909399",
        )
        self.status_label.pack(side="left", padx=(0, 18))
        ttk.Label(status_frame, textvariable=self.round_var).pack(side="left", padx=8)
        ttk.Label(status_frame, textvariable=self.clicks_var).pack(side="left", padx=8)

        self.start_btn = ttk.Button(status_frame, text="开始", command=self.start)
        self.start_btn.pack(side="right", padx=3)
        self.stop_btn = ttk.Button(
            status_frame, text="停止", command=self.stop, state="disabled"
        )
        self.stop_btn.pack(side="right", padx=3)
        self.pause_btn = ttk.Button(
            status_frame, text="暂停", command=self.toggle_pause, state="disabled"
        )
        self.pause_btn.pack(side="right", padx=3)

        # 参数
        param_frame = ttk.LabelFrame(main, text="参数", padding=8)
        param_frame.pack(fill="x", pady=(0, 8))

        ttk.Label(param_frame, text="相似度阈值").pack(side="left")
        self.threshold_var = tk.StringVar(value=f"{self.settings['threshold']:.2f}")
        ttk.Entry(param_frame, textvariable=self.threshold_var, width=6).pack(
            side="left", padx=(4, 16)
        )

        ttk.Label(param_frame, text="点击随机偏移(px)").pack(side="left")
        self.jitter_var = tk.StringVar(value=str(self.settings["click_jitter"]))
        ttk.Entry(param_frame, textvariable=self.jitter_var, width=4).pack(
            side="left", padx=(4, 16)
        )

        ttk.Label(param_frame, text="点击后等待(秒)").pack(side="left")
        self.wait_var = tk.StringVar(value=f"{self.settings['wait_after_click']:.1f}")
        ttk.Entry(param_frame, textvariable=self.wait_var, width=5).pack(
            side="left", padx=(4, 16)
        )

        ttk.Label(param_frame, text="ESC 检测间隔(秒)").pack(side="left")
        self.esc_var = tk.StringVar(value=f"{self.settings['esc_interval']:.1f}")
        ttk.Entry(param_frame, textvariable=self.esc_var, width=5).pack(
            side="left", padx=(4, 16)
        )

        ttk.Label(param_frame, text="卡住超时(秒)").pack(side="left")
        self.step_var = tk.StringVar(value=f"{self.settings['step_timeout']:.0f}")
        ttk.Entry(param_frame, textvariable=self.step_var, width=5).pack(
            side="left", padx=(4, 16)
        )

        for var in (
            self.threshold_var,
            self.jitter_var,
            self.wait_var,
            self.esc_var,
            self.step_var,
        ):
            var.trace_add("write", self._schedule_save)

        self.ignore_progress_var = tk.BooleanVar(
            value=bool(self.settings.get("ignore_progress_full", False))
        )
        self.ignore_progress_var.trace_add("write", self._schedule_save)
        ttk.Checkbutton(
            main,
            text="活动奖励已满仍继续运行",
            variable=self.ignore_progress_var,
            style="Big.TCheckbutton",
        ).pack(anchor="w", pady=(0, 10))

        # 模板
        tpl_frame = ttk.LabelFrame(main, text="模板", padding=8)
        tpl_frame.pack(fill="x", pady=(0, 8))
        self.template_vars: dict[str, tk.StringVar] = {}
        self.template_labels: dict[str, tk.Label] = {}
        for key in BUTTONS:
            ttk.Label(tpl_frame, text=key).pack(side="left")
            var = tk.StringVar(value="…")
            label = tk.Label(
                tpl_frame, textvariable=var, font=("Microsoft YaHei", 10, "bold")
            )
            label.pack(side="left", padx=(3, 14))
            self.template_vars[key] = var
            self.template_labels[key] = label
        ttk.Button(tpl_frame, text="采集模板", command=self.open_template_tool).pack(
            side="right"
        )
        ttk.Button(tpl_frame, text="刷新", command=self.refresh_templates).pack(
            side="right", padx=4
        )

        # 日志
        log_frame = ttk.LabelFrame(main, text="日志", padding=4)
        log_frame.pack(fill="both", expand=True)
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=16,
            wrap="word",
            font=("Consolas", 9),
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="#d4d4d4",
        )
        self.log_text.pack(fill="both", expand=True)

        footer = ttk.Frame(main)
        footer.pack(fill="x", pady=(6, 0))
        ttk.Label(
            footer,
            text="F9 暂停/继续   F10 停止   鼠标左上角急停",
            foreground="#666666",
        ).pack(side="left")
        ttk.Label(
            footer,
            text=f"  {__project__} v{__version__} · MIT · © 2026 {__author__}",
            foreground="#999999",
        ).pack(side="left")
        ttk.Button(footer, text="关于", command=self.show_about).pack(side="right")

    # ------------------------------------------------------------------ #
    # 窗口与模板
    # ------------------------------------------------------------------ #

    def refresh_windows(self) -> None:
        try:
            self.windows = list_windows()
        except Exception as exc:
            logging.error("枚举窗口失败：%s", exc)
            return
        values = [f"{w.title}  ({w.width}x{w.height})" for w in self.windows]
        self.combo["values"] = values
        if not values:
            self.window_var.set("")
            return
        for index, win in enumerate(self.windows):
            if "异环" in win.title:
                self.combo.current(index)
                return
        self.combo.current(0)

    def selected_window(self):
        index = self.combo.current()
        if 0 <= index < len(self.windows):
            return self.windows[index]
        return None

    def bring_front(self) -> None:
        window = self.selected_window()
        if window is None:
            messagebox.showwarning("提示", "请先选择游戏窗口。")
            return
        activate_window(window.hwnd)
        logging.info("已把窗口置前：%s", window.title)

    def refresh_templates(self) -> None:
        for key in BUTTONS:
            exists = any(TEMPLATE_DIR.glob(f"{key}*.png"))
            self.template_vars[key].set("✓ 已采集" if exists else "✗ 缺失")
            self.template_labels[key].config(
                fg="#2e8b57" if exists else "#c0392b"
            )

    def open_template_tool(self) -> None:
        if getattr(sys, "frozen", False):
            exe = Path(sys.executable).parent / "NTE-TemplateCapture.exe"
            if exe.exists():
                subprocess.Popen([str(exe)])
            else:
                messagebox.showerror("错误", "找不到 TemplateCapture.exe。")
            return
        bat = BASE_DIR / "run_make_template.bat"
        if not bat.exists():
            messagebox.showerror("错误", f"找不到 {bat.name}")
            return
        os.startfile(str(bat))

    def show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            f"{__project__} v{__version__}\n\n"
            f"作者：{__author__}\n"
            f"许可：{__license__}\n"
            f"项目：{__github__}\n\n"
            "AI 声明：本项目代码与文档在 AI 编程助手辅助下完成，"
            "经作者实机测试与修改。详见 AI_DISCLOSURE.md。",
        )

    # ------------------------------------------------------------------ #
    # 运行控制
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        window = self.selected_window()
        if window is None:
            messagebox.showwarning("提示", "请先选择游戏窗口。")
            return

        if not self.admin_ok:
            try:
                pid = win32process.GetWindowThreadProcessId(window.hwnd)[1]
                game_rid = get_integrity_rid(pid)
                self_rid = get_integrity_rid(os.getpid())
                if game_rid and self_rid and game_rid > self_rid:
                    messagebox.showerror(
                        "权限不足",
                        "游戏以管理员权限运行，当前程序权限不足，点击会被 Windows 拦截。\n\n"
                        "请关闭本程序，右键选择“以管理员身份运行”后再试。",
                    )
                    return
            except Exception:
                pass

        templates = check_templates()
        if templates is None:
            messagebox.showerror(
                "缺少模板",
                "四个按钮模板不完整，请先点“采集模板”或运行 run_make_template.bat。",
            )
            return

        try:
            cfg = Config()
            cfg.threshold = float(self.threshold_var.get())
            cfg.click_jitter = int(self.jitter_var.get())
            cfg.wait_after_click = float(self.wait_var.get())
            cfg.esc_interval = float(self.esc_var.get())
            cfg.step_timeout = float(self.step_var.get())
            cfg.ignore_progress_full = bool(self.ignore_progress_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "阈值、偏移、等待时间、ESC 间隔、卡住超时必须是数字。")
            return
        if not 0.5 <= cfg.threshold <= 0.99:
            messagebox.showerror("参数错误", "相似度阈值需要在 0.5 ~ 0.99 之间。")
            return
        if not 0 <= cfg.click_jitter <= 20:
            messagebox.showerror("参数错误", "点击随机偏移建议在 0 ~ 20 像素之间。")
            return
        if not 0.2 <= cfg.esc_interval <= 60:
            messagebox.showerror("参数错误", "ESC 检测间隔需要在 0.2 ~ 60 秒之间。")
            return
        if not 5 <= cfg.step_timeout <= 600:
            messagebox.showerror("参数错误", "卡住超时需要在 5 ~ 600 秒之间。")
            return
        save_gui_settings(
            {
                "threshold": cfg.threshold,
                "click_jitter": cfg.click_jitter,
                "wait_after_click": cfg.wait_after_click,
                "esc_interval": cfg.esc_interval,
                "step_timeout": cfg.step_timeout,
                "ignore_progress_full": cfg.ignore_progress_full,
            }
        )

        ok, width, height = check_window_size(window.hwnd, cfg)
        if not ok:
            messagebox.showerror(
                "窗口尺寸不符",
                f"当前窗口客户区 {width}x{height}。\n"
                f"程序按 {cfg.expected_width}x{cfg.expected_height} 采集和识别"
                f"（允许 ±{cfg.size_tolerance * 100:.0f}%，窗口 / 无边框全屏均可）。\n\n"
                "请把游戏窗口调整为 1920x1080 后再开始。",
            )
            return

        self.bot = GameBot(window, cfg, templates)
        self.thread = threading.Thread(target=self._run_bot, daemon=True)
        self.thread.start()

        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal", text="暂停")
        self.stop_btn.config(state="normal")
        logging.info("===== 启动：%s =====", window.title)

    def _run_bot(self) -> None:
        try:
            self.bot.run()
        except Exception:
            logging.exception("脚本异常退出")
        finally:
            try:
                self.root.after(0, self._on_bot_stopped)
            except Exception:
                pass

    def _on_bot_stopped(self) -> None:
        self.start_btn.config(state="normal")
        self.pause_btn.config(state="disabled", text="暂停")
        self.stop_btn.config(state="disabled")

    def toggle_pause(self) -> None:
        if self.bot is None or not self.bot.running:
            return
        self.bot.paused = not self.bot.paused
        logging.info("用户点击：%s", "暂停" if self.bot.paused else "继续")

    def stop(self) -> None:
        if self.bot is not None and self.bot.running:
            self.bot.running = False
            logging.info("用户点击：停止")

    def on_close(self) -> None:
        self._save_settings()
        if self.bot is not None and self.bot.running:
            if not messagebox.askyesno("退出", "脚本正在运行，确定停止并退出吗？"):
                return
            self.bot.running = False
            if self.thread is not None:
                self.thread.join(timeout=3)
        self.root.destroy()

    # ------------------------------------------------------------------ #
    # 参数保存
    # ------------------------------------------------------------------ #

    def _current_settings(self) -> dict | None:
        try:
            return {
                "threshold": float(self.threshold_var.get()),
                "click_jitter": int(self.jitter_var.get()),
                "wait_after_click": float(self.wait_var.get()),
                "esc_interval": float(self.esc_var.get()),
                "step_timeout": float(self.step_var.get()),
                "ignore_progress_full": bool(self.ignore_progress_var.get()),
            }
        except ValueError:
            return None

    def _schedule_save(self, *_args) -> None:
        if self.save_after_id is not None:
            try:
                self.root.after_cancel(self.save_after_id)
            except Exception:
                pass
        self.save_after_id = self.root.after(800, self._save_settings)

    def _save_settings(self) -> None:
        self.save_after_id = None
        values = self._current_settings()
        if values is not None:
            save_gui_settings(values)

    # ------------------------------------------------------------------ #
    # 定时刷新
    # ------------------------------------------------------------------ #

    def _update_status(self) -> None:
        if self.bot is not None:
            if self.bot.paused:
                text, color = "已暂停", "#e6a23c"
            elif self.bot.running:
                text, color = "运行中", "#2e8b57"
            else:
                text, color = "已停止", "#909399"
            self.status_label.config(text=text, fg=color)
            self.round_var.set(f"第 {self.bot.round} 轮")
            self.clicks_var.set(f"点击 {self.bot.clicks} 次")
            self.pause_btn.config(text="继续" if self.bot.paused else "暂停")
        else:
            self.status_label.config(text="未启动", fg="#909399")
        self.root.after(400, self._update_status)

    def _poll_logs(self) -> None:
        lines = 0
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", message + "\n")
            lines += 1
        if lines:
            total = int(self.log_text.index("end-1c").split(".")[0])
            if total > 3000:
                self.log_text.delete("1.0", f"{total - 3000}.0")
            self.log_text.see("end")
        self.root.after(100, self._poll_logs)


def main() -> int:
    set_dpi_awareness()
    root = tk.Tk()
    app = BotGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        crash = BASE_DIR / "logs" / "gui_crash.log"
        crash.parent.mkdir(parents=True, exist_ok=True)
        crash.write_text(traceback.format_exc(), encoding="utf-8")
        raise
