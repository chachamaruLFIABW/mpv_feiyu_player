# -*- coding: utf-8 -*-
"""验证播放组的「全屏」小勾真的生效：真 GUI + 真 mpv，对比勾/不勾的窗口尺寸。

判据：`GetWindowRect(hwnd)`。
  · 勾上   → 应铺满屏幕 (0,0,SW,SH)
  · 不勾   → 应约等于视频分辨率（1080p 源 → 1920x1080 上下）
用法: python _t_fs_check.py
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

os.environ.pop("QT_QPA_PLATFORM", None)
ROOT = Path(os.path.abspath(__file__)).parent.parent   # tests/ 的上一级才是项目根
sys.path.insert(0, str(ROOT))
import live_gui as G                        # noqa: E402
from PySide6.QtWidgets import QApplication   # noqa: E402

u32 = ctypes.windll.user32
SW, SH = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)

app = QApplication.instance() or QApplication([])
w = G.MainWindow()
w.show()
app.processEvents()
CFG_BACKUP = G.CFG_PATH.read_text(encoding="utf-8")
w.chk_up.setChecked(False)
w.chk_ip.setChecked(False)
w._refresh_enabled()
app.processEvents()


def rect_of(tag, fullscreen, settle=7.0):
    w.chk_fs.setChecked(fullscreen)
    app.processEvents()
    w._start()
    app.processEvents()
    time.sleep(settle)
    app.processEvents()
    h = u32.FindWindowW(None, "mpv live")
    r = wintypes.RECT()
    if h:
        u32.GetWindowRect(h, ctypes.byref(r))
        got = (r.left, r.top, r.right - r.left, r.bottom - r.top)   # x,y,w,h
    else:
        got = None
    print(f"  {tag:<22} hwnd={h}  窗口(x,y,w,h)={got}")
    w._stop()
    app.processEvents()
    time.sleep(2.5)
    u32.SetCursorPos(SW // 2, SH // 2)
    return got


print(f"屏幕 {SW}x{SH}")
a = rect_of("不勾（默认）", False)
b = rect_of("勾上全屏", True)

print()
ok_a = a is not None and a[2] < SW - 100 and abs(a[3] - 1080) < 60
ok_b = b is not None and b[0] == 0 and b[1] == 0 and b[2] == SW and b[3] == SH
print(f"  不勾 → 按视频分辨率开窗（期望高≈1080）: {'OK' if ok_a else 'XX'}")
print(f"  勾上 → 铺满屏幕（期望 0,0,{SW},{SH}）      : {'OK' if ok_b else 'XX'}")

G.CFG_PATH.write_text(CFG_BACKUP, encoding="utf-8")
print("ui_config.json 已还原 ✓")
sys.exit(0 if (ok_a and ok_b) else 1)
