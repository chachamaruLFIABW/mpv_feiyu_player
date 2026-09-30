# -*- coding: utf-8 -*-
"""相似帧检测：给「与下一帧几乎一样」的源帧打标记，让 RIFE 跳过推理。

**为什么有效**：vsmlrt 的 `RIFE` 内部有个 `filter_sc`（见 vsmlrt.py）：

    def filter_sc(n, f):
        ...
        if (current_time % src_duration == 0
                or left_index + 1 >= src_frames
                or f.props.get("_SceneChangeNext", False)):
            return left0            # ← 直接输出左源帧，不跑网络
        return output0

所以只要源帧带 `_SceneChangeNext`，那一对帧就**不做推理**。
k7sfunc / mpv-lazy 的 `turbo=2` + `stat_th` 走的就是这条通路。

**为什么自己算**：本机没有 `core.misc`（SCDetect）也没有 `core.akarin`（PropExpr），
那两家用的插件这里都没有。于是用标准滤镜拼：
`std.ShufflePlanes`(取一个平面) → `std.MakeDiff` → `std.PlaneStats` → `std.ModifyFrame`。

**方向说明**：`_SceneChangeNext` 打在帧 k 上表示「k 与 k+1 这一对不要插」。
理想上应该用前向差 |k − (k+1)|，但那要**前视一帧**，而 mpv 下源 clip 的
`num_frames` 是哨兵值、文件末尾会去要不存在的帧（真实长度下直接 IndexError）。
所以用**后向差** |k − (k−1)|：语义等价（只多浪费每个静止段的第一次推理，
而那两帧本来一模一样）。**改方向前先看 `_t_static_unit.py` 的断言。**
"""

import vapoursynth as vs

_STAT = {"n": 0, "skip": 0}


def reset_stats() -> None:
    _STAT["n"] = 0
    _STAT["skip"] = 0


def stats() -> dict:
    return dict(_STAT)


def mark_static(clip, th: float, core=None, report=None, every: int = 240):
    """返回打了 `_SceneChangeNext` 的 clip。

    th     归一化阈值（0~1）。0 = 关闭，调用方应自己跳过。
    report 可选回调，形如 report(skip, total)，用来定期回报占比。
    every  每处理多少源帧回报一次。
    """
    if th <= 0:
        return clip
    core = core or vs.core
    # ★ PlaneStatsAverage 在 R60+/API4 里**一律是归一化的 0~1**，
    #   跟位深无关 —— 实测：GRAY8 里一个恒为 10 的平面报 0.039（= 10/255）。
    #   所以阈值**不要**乘 (2^bits-1)。这个坑是 `_t_static_unit.py` 抓出来的：
    #   乘了之后阈值被放大 255 倍，会把每一帧都判成"相似"→ RIFE 全部跳过。
    th_abs = th
    if clip.format.num_planes != 1:
        gray = core.std.ShufflePlanes([clip], [0], vs.GRAY)
    else:
        gray = clip
    # ★★ `PlaneStats` **不认 fp16**：实测链路上会直接抛
    #    "Input clip must be constant format 8..16 bit integer or 32 bit float,
    #     passed GrayH." —— 而开了 fp16 I/O 之后灰度平面正好是 GrayH
    #    （只取一个平面，这一步很便宜）。
    if gray.format.sample_type == vs.FLOAT and gray.format.bits_per_sample != 32:
        gray = core.resize.Point(gray, format=vs.GRAYS)
    # ★ 用**后向差** |k − (k−1)|，不做前视。
    #   原因：mpv 给的源 clip 的 num_frames 是哨兵值（实测 2^27），
    #   而真实长度下 `gray[n+1]` 会直接 IndexError；写 `gray[n+1]` 在 mpv 里
    #   虽然不越界检查，但**文件末尾那帧会去要不存在的下一帧** → 滤波器报错。
    #   SCDetect 这类上游插件也只回看，不回看前方，同一个道理。
    #   代价：每个静止段的第一对帧仍会被推理一次（两帧一模一样，结果不变）。
    prev = core.std.FrameEval(gray, lambda n: gray[max(n - 1, 0)])
    # ★★ 必须取**绝对差**。`std.MakeDiff` 在浮点格式下是**有符号**的（a − b）：
    #    实测一对 0.8 → 0.04 的相邻帧会得到 PlaneStatsAverage = **−0.76**，
    #    而 −0.76 < 阈值 → 被误判成"相似帧"、本该插帧的运动被跳过。
    #    用 Expr 的 `abs` 才是真正的平均绝对差。
    diff = core.std.PlaneStats(core.std.Expr([gray, prev], "x y - abs"))

    def _mark(n, f):
        # ⚠ ModifyFrame 传参有形态差异：单 clip 时 f 是 frame 本身，
        #   多 clip 时才是 frame 的列表。别假设。
        frames = f if isinstance(f, (list, tuple)) else [f]
        out = frames[0].copy()
        # n == 0 没有上一帧可比，一律当"有运动"（不倒推、不跳过），最保守
        skip = int(n > 0 and frames[1].props.PlaneStatsAverage < th_abs)
        out.props["_SceneChangeNext"] = skip
        _STAT["n"] += 1
        if skip:
            _STAT["skip"] += 1
        if report is not None and every and _STAT["n"] % every == 0:
            report(_STAT["skip"], _STAT["n"])
        return out

    return core.std.ModifyFrame(clip, [clip, diff], _mark)
