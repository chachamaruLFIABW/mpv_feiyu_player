"""补帧档位「源帧率 ×n」的离线矩阵校验。

验证 live_gui.resolve_multi / resolve_target_fps 与 live.vpy 的
interp_multi() 对**同一批源帧率 × 全部档位**给出**一致**的倍率，
并且不再出现「倍率 < 1 而跳过补帧」这种白选的情况。

用法： .venv/Scripts/python.exe _t_fpsmatrix.py
"""
from __future__ import annotations

import re
import sys
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def _extract_funcs(path: Path, names: set[str]) -> str:
    """按**函数名**从源码里抠出顶层 def 的源码（不靠行号 —— 行号会漂）。

    ★ 原来这里写死行切片（`lines[507:543]`）—— 上游文件一改就 KeyError。
      2026-09-30 加 Anime4KCPP 时又踩了一次。改成 AST 定位后彻底免疫。
    """
    import ast
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            # 连上面的装饰器和紧邻的注释一起要（没有也不影响执行）
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            out.append("\n".join(lines[start - 1:node.end_lineno]))
    missing = names - {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if missing:
        raise SystemExit(f"{path.name} 里找不到函数 {sorted(missing)}")
    return "\n".join(out)


# ── 1. 把 GUI 的两个纯函数抠出来（import live_gui 会拉起 Qt，太重）──
_ns_gui: dict = {"AUTO_FPS_MAX": 60}
exec(_extract_funcs(HERE / "live_gui.py", {"resolve_multi", "resolve_target_fps"}),
     _ns_gui)
resolve_multi = _ns_gui["resolve_multi"]
resolve_target_fps = _ns_gui["resolve_target_fps"]

# ── 2. 把 live.vpy 的 interp_multi 抠出来，喂假 clip / 假全局 ──
_ns_vpy: dict = {}
_ns_vpy.update({
    "INTERP_FPS_AUTO": True, "INTERP_FPS_MULTI": 0.0, "INTERP_FPS": 0.0,
    "INTERP_MULTI": 0.0, "AUTO_FPS_MAX": 60, "Fraction": Fraction,
})
_vpy_src = _extract_funcs(HERE / "live.vpy", {"interp_multi"})


class _Clip:
    """live.vpy 的 interp_multi 只读 .fps / .fps_num，给个最小替身就够。"""

    def __init__(self, fps: float):
        fr = Fraction(fps).limit_denominator(1001)
        self.fps = fr
        self.fps_num = fr.numerator
        self.fps_den = fr.denominator


def _mk_clip(fps: float):
    return _Clip(fps)


AUTO_FPS_MAX = 60
_vpy_ns: dict = {"Fraction": Fraction, "AUTO_FPS_MAX": AUTO_FPS_MAX}


def _vpy_multi(sel, src_fps: float) -> Fraction:
    """按 live.vpy 的真实分支逻辑算倍率（模拟三种 sel 编码）。"""
    ns = dict(_vpy_ns)
    if sel == 0:
        ns["INTERP_FPS_AUTO"] = True
        ns["INTERP_FPS_MULTI"] = 0.0
        ns["INTERP_FPS"] = 0.0
    elif sel < 0:
        ns["INTERP_FPS_AUTO"] = False
        ns["INTERP_FPS_MULTI"] = float(-sel)
        ns["INTERP_FPS"] = 0.0
    else:
        ns["INTERP_FPS_AUTO"] = False
        ns["INTERP_FPS_MULTI"] = 0.0
        ns["INTERP_FPS"] = float(sel)
    ns["INTERP_MULTI"] = 0.0
    exec(_vpy_src, ns)
    return ns["interp_multi"](_mk_clip(src_fps))


SOURCES = [
    ("23.976 电影", 24000 / 1001),
    ("24 剧场", 24.0),
    ("25 PAL", 25.0),
    ("29.97 NTSC", 30000 / 1001),
    ("30 番剧", 30.0),
    ("48 高帧", 48.0),
    ("50 PAL-HD", 50.0),
    ("59.94", 60000 / 1001),
    ("60 高帧", 60.0),
    ("120 手机", 120.0),
]
SELS = [0, -2, -3, -4, 60, 144]

fails: list[str] = []
rows: list[tuple] = []

for name, sf in SOURCES:
    for sel in SELS:
        g_m = resolve_multi(sel, sf)
        v_m = _vpy_multi(sel, sf)
        g_mf = float(Fraction(g_m).limit_denominator(1000))
        v_mf = float(v_m)
        same = abs(g_mf - v_mf) < 1e-6
        tgt = sf * g_mf
        tag = "" if same else "  ← GUI/V S 不一致!"
        if not same:
            fails.append(f"{name} sel={sel}: GUI={g_mf:g} vpy={v_mf:g}")
        # ★ 判据：**倍率档（负数）与 auto** 不该算出 ≤1（那是"白选"）。
        #   绝对 fps 档（60/144）例外 —— 源本身 ≥目标时倍率必然 ≤1，
        #   这时跳过补帧是**正确**行为（没什么可插的）。
        if (sel == 0 or sel < 0) and g_mf <= 1.0:
            fails.append(f"{name} sel={sel}: 倍率 {g_mf:g} ≤1（白选）")
        rows.append((name, sf, sel, g_mf, v_mf, tgt, tag))

print(f"{'源':<12}{'fps':>8}{'档位':>7}{'GUI×':>7}{'vpy×':>7}"
      f"{'→目标fps':>10}")
print("-" * 62)
for name, sf, sel, gm, vm, tgt, tag in rows:
    slab = "auto" if sel == 0 else (f"×{-sel}" if sel < 0 else f"{sel}fps")
    print(f"{name:<12}{sf:>8.3f}{slab:>7}{gm:>7.3f}{vm:>7.3f}"
          f"{tgt:>10.1f}{tag}")

print()
if fails:
    print(f"✗ {len(fails)} 项不合格：")
    for f in fails:
        print("   ", f)
    sys.exit(1)
print(f"✓ 全部 {len(rows)} 组一致，且无「倍率 ≤1 白选」")
