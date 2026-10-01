# -*- coding: utf-8 -*-
"""VFR 判定（ProbeWorker._probe_vfr）的**零误判**校验。

为什么这条必须有：判错的代价是**功能级**的，两个方向都会坏 ——
  · VFR 漏判 → 时间轴被 AssumeFPS 拍平 → 音画持续漂移（用户报的那个 bug）；
  · CFR 误判 → 该片**补帧被静默跳过**（live.vpy 里 src.fps=0 → 倍率 0），
    用户只会看到"补帧没生效"，很难联想到是探测器误判。
所以两头都要有真片源验：合成 CFR（24fps / 29.97fps）+ 已知的 VFR 片。

用法：python tests/_t_vfr_probe.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import live_gui as G  # noqa: E402

OK: list[str] = []
BAD: list[str] = []


def ck(cond: bool, name: str, extra: str = "") -> None:
    (OK if cond else BAD).append(name)
    print(f"  [{'OK' if cond else 'X '}] {name}" + (f"   {extra}" if extra else ""))


def make_cfr(path: Path, rate: str) -> bool:
    """用自带 ffmpeg 合成一段 CFR 测试片（3 秒 320x240，纯 CPU）。"""
    if not G.FFMPEG.is_file():
        return False
    try:
        r = subprocess.run(
            [str(G.FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"testsrc2=s=320x240:r={rate}", "-t", "3",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode == 0 and path.is_file() and path.stat().st_size > 0
    except Exception:
        return False


print("== VFR 判定零误判 ==")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    for rate, label, want in (("24", "CFR 24fps", False),
                              ("30000/1001", "CFR 29.97fps（pts 有抖动）", False)):
        p = tmp / f"cfr_{rate.replace('/', '_')}.mp4"
        if not make_cfr(p, rate):
            ck(False, f"合成 {label} 测试片失败（ffmpeg 不可用？）")
            continue
        is_vfr, fps = G.ProbeWorker._probe_vfr(str(p))
        ck(is_vfr is want, f"{label} → VFR={want}", f"实测 VFR={is_vfr} {fps:.2f}fps")

# 真实 VFR 样片（存在才跑；换机器没有就跳过，不算失败）
VFR_SAMPLE = Path(r"F:\动画\正常向\[X2][D.Gray-man][01-103][HDTVRIP][BIG5][RV10]"
                  r"\[X2][D.Gray-man][01][HDTVRIP][BIG5][XVID].rmvb")
if VFR_SAMPLE.is_file():
    is_vfr, fps = G.ProbeWorker._probe_vfr(str(VFR_SAMPLE))
    ck(is_vfr is True, "真 VFR 样片（rmvb）→ VFR=True", f"实测平均 {fps:.2f}fps")
    ck(0 < fps < 30, "该样片实测平均帧率落在合理区间（< 容器头的 30）", f"{fps:.2f}fps")
else:
    print(f"  -- 跳过真 VFR 样片（不存在：{VFR_SAMPLE}）")

print()
if BAD:
    print(f"✗ 失败 {len(BAD)} 项：{BAD}")
    sys.exit(1)
print(f"✓ 全部通过　共 {len(OK)} 项")
