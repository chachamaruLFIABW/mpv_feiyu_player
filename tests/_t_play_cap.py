"""「限定播放分辨率」(LIVE_PLAY_MAX_H) 回归：断言补帧的工作尺寸真的被限住。

背景（2026-09-30 用户报「限定播放分辨率在仅补帧下不会生效，信息还是拿 4K 去补」）：
  `fit_play_cap` 被错关在 `if UPSCALE_ON:` 内部 → 仅补帧（UPSCALE_ON=False）时
  整块跳过 → 上限形同虚设。而仅补帧时 ORDER 必然是 HR，正好走那个分支。

判据：从日志解析两条行，断言「补帧：... 输入 WxH」== 「限定播放分辨率 上限：... → WxH」。
覆盖：仅补帧（1080/1440/不限）、超分+补帧（无回归）、上限大于源（不该放大）。

用法：python _t_play_cap.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MPV = ROOT / "mpv" / "mpv.exe"
CN_DIR = ROOT / "_cn_tmp"
SRC = ROOT / "_fx_4k_cap.mp4"
ok = fail = 0


def gen_src() -> Path:
    if SRC.is_file():
        return SRC
    sys.path.insert(0, str(ROOT / ".venv" / "Lib" / "site-packages"))
    import imageio_ffmpeg
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i",
         "testsrc2=size=3840x2160:rate=24:duration=3", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", str(SRC)],
        check=True, capture_output=True)
    return SRC


def run(extra: dict) -> str:
    vspy = ROOT / ".venv"
    vssp = vspy / "Lib" / "site-packages"
    e = dict(os.environ)
    e.update({
        "LIVE_VFI": "mv", "LIVE_INTERP": "1", "LIVE_OUT_SCALE": "1",
        "LIVE_VERBOSE": "1", "LIVE_INTERP_FPS": "-2",
        "PATH": os.pathsep.join([str(vssp / "vapoursynth"), str(vspy),
                                 str(vspy / "Scripts"), e.get("PATH", "")]),
        "PYTHONPATH": str(vssp),
    })
    e.update(extra)
    CN_DIR.mkdir(exist_ok=True)
    p = subprocess.run(
        [str(MPV), "--config-dir=" + str(CN_DIR),
         "--vf=vapoursynth=file=live.vpy:concurrent-frames=4",
         "--vo=null", "--ao=null", "--frames=2", "--msg-level=all=info",
         str(SRC)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=e, timeout=240, cwd=str(ROOT))
    return (p.stdout or "") + (p.stderr or "")


def case(tag: str, extra: dict, want_h, must_cap: bool):
    """want_h: 期望补帧输入短边；None = 不检查具体值只查一致性。"""
    global ok, fail
    txt = run(extra)
    # ⚠ 正则必须容忍判据边后缀 —— 2026-09-30 给日志加了「（按短边）」之后，
    #   原来的 `(\d+)：` 直接失配，4 个用例全报"未生效"（其实尺寸全对）。
    cap = re.search(
        r"限定播放分辨率 (\d+)(?:（按[短长]边）)?：(\d+)x(\d+) → (\d+)x(\d+)", txt)
    interp = re.search(r"补帧：\S+\s+引擎=mvtools.*?输入 (\d+)x(\d+)", txt)
    enabled_note = re.search(r"链路就绪：.*?→\s*(\d+)x(\d+)\s*@", txt)
    problems = []
    if not interp:
        problems.append("日志无补帧行")
    cap_h = None
    if cap:
        cap_h = min(int(cap.group(4)), int(cap.group(5)))
    if must_cap and not cap:
        problems.append("设了上限但日志没有『限定播放分辨率』行（未生效？）")
    if interp and cap:
        iw, ih = int(interp.group(1)), int(interp.group(2))
        if (iw, ih) != (int(cap.group(4)), int(cap.group(5))):
            problems.append(f"补帧输入 {iw}x{ih} ≠ 上限尺寸 "
                            f"{cap.group(4)}x{cap.group(5)}")
    if interp and want_h is not None:
        ih = int(interp.group(2))
        if abs(ih - want_h) > 2:
            problems.append(f"补帧短边 {ih} ≠ 期望 {want_h}")
    good = not problems
    got = (f"{interp.group(1)}x{interp.group(2)}" if interp else "—")
    print(f"  [{'OK  ' if good else 'FAIL'}] {tag:<34} 补帧输入={got:<11}"
          f"{'  ← ' + '; '.join(problems) if problems else ''}")
    ok += good
    fail += not good


gen_src()
print("=== 限定播放分辨率 回归（4K 源，仅补帧 / 超分+补帧）===")
print("核心断言：补帧的实际输入尺寸 == 上限尺寸（不能仍是 4K）")

print("\n-- 仅补帧（超分关）—— 曾经完全失效的场景 --")
case("仅补帧 + 限 1080", {"LIVE_UPSCALE": "0", "LIVE_PLAY_MAX_H": "1080"},
     1080, True)
case("仅补帧 + 限 1440", {"LIVE_UPSCALE": "0", "LIVE_PLAY_MAX_H": "1440"},
     1440, True)
case("仅补帧 + 限 720", {"LIVE_UPSCALE": "0", "LIVE_PLAY_MAX_H": "720"},
     720, True)

print("\n-- 不限（对照，应保持 4K）--")
case("仅补帧 + 不限", {"LIVE_UPSCALE": "0", "LIVE_PLAY_MAX_H": "0"},
     None, False)

print("\n-- 超分+补帧（确认无回归）--")
case("超分+补帧 + 限 1080",
     {"LIVE_UPSCALE": "1", "LIVE_SR": "realesr-animevideov3_re2x",
      "LIVE_PLAY_MAX_H": "1080"}, 1080, True)

print("\n-- 上限大于源（绝不放大）--")
case("仅补帧 + 限 4320（>源）", {"LIVE_UPSCALE": "0", "LIVE_PLAY_MAX_H": "4320"},
     None, False)

print(f"\n结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
