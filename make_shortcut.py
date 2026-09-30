# -*- coding: utf-8 -*-
"""给实时播放器建 Windows 快捷方式 —— 双击不经过 cmd，彻底没有黑窗闪烁。

为什么要脚本生成而不是手建：
  快捷方式必须指向 pythonw.exe、并把 live_gui.py 当**参数**传，
  还要设"起始位置"，手建容易漏一样。重跑本脚本 = 重建/修复快捷方式。

★ 2026-09-30：pythonw.exe 改用本目录 .venv（自带），找不到再回退系统 Python。

    <本目录 .venv>\python.exe make_shortcut.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GUI = ROOT / "live_gui.py"
LNK = ROOT / "实时播放器.lnk"
ICO = ROOT / "player.ico"


def _pick_pythonw() -> Path:
    """.venv 的 pythonw 优先，回退系统 Python（含 PySide6 的那个）。"""
    cands = [ROOT / ".venv" / "pythonw.exe"]
    la = os.environ.get("LOCALAPPDATA", "")
    cands += [Path(la) / "Programs" / "Python" / "Python310" / "pythonw.exe",
              Path(la) / "Programs" / "Python" / "Python313" / "pythonw.exe"]
    for c in cands:
        if c.is_file():
            return c
    return cands[0]


PYW = _pick_pythonw()


def make_icon() -> Path | None:
    """画一个图标：深色圆角底 + 青色播放三角 + 右下角的放大箭头。

    （不如让快捷方式顶着 Python 的图标 —— 它是个播放器，不是脚本。）
    """
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    s = 256
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((10, 10, s - 10, s - 10), radius=54,
                        fill=(27, 27, 31, 255), outline=(62, 62, 74, 255),
                        width=6)
    d.polygon([(92, 70), (92, 186), (188, 128)], fill=(93, 202, 165, 255))
    d.line([(152, 198), (198, 198)], fill=(133, 183, 235, 255), width=13)
    d.line([(152, 198), (152, 152)], fill=(133, 183, 235, 255), width=13)
    d.line([(152, 198), (198, 152)], fill=(133, 183, 235, 255), width=13)
    try:
        img.save(ICO, sizes=[(256, 256), (64, 64), (48, 48), (32, 32),
                             (16, 16)])
        return ICO
    except Exception:
        return None


def main() -> int:
    ok = True
    if not GUI.is_file():
        print("[X] 找不到 live_gui.py：", GUI)
        ok = False
    if not PYW.is_file():
        print("[X] 找不到 pythonw.exe：", PYW)
        ok = False
    if not ok:
        return 1

    icon = make_icon()
    print("图标：", icon if icon
          else "未生成（没装 Pillow）→ 用 pythonw 默认图标")

    try:
        import win32com.client
    except ImportError:
        print("[X] 这个解释器没有 pywin32，装它：")
        print("    ", sys.executable, "-m pip install pywin32")
        return 1

    shell = win32com.client.Dispatch("WScript.Shell")
    lnk = shell.CreateShortCut(str(LNK))
    lnk.TargetPath = str(PYW)
    lnk.Arguments = '"' + str(GUI) + '"'          # 带引号防路径空格
    lnk.WorkingDirectory = str(ROOT)
    lnk.IconLocation = (str(icon) + ",0") if icon else (str(PYW) + ",0")
    lnk.Description = "mpv 实时超分 + 补帧 控制台"
    lnk.WindowStyle = 1
    lnk.save()

    r = shell.CreateShortCut(str(LNK))
    print()
    print("已创建：", LNK.name)
    if LNK.is_file():
        print("  文件   :", LNK, f"（{LNK.stat().st_size} 字节）")
    print("  目标   :", r.TargetPath)
    print("  参数   :", r.Arguments)
    print("  起始于 :", r.WorkingDirectory)
    print("  图标   :", r.IconLocation)
    print()
    print("双击这个快捷方式即可 —— 全程没有 cmd 参与。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
