# -*- coding: utf-8 -*-
r"""链路自检：mpv -> VSScript -> python -> VapourSynth 能不能通。

**只走 CPU 滤镜（std.Levels），不碰 CUDA/TRT**，可以放心在跑别的 GPU 任务时执行。

用法（在 tools/live 目录下，或让 selfcheck.py --link 自动跑）：
    set PATH=<vapoursynth目录>;<python目录>;%PATH%
    set PYTHONPATH=<site-packages>
    ..\mpv\mpv.exe --no-config --no-hwdec --vo=null --ao=null ^
        --vf=vapoursynth=file=link_check.vpy --frames=6 --untimed ..\_link_test.mp4

★ 两个必须记住的约定（都是实测踩出来的）：
  1) 输入变量是 **video_in**（mpv 注入），**没有 `clip`** —— 那是 vspipe 的约定。
  2) 输出必须调 **VideoNode.set_output()**；只写 `output = ...`（老约定）时，
     mpv 的 getOutputNode(script, 0) 拿不到节点，会报
     "Could not get script output node." 并把滤镜禁用。
"""
import sys

import vapoursynth as vs

core = vs.core

src = video_in                                     # mpv 注入的输入（不是 clip）
print(f"[link] 输入 {src.width}x{src.height} {src.format.name} "
      f"container_fps={container_fps} display_fps={display_fps} "
      f"user_data={user_data!r}", file=sys.stderr, flush=True)

out = core.std.Levels(src, 0, 255, 1.0, 0, 235, [0, 1, 2])   # 纯 CPU
print(f"[link] 链构建完成 -> {out.width}x{out.height} {out.format.name}",
      file=sys.stderr, flush=True)

out.set_output(0)                                  # ★ API4 的注册机制
print("[link] set_output(0) 完成 —— 链路通了", file=sys.stderr, flush=True)
