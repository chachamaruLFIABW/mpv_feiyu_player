# -*- coding: utf-8 -*-
"""对比 live.vpy 的 _play_cap_size 与 GUI 的 capped_play_size 是否一字不差。

用户踩过的坑："两处各写一份规则" → 预估值和实际跑出来的不一样。
这里用真 VS 运行时把 live.vpy 的算式抽出来跑一遍。
"""
import os
import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent   # tests/ 的上一级 = 项目根
src = (ROOT / "live.vpy").read_text(encoding="utf-8")

# 把 _native_out_size + _play_cap_size 的实现抽出来。
# ★ 2026-09-30：`_play_cap_size` 现在调 `_native_out_size()`（不再是内联
#   `src_w * OUT_SCALE`）—— 必须**两个函数一起抽**，否则 exec 时 NameError。
m0 = re.search(r"def _native_out_size\(\).*?\n(?=def |\Z)", src, re.S)
assert m0, "抽不到 _native_out_size"
m = re.search(r"def _play_cap_size\(\).*?\n(?=def |\Z)", src, re.S)
assert m, "抽不到 _play_cap_size"
body = m0.group(0) + "\n" + m.group(0)
body = body.replace("tuple[int, int] | None", "object")   # 兼容 py3.7 语法

GUI_CASES = [
    # (w, h, scale, cap, edge, 期望)
    (1280, 720, 4.0, 0, "short", (5120, 2880)),
    (1280, 720, 4.0, 2160, "short", (3840, 2160)),
    (1280, 720, 4.0, 1440, "short", (2560, 1440)),
    (1280, 720, 4.0, 1080, "short", (1920, 1080)),
    (1280, 720, 1.0, 2160, "short", (1280, 720)),
    (1920, 1080, 2.0, 2160, "short", (3840, 2160)),
    (3840, 2160, 2.0, 1080, "short", (1920, 1080)),
    # ★ 2026-09-30 加：判据边 long（拿长边比）—— 与 short 结果必须不同
    (3840, 2160, 1.0, 1920, "long", (1920, 1080)),
    (3840, 2160, 1.0, 1920, "short", (3414, 1920)),
    (2560, 1440, 2.0, 2560, "long", (2560, 1440)),
    (2560, 1440, 1.0, 1920, "long", (1920, 1080)),
    (3840, 2160, 1.0, 2560, "long", (2560, 1440)),
    (1920, 1080, 1.0, 1080, "long", (1080, 608)),
    (3840, 2160, 1.0, 3840, "long", (3840, 2160)),
]

ns = {}
# 直接和 GUI 的 capped_play_size 对齐（GUI 跑在系统 Python，这里重写一份同式）
def gui_cap(w, h, scale, cap, edge="short"):
    # 与 live_gui.capped_play_size 同式（含 edge 判据）
    ow, oh = int(round(w * scale)), int(round(h * scale))
    if cap and cap > 0:
        ref = max(ow, oh) if edge == "long" else min(ow, oh)
        if ref > cap:
            k = cap / float(ref)
            ow, oh = int(round(ow * k)), int(round(oh * k))
    return ow + (ow & 1), oh + (oh & 1)


bad = 0
for w, h, scale, cap, edge, exp in GUI_CASES:
    # scale 的语义已从"输出倍率"变成"**模型原生倍率**"（2026-09-30 删掉 OUT_SCALE）。
    # 数值上正好对应：animev3 = 4 / 2x 模型 = 2 / 仅补帧 = 1。
    ns = {"src_w": w, "src_h": h, "PLAY_MAX_H": cap,
          "PLAY_MAX_EDGE": edge, "UPSCALE_ON": True, "SR_NATIVE_SCALE": scale,
          "log": lambda *a, **k: None}
    exec(compile(body, "live.vpy:_play_cap_size", "exec"), ns)
    vs_got = ns["_play_cap_size"]()
    gui_got = gui_cap(w, h, scale, cap, edge)
    # ★ 语义差异是**设计如此**：VS 侧返回 None = "不用缩"（省掉一次 resize 节点），
    #   GUI 侧永远返回实际尺寸（它是用来显示"输出 2560×1440"的）。
    #   两者只有在「真的需要缩」时才必须逐像素一致。
    full = (int(round(w * scale)), int(round(h * scale)))
    _ref = max(full) if edge == "long" else min(full)
    need_resize = bool(cap and cap > 0 and _ref > cap)
    if need_resize:
        ok = (vs_got == gui_got == exp)
    else:
        ok = (vs_got is None and gui_got == full)
    if not ok:
        bad += 1
    print("  [%s] %dx%d x%.1f cap=%s/%-5s  VS=%s GUI=%s 期望=%s"
          % ("OK" if ok else "XX", w, h, scale, cap, edge, vs_got, gui_got, exp))

print()
if bad:
    print("✗ %d 项不一致" % bad)
    sys.exit(1)
print("✓ live.vpy 与 GUI 的限定播放分辨率语义一致（%d 项；需缩时逐像素相同）"
      % len(GUI_CASES))
