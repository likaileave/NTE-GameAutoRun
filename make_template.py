#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# NTE-GameAutoRun v1.0
# Copyright (c) 2026 钻非恒久远
# SPDX-License-Identifier: MIT
# AI 声明：本文件在 AI 编程助手辅助下完成，详见 AI_DISCLOSURE.md
"""
按钮模板采集工具

用法：
    run_make_template.bat
    run_make_template.bat --window MuMu

操作步骤：
    1. 选择游戏窗口；
    2. 把游戏界面停在按钮可见的画面；
    3. 输入序号（1~4）选择要采集的按钮；
    4. 在弹出的截图上拖动鼠标框选按钮本体，按 Enter 确认；
    5. 按 r 重新截图，再采集另一个界面上的按钮。

模板保存到 templates/ 目录，文件名就是按钮名（前往.png、开始匹配.png、ESC.png、确认.png）。
同一个按钮可以采集多张，命名加下划线序号，如 前往_2.png、前往_3.png。
截图时会记录当前窗口尺寸到 templates/meta.json，主程序按窗口大小自动缩放模板。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import mss
import numpy as np
import win32gui

from game_auto import (
    BUTTONS,
    TEMPLATE_DIR,
    activate_window,
    create_sct,
    save_png,
    select_window,
    set_dpi_awareness,
)


def capture_client(sct: mss.mss, hwnd: int) -> np.ndarray:
    """截取窗口客户区。"""
    if not win32gui.IsWindow(hwnd):
        raise RuntimeError("窗口已关闭")
    if win32gui.IsIconic(hwnd):
        raise RuntimeError("窗口处于最小化状态，请先恢复窗口")
    rect = win32gui.GetClientRect(hwnd)
    width, height = rect[2], rect[3]
    if width <= 0 or height <= 0:
        raise RuntimeError("窗口客户区尺寸为 0")
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    raw = sct.grab({"left": left, "top": top, "width": width, "height": height})
    return cv2.cvtColor(np.asarray(raw), cv2.COLOR_BGRA2BGR)


def select_region(frame: np.ndarray) -> tuple[int, int, int, int] | None:
    """显示截图并让用户框选，返回原图坐标 (x, y, w, h)。"""
    height, width = frame.shape[:2]
    max_width, max_height = 1500, 820
    scale = min(1.0, max_width / width, max_height / height)
    if scale < 1.0:
        display = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        display = frame

    title = "Select button area  (drag, Enter=OK, c=Cancel)"
    try:
        x, y, w, h = cv2.selectROI(
            title, display, showCrosshair=True, fromCenter=False
        )
    finally:
        try:
            cv2.destroyWindow(title)
        except Exception:
            pass

    if w < 4 or h < 4:
        return None

    return (
        int(round(x / scale)),
        int(round(y / scale)),
        int(round(w / scale)),
        int(round(h / scale)),
    )


def print_menu() -> None:
    print()
    print("请选择要采集的按钮：")
    for index, (key, spec) in enumerate(BUTTONS.items(), 1):
        print(f"  {index}. {key:<6}（{spec.hint}）")
    print(f"  {len(BUTTONS) + 1}. 自定义名称（如 前往_2，用于多模板）")
    print("  r. 重新截图（切换游戏界面后使用）")
    print("  0. 退出")


def main(argv: list[str] | None = None) -> int:
    set_dpi_awareness()

    parser = argparse.ArgumentParser(description="从游戏窗口框选按钮，保存为识别模板")
    parser.add_argument("--window", "-w", help="窗口标题关键字；不填则交互选择")
    args = parser.parse_args(argv)

    print("=" * 62)
    print("按钮模板采集工具")
    print("=" * 62)

    window = select_window(args.window)
    if window is None:
        print("已取消")
        return 0

    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n目标窗口：{window.title}（{window.width}x{window.height}）")
    print("提示：请保持游戏窗口可见，并把界面停在按钮出现的画面。")

    with create_sct() as sct:
        frame: np.ndarray | None = None
        while True:
            if frame is None:
                print("\n正在把游戏窗口切到前台，请稍候...")
                activate_window(window.hwnd)
                time.sleep(0.6)
                try:
                    frame = capture_client(sct, window.hwnd)
                except Exception as exc:
                    print(f"\n截图失败：{exc}")
                    return 1
                rect = win32gui.GetClientRect(window.hwnd)
                TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
                (TEMPLATE_DIR / "meta.json").write_text(
                    json.dumps({"width": rect[2], "height": rect[3]}),
                    encoding="utf-8",
                )
                print(f"\n截图完成：{frame.shape[1]} x {frame.shape[0]}")
                print("如果画面不对，请切到游戏调整后按 r 重新截图。")
                print("截图后请点回这个控制台窗口继续操作。")

            print_menu()
            choice = input("请选择：").strip().lower()

            if choice in ("0", "q", "quit"):
                print("退出。")
                return 0
            if choice == "r":
                frame = None
                continue

            names = list(BUTTONS.keys())
            if choice.isdigit() and 1 <= int(choice) <= len(names):
                name = names[int(choice) - 1]
            elif choice == str(len(names) + 1):
                name = input("输入模板名称（不含 .png，可加序号如 前往_2）：").strip()
                name = Path(name).name
                if name.lower().endswith(".png"):
                    name = name[:-4]
                if not name:
                    print("名称不能为空。")
                    continue
            else:
                print("输入无效，请重新选择。")
                continue

            print()
            print("请在弹出窗口中拖动鼠标框选按钮本体，按 Enter 确认，按 c 取消。")
            roi = select_region(frame)
            if roi is None:
                print("已取消框选。")
                continue

            x, y, w, h = roi
            crop = frame[y:y + h, x:x + w].copy()
            if w < 8 or h < 8:
                print(f"选区太小（{w}x{h}），请重新框选。")
                continue

            path = TEMPLATE_DIR / f"{name}.png"
            if path.exists():
                answer = input(f"{path.name} 已存在，覆盖？(y/N) ").strip().lower()
                if answer != "y":
                    print("已保留原文件。")
                    continue

            save_png(path, crop)
            print(f"已保存：{path}")
            print(f"模板尺寸：{w} x {h}")
            print("建议：只截按钮本体，少带会变化的背景；按钮有动画时就多采 2~3 张。")
            print("采完一个按钮后，可按 r 重新截图再采下一个。")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消")
        raise SystemExit(0)
