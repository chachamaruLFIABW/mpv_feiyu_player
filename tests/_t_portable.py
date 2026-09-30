# -*- coding: utf-8 -*-
"""★ 可移植性验收：确认整个 mpv-live 目录**只靠自带 .venv 就能跑**。

验的是「拷到新电脑能不能开盖即食」，不是算法正确性。四层：

  A. .venv 自包含 —— base_prefix == prefix（不指向任何外部 Python）
  B. 依赖齐全   —— VS 核心 + mvtools 插件 + PySide6 + ffmpeg 全在 .venv 内
  C. 零外部引用 —— 源码里没有指向 C:\\Users / D:\\ComfyUI 的**硬编码**
                  绝对路径残留（注释除外）
  D. 链路能跑   —— 用 .venv 的解释器真建一次补帧图，四个引擎都出帧

用法: <本目录>\\venv\\python.exe _t_portable.py
"""
import os
import re
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
fails = []
n = 0


def ck(cond, msg, got=""):
    global n
    n += 1
    if cond:
        print(f"  [OK] {msg}" + (f"   {got}" if got else ""))
    else:
        print(f"  [X ] {msg}   {got}")
        fails.append(msg)


print("=" * 76)
print("A. .venv 自包含性")
print("=" * 76)
ck((VENV / "python.exe").is_file(), ".venv/python.exe 存在")
ck((VENV / "pyvenv.cfg").is_file() is False,
   "pyvenv.cfg 已删除（否则会指回外部 Python）")

import sysconfig                                                  # noqa: E402
ck(Path(sys.prefix) == VENV, "sys.prefix 指向 .venv", str(sys.prefix))
ck(Path(sys.base_prefix) == VENV,
   "sys.base_prefix == sys.prefix（真自包含）",
   f"{sys.base_prefix}")
_std = Path(sysconfig.get_paths()["stdlib"])
ck(VENV in _std.parents or _std.parent == VENV,
   "stdlib 在 .venv 内（不需要外部 Python）", str(_std))

print()
print("=" * 76)
print("B. 依赖齐全（全部应在 .venv 内）")
print("=" * 76)
SP = VENV / "Lib" / "site-packages"
for tag, p in [
    ("VapourSynth 核心 vsscript.dll", SP / "vapoursynth" / "vsscript.dll"),
    ("VapourSynth 核心 libvapoursynth.dll",
     SP / "vapoursynth" / "libvapoursynth.dll"),
    ("mvtools 插件（mv 引擎靠它）", SP / "vapoursynth" / "plugins" / "mvtools.dll"),
    ("vstrt 插件（rife 后端靠它）", SP / "vapoursynth" / "plugins" / "vstrt.dll"),
    ("TensorRT nvinfer", SP / "vapoursynth" / "plugins" / "vsmlrt-cuda"
     / "nvinfer_11.dll"),
    ("PySide6", SP / "PySide6" / "__init__.py"),
    ("ffmpeg", SP / "imageio_ffmpeg" / "binaries"
     / "ffmpeg-win-x86_64-v7.1.exe"),
    ("python313.dll（Scripts 下）", VENV / "Scripts" / "python313.dll"),
]:
    ok = p.is_file()
    sz = f"{p.stat().st_size/2**20:.1f} MB" if ok else "缺失"
    ck(ok, tag, sz)

print()
print("=" * 76)
print("C. 源码零外部硬编码路径")
print("=" * 76)
# ★ 只认「**主路径**」上的外部硬编码。
#   回退候选（.venv 找不到时才试的备选）是**设计意图**，不算残留 ——
#   所以这些行只要带着「回退 / 备选 / 兜底 / fallback」语义就放过。
BAD = [
    re.compile(r"C:\\Users\\[A-Za-z0-9_]+"),
    re.compile(r"D:\\ComfyUI"),
]
# 允许出现的上下文关键词（同文件内、注释里已声明这是回退）
ALLOW_NEAR = ("回退", "备选", "兜底", "fallback", "cands", "候选",
              "按可能性排序", "常见安装")
SRC = ["live_gui.py", "live_play.bat", "启动控制台.bat", "selfcheck.py",
       "make_shortcut.py", "live.vpy"]


def _read_lines(p: Path):
    """★ bat 是 **GBK** 编码的（Windows 控制台要求），别用 utf-8 硬读 ——
    否则中文关键词（"回退"）会被替换成乱码，下面的上下文判断直接失效。
    这正是本测试第一版"误报 bat 有硬编码"的原因。"""
    for enc in ("utf-8", "gbk"):
        try:
            return p.read_text(encoding=enc).splitlines()
        except UnicodeDecodeError:
            continue
    return p.read_text(encoding="utf-8", errors="replace").splitlines()


for name in SRC:
    f = ROOT / name
    if not f.is_file():
        continue
    lines = _read_lines(f)
    hits = []
    for ln, line in enumerate(lines, 1):
        s = line.strip()
        if s.startswith(("rem ", "rem", "#", "::")):
            continue                              # 注释不算
        if not any(pat.search(line) for pat in BAD):
            continue
        # 看上下 16 行有没有"这是回退候选"的声明
        ctx = "\n".join(lines[max(0, ln - 17):ln + 7])
        if any(k in ctx for k in ALLOW_NEAR):
            continue                              # 是回退候选 → 放过
        hits.append(f"L{ln}: {s[:70]}")
    ck(not hits, f"{name} 主路径无外部硬编码",
       "" if not hits else f"{len(hits)} 处 → " + hits[0])

print()
print("=" * 76)
print("D. 补帧链路能真跑（四个引擎）")
print("=" * 76)
os.environ["PATH"] = (str(SP / "vapoursynth") + os.pathsep + str(VENV)
                      + os.pathsep + os.environ.get("PATH", ""))
os.environ["PYTHONPATH"] = str(SP)
SRC_VPY = (ROOT / "live.vpy").read_text(encoding="utf-8")

import vapoursynth as vs                                          # noqa: E402
core = vs.core
ck(hasattr(core, "mv"), "core.mv 可用（mvtools 加载成功）")
ck(hasattr(core, "trt"), "core.trt 可用（TensorRT 运行时已带上）")


def build(vfi, length=40, w=640, h=480):
    for k in [k for k in os.environ if k.startswith(("LIVE_", "WV"))]:
        os.environ.pop(k, None)
    os.environ.update({
        "LIVE_VFI": vfi, "LIVE_INTERP": "1", "LIVE_UPSCALE": "0",
        "LIVE_INTERP_FPS": "48", "LIVE_ORDER": "hr", "LIVE_OUT_SCALE": "1",
        "LIVE_PLAY_MAX_H": "0", "LIVE_MATRIX": "709",
        "LIVE_OUT_FMT": "yuv420p8", "LIVE_FP16_IO": "0", "LIVE_VERBOSE": "0",
        "LIVE_STATIC_TH": "0", "LIVE_DEVICE": "0",
    })
    clip = core.std.BlankClip(width=w, height=h, length=length,
                              fpsnum=24, fpsden=1, format=vs.YUV420P8)
    g = {"__name__": "live_build", "__file__": str(ROOT / "live.vpy"),
         "video_in": clip, "video_in_dw": w, "video_in_dh": h,
         "container_fps": 24.0, "display_fps": 144.0,
         "display_res": "2560x1440", "user_data": ""}
    exec(compile(SRC_VPY, "live.vpy", "exec"), g)
    return g.get("output")


for vfi in ("mv", "dup", "blend", "rife"):
    try:
        out = build(vfi)
        f = out.get_frame(3)
        ck(True, f"{vfi}: 建图 + 出帧", f"{out.width}x{out.height} "
                                        f"{out.num_frames}帧 @ {out.fps}")
    except Exception as e:                                        # noqa: BLE001
        ck(False, f"{vfi}: 建图 + 出帧", f"{type(e).__name__}: {str(e)[:90]}")

print()
print("=" * 76)
if fails:
    print(f"✗ 失败 {len(fails)} 项：")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print(f"✓ 可移植性验收全部通过　共 {n} 项")
