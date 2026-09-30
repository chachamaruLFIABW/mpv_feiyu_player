# -*- coding: utf-8 -*-
"""mpv 实时超分 + 补帧滤镜脚本（VapourSynth）

由 mpv 通过 `--vf=vapoursynth=file=live.vpy` 加载，把「超分 / 补帧」插进播放链路。
播放/解码/音画同步/字幕/seek 全部由 mpv 负责，这里只管图像处理。

参数全走环境变量（由 live_play.bat 设置，也可以手动 export 后直接跑 mpv）：

    LIVE_UPSCALE=1/0       超分开关                 默认 1
    LIVE_INTERP=1/0        补帧开关                 默认 1
    LIVE_ORDER=auto        处理顺序                 默认 auto
                           hr = 先超分后补帧；lr = 先补帧后超分；
                           auto = 源宽 ≥1280 且两者都开时用 lr，否则 hr
    LIVE_OUT_SCALE          ⚠ **已删除（2026-09-30）** —— 见下面「为什么没有输出倍率」
    LIVE_PLAY_MAX_H=0      限定播放分辨率的上限     默认 0 = 不限
                           ★ 解决的问题：4x 模型会把 720p 源超成 5120x2880，
                             比屏幕还大 —— 多出来的像素全要过补帧和输出搬运。
                             限住之后：超分照旧按档位推理 → 缩到上限尺寸 →
                             **补帧在这个尺寸上做**（省的是最贵的那一步）。
                           ⚠ 只管上限：源比它小就按源的来，**绝不放大**。
                           ⚠ 它管**最终输出尺寸**；管超分推理档位的是 LIVE_SR_MAX。
    LIVE_PLAY_MAX_EDGE=short  判据边：short = 按短边 / long = 按长边
                           「我想限成 1920 宽」用 long：
                             3840x2160 限 1920 → short 给 3414x1920，long 给 1920x1080
    LIVE_SR_MAX=0          超分**导入**分辨率上限        默认 0 = 不限
    LIVE_SR_MAX_EDGE=short   判据边：short = 比短边（1080p 源填 1080 挑 1920x1080）
                                     long  = 比长边（填 1920 也挑 1920x1080）
                           ★ 两种填法表达同一件事，只是拿哪条边当判据 —— 想按
                             "1920 宽"思考就用 long，不用自己换算短边 px。
                           ★ 降本开关，但**救不了 1080p 全链路**（60s 长窗实测）：
                             超分单帧随档位下降（AnimeJaNai Balanced 隔离口径：
                             1920x1080→26.9ms / 1280x720→12.4ms / 960x720→7.9ms），
                             可"超分+补帧"同开时，60 秒真播稳定丢帧 9.0/s
                             —— 且 estimated-vf-fps 照样报 48.0，**只看帧率会误判**。
                             ★ 1080p 要稳，**超分 / 补帧 二选一**（各 60s 窗全 0 丢帧）。
                             ⚠ 别用 12~14s 短窗判稳：临界配置短窗能出 0/0/1/1。
                             720p 源两者同开无压力，不受影响。
    LIVE_INTERP_FPS=auto   补帧倍率档（**源帧率 ×n**）  默认 auto
                           ★ 2026-09-30 改语义：从「绝对目标帧率」改成
                             「源 ×n」，因为源不一定是 24fps —— 旧档位
                             （48/60/72…）碰到 25fps 源会算出 1.92x（非整数、
                             多调一次 RIFE），碰到 50fps 源算出 0.96x < 1
                             → **直接跳过补帧**，等于白选。
                           取值三态：
                             auto/0  → 自动倍数：源 ≥60fps 取 2x；否则取能
                                       塞进 60 的最大整数倍（24→2x=48、
                                       30→2x=60、25→2x=50）
                             -2/-3/-4 → **固定 n 倍**（与源帧率无关，主力档）
                             60/144  → 绝对目标帧率（对齐显示器刷新率才用）
                           ★ 为什么优先整数倍：每对输入帧调用 RIFE 的次数
                             = ⌈倍率⌉−1。24→60 是 2.5x，要调 **2 次**；
                             24→48 是 2x，只调 **1 次** —— 多 100% 成本只多 25% 帧。
    LIVE_STATIC_TH=0.003   相似帧阈值（0 = 关闭）
                           与下一帧的平均差低于它 → 跳过 RIFE 直接用源帧。
                           实拍/番剧里大量静止帧，收益很大（mpv-lazy 的 turbo=2 就是干这个）
    LIVE_INTERP_MULTI=0    补帧倍率（兼容旧调用）  默认 0 = 不用，按 FPS 算
    LIVE_RIFE_VER=4.26     RIFE 模型版本            默认 4.26
                           ★ 实测**版本对性能几乎没影响**（1080p 都在 3.2~4.4ms），
                             挑版本是白费功夫 —— 想换口味应该换下面的 LIVE_VFI。
    LIVE_VFI=rife          补帧**引擎**（换算法，不是换模型版本）  默认 rife
                             rife   = vsmlrt C++ TensorRT（最快，占显存；
                                      平移镜头下有线条抖动/伪影）
                             mv     = mvtools 传统光流（**纯 CPU、零显存**；
                                      画质弱，但能让 1080p 超分+补帧同开）
                             dup    = 帧复制（零成本零插件，运动顿挫）
                             blend  = 帧混合（零成本零插件，运动有重影）
                           ★ 关键实测（1080p 源，60 秒长窗，@48fps）：
                               rife : 超分 + 补帧 同开 → 丢 9.0/s（跑不动）
                               mv   : 超分 + 补帧 同开 → **丢 0**（补帧挪到 CPU，
                                      超分用满 GPU，两边都要）
                             前提是超分用 **2x 模型**（AnimeJaNai）：
                             同一个 mv 配 re2x 超分 → 丢 1684 帧（4x 引擎的
                             1080p 中间步骤太贵）。
                           （补帧后端**固定 TensorRT**，不给选项：ncnn 跑不了 RIFE ——
                             RIFE 依赖 GridSample，ncnn 会报
                             `GridSample not supported yet!`，图能建、fps 也对，
                             但推理是废的。本机缺的 trtexec.exe 由 trtexec_py.py
                             用 TensorRT Python API 顶替，见该文件注释）
    LIVE_SR=animev3        超分模型：animev3 / realesr-animevideov3_re2x
    LIVE_DEVICE=0          GPU 编号                 默认 0
    LIVE_STREAMS=2         推理并发流               默认 2（1 会慢约 10%）
    LIVE_FP16_IO=1         补帧用 fp16 I/O          默认 1
                           （搬运量砍半，1080p @60 实测 +55%）
    LIVE_OUT_FMT=auto      输出像素格式             默认 auto
                           auto = 8bit 源用 yuv420p8、否则 yuv420p16
                           （另可选 yuv444p16 等，mpv 都收，但要实测）
                           ⚠ 输出**必须**是 YUV：mpv 的 vapoursynth 滤镜
                             只认 YUV（mp_from_vs 里 `if (colorFamily==cfYUV)`，
                             RGB 一律 `Unsupported output format` 并**禁用
                             整条滤镜**）。曾经的 LIVE_RAW_OUT 开关因此已删除。
    LIVE_VS_THREADS=0      VS 线程数                默认 0 = 不干预
                           （本机默认 20，显式限制有时更稳）
                           ⚠ 对 mv 补帧**无效** —— mvtools 内部自有线程池，
                             实测 1→32 线程耗时只差 18%。治 mv 请用 LIVE_MV_OPT。
    LIVE_MV_OPT=4          mvtools 内部线程数（pinterf `opt`）  默认 4
                           ★ 这是 mv 补帧唯一有效的加速旋钮。库默认 -1(自动)
                             偏保守：1280x720 整链 1.63 → opt=4 时 1.59 ms，
                             仅 Analyse 一级则 2.32 → 1.51 ms（快 35%）。
                             超过 ~12 无收益甚至回退（物理核只有 12）。
                             老构建不认 opt 会自动跳过（见 MV_OPT 处探查）。
    LIVE_MV_HD=1           4K 自适应：源宽 ≥2560 时 blksize 翻倍(≤64) +
                           块匹配关色度   默认 1 = 开
                           ★ 4K 仅补帧从 43.4fps（不够 48 → 掉帧）拉到
                             61.7fps（余量 1.29x）。动画素材基本无损。
                             0 = 关闭（回到单一 blksize）。
    LIVE_IMPL=1            RIFE 实现                默认 1（只剩这一条路了）
                           1 = 11 通道 onnx（models/rife/，要 pad 到模型要求的
                               倍数：4.26/4.25_heavy = 64，4.25_lite = **128**）
                           ★ 2026-09-30：2（7 通道 rife_v2/）已删除 —— 本机 TRT 11
                             解析不了它的混合精度 /Mul，留着只是占 429MB 磁盘。
    LIVE_CUDA_GRAPH=1      TRT CUDA graph           默认 1
    LIVE_MATRIX=709        色彩矩阵                 默认 709
    LIVE_VERBOSE=1         打印链路与耗时           默认 1

═══ 四个关键设计决定（都是被现场实测逼出来的，别随手改）═══

1) **超分不走 vsmlrt.RealESRGAN，直接调 core.trt.Model 吃现成的 .engine。**
   原因：vsmlrt 的 TRT 后端不是自己编译引擎，而是命令行调
   `plugins/vsmlrt-cuda/trtexec`（见 vsmlrt.py 的 trtexec()）。
   而 pip 版 VapourSynth 的 wheel 里 `plugins/vsmlrt-cuda/` **只有
   nvinfer_11.dll / nvinfer_plugin_11.dll / nvonnxparser_11.dll，没有 trtexec.exe**
   （已确认，全盘也没有）。好在 `models/engines/` 里已经有 trt_upscaler.py 编好的引擎，
   `core.trt.Model` 可以直接加载 .engine —— 绕开 trtexec 这一环，还省掉首次编译。

2) **档位化：源先 resize 到最近的固定档再推理，输出再按源尺寸 × 倍率降采样。**
   原因：TRT 引擎是 fixed shape（档位见 LADDER）。番剧源多大都有
   （848x480 / 852x480 / 854x480 都常见），非标尺寸直接喂进去会 shape 不匹配。

3) **补帧前要把尺寸 pad 到 64 的倍数，插完再裁回。**
   RIFE v4.26 的 `tilesize_requirement = 64`，而视频高度基本都不满足
   （640x360 的 360、1280x720 的 720 都不是）。不 pad 会直接报
   `vsmlrt.RIFEMerge: tile size must be divisible by 64`。

4) **★ 高分辨率源用 LR 顺序（先补帧后超分）而不是 HR。**
   RIFE 的开销对尺寸极度敏感：720p 输入每对 ~12 ms，但 1440p 输入每对 ~53 ms（4.4 倍）。
   而超分模型便宜到可以多跑一倍次数（realesr-animevideov3_re2x 720p→1440p 只要 6.5 ms）。
   实测 720p 源 → 1440p 输出 @48fps：
       HR（先超分后补帧）  29.91 ms/输出帧 →  33 fps  ✗
       LR（先补帧后超分）  18.08 ms/输出帧 →  55 fps  ✓
   ⚠ 这条推翻了 09-27 的旧结论（那时超分器 av3 要 8.47ms，LR 多做一倍次数所以更慢）。
     旧结论只在「超分贵、RIFE 尺寸小」时成立，现在两个前提都变了。

补帧走 vsmlrt.RIFE（官方 onnx，模型目录被改到 tools/live/models/）。
⚠️ 注意：ComfyUI 那个 rife_v4.26.safetensors 和项目里的 rife49_sim.onnx（3 输入）
   都**不能**用于 vsmlrt —— 后者的 onnx 把多路输入沿**通道**拼成单输入
   （rife/ 是 11 通道 = img0+img1+timestep+4 个网格量）。
"""
from __future__ import annotations

import os
import sys
import time
from fractions import Fraction
from pathlib import Path

import vapoursynth as vs

core = vs.core

HERE = Path(__file__).resolve().parent

# 本文件夹是**自包含**的 —— 不依赖视频放大项目的任何路径：
#     mpv\            mpv.exe（含 vapoursynth 滤镜的构建）
#     vsmlrt\         vsmlrt.py（RIFE 的图构建逻辑）
#     models\sr\      超分引擎（animevideov3 / realesr-animevideov3_re2x 的 fp16 档）
#     models\rife\    RIFE onnx + 已编译的引擎
# 唯一的外部依赖是 Python 侧的 VapourSynth（装在 ComfyUI 解释器里，
# 由 live_play.bat 通过 PATH / PYTHONPATH 指过去）。
VSMLRT_SCRIPTS = HERE / "vsmlrt"
ENGINE_DIR = HERE / "models" / "sr"
AJ_ENGINE_DIR = HERE / "models" / "sr_aj"      # AnimeJaNai / anime4x 系（build_aj_engines.py 编）
LIVE_MODELS = HERE / "models"


# ---------------------------------------------------------------- 参数读取
def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "y")


UPSCALE_ON = _flag("LIVE_UPSCALE", True)
INTERP_ON = _flag("LIVE_INTERP", True)
ORDER = _env("LIVE_ORDER", "auto").lower()
# ★★ 「输出倍率」LIVE_OUT_SCALE —— **2026-09-30 已删除**（用户提问后查证确认冗余）。
#   为什么删（三条实测/代码依据，不是猜的）：
#     ① **改不了推理倍率**：模型倍率是设计好的（animev3 = 4x / AnimeJaNai · re2x = 2x
#        / Anime4KCPP = 2x）。这个旋钮从来没能改变模型跑几倍。
#     ② **改不了选档、也不影响推理成本**：`pick_ladder(src_w, src_h)` 只看**源尺寸**，
#        与它无关。也就是说它对"超分要算多少像素"**零影响**。
#     ③ **它唯一的作用是"最后那次 resize 的目标尺寸"** —— 而那正是
#        `LIVE_PLAY_MAX_H`（限定播放分辨率）的职责。
#        ⚠ 而且两者重叠时它**完全被覆盖**：cap 生效时输出恒等于 cap 决定的尺寸
#          （源 3840x2160 + cap 1080/short：无论 OUT_SCALE=1 还是 2 都是 1920x1080）。
#   → 结论：冗余，且有害（让用户以为能选"超分倍率"，选 ×4 配 2x 模型只是
#     **假放大**：推理出 2x 再 bicubic 拉到 4x，画质不会变好、还多一次 resize）。
#   ★ 现在的语义：**输出尺寸 = 源 × 模型原生倍率，再受 LIVE_PLAY_MAX_H 压**。
#     要"只清洗不放大"（1080p 源 + 2x 模型 → 仍出 1080p）：把 cap 设成 1080 即可 ——
#     原来用 OUT_SCALE=1 表达的意图，cap 一样能表达（而且更直观）。
# ★ 超分档位上限（短边像素）：0 = 不限。命中时 pick_ladder 只在 min(w,h) ≤ 该值的档里挑。
#   超分成本 ~正比档位像素数（AnimeJaNai Balanced 隔离实测，逐帧噪声输入）：
#     960x720 → 13.1ms / 1280x720 → 26.6ms / 1920x1080 → 26.6ms
#   （隔离口径比真播悲观 —— 真播同配置 60s 零丢帧，见下。）
#
# ⚠️⚠️ 2026-09-30 更正 —— 之前两条结论是**错测量法**得出的，重测后推翻：
#   ① 早期"实测"用 `_t_avsync.py` 时传的键名漏了 `LIVE_` 前缀（写成 `INTERP=0`），
#      env 覆盖**静默失效**，量的一直是出厂默认档 —— 那些"低消耗预设 0 丢帧"
#      其实测的是别的配置。
#   ② 短窗（12~14s）量不出临界配置：同一配置 4 次能出 0/0/1/1，看着完全没问题。
#   用 70s 素材开 60s 真播窗后真相出来：
#     A 只超分（SR_MAX=960 out×1）  → 输出 24.0fps  丢帧 0 帧     ✓
#     B 只补帧 @48                  → 输出 48.0fps  丢帧 0 帧     ✓
#     C 超分+补帧（同 A 口径 @48）   → 输出 48.0fps  丢帧 503 帧   ❌ 9.0/s
#   C 的阴险之处：`estimated-vf-fps` **照样报 48.0**（滤镜链吞吐达标），
#   但 VO 一直在丢帧保同步 —— 只看帧率会误判成"能跑"。**必须看丢帧计数。**
#   ★ 结论：1080p 上超分 / 补帧 只能二选一。这个开关的价值是把单帧从 26.6ms
#     压到 13.1ms，让"只超分"更宽裕，但**救不活全链路**。
#   720p 源两者同开毫无压力（0 丢帧），设不设都一样。
SR_MAX = int(_env("LIVE_SR_MAX", "0") or 0)
# ★ 2026-10-01：上限的**判据边**（short = 短边 / long = 长边）。
#   「按短边」= 原来的行为（1080p 源填 1080 → 挑 1920x1080 档）。
#   「按长边」= 直接填想限的**宽度**（填 1920 → 同样挑到 1920x1080 档），
#   不用自己换算短边 px —— 正常人不会去算这个。
#   ⚠ 合法值校验**不能写在这里**：`warn` 定义在 ~430 行，在参数区调它会
#     `NameError` → 整条滤镜被 mpv 禁用。同 _PLAY_EDGE_BAD / _MATRIX_BAD 的模式：
#     常量区只**记账**，warn 定义之后统一报。
SR_MAX_EDGE = _env("LIVE_SR_MAX_EDGE", "short").strip().lower()
if SR_MAX_EDGE not in ("short", "long"):
    _SR_EDGE_BAD = SR_MAX_EDGE
    SR_MAX_EDGE = "short"
else:
    _SR_EDGE_BAD = None
# ★ 限定播放分辨率（LIVE_PLAY_MAX_H）：最终**输出**（也是**补帧的工作分辨率**）
#   的短边上限。0 = 不限。
#
#   为什么需要它：4x 模型（realesr-animevideov3）在 720p 源上会出 5120x2880 ——
#   比屏幕还大。多出来的像素一个都跑不掉：补帧要在大图上做（RIFE 对大尺寸极贵），
#   输出那次 CPU 转换也要搬更大的图。屏幕只有 1440p 的话，这些全是白烧。
#
#   实现位置很关键：**超分之后、补帧之前**缩一次（见文件末尾的调度段）。
#   为什么不缩在最后：那样缩的只是"显示的图"，补帧仍然在最贵的尺寸上算 ——
#   而这个开关要省的恰恰是补帧那一步。
#
#   ⚠ 只管上限，绝不放大（源比它小就按源的来）—— "限定分辨率"不是"锁定分辨率"。
#   ⚠ 输出尺寸 = 源 × 模型原生倍率，再受它压一次（没有"输出倍率"旋钮了）。
PLAY_MAX_H = int(_env("LIVE_PLAY_MAX_H", "0") or 0)
# ★ 判据边（2026-09-30 加，配合 GUI 的「预设 / 自定义」单选）：
#   short = 拿**短边**跟 PLAY_MAX_H 比（默认，与旧行为完全一致）
#   long  = 拿**长边**比  —— 给"我想限成 1920 宽"这种诉求用：
#           3840x2160 源 + 限长边 1920 → k = 1920/3840 = 0.5 → 1920x1080
#   ⚠ 与 GUI 的 `capped_play_size(edge=)` 必须一字不差（`_t_playcap_parity.py` 钉住）。
PLAY_MAX_EDGE = _env("LIVE_PLAY_MAX_EDGE", "short").strip().lower()
#   ⚠ 合法值校验**不能写在这里** —— `warn` 定义在本文件 ~397 行，参数区在它之前，
#     在这里调 warn 会 `NameError` → **整条滤镜被 mpv 禁用**（看着像"滤镜没生效"）。
#     照抄下面 `_MATRIX_BAD` 的现成模式：先在常量区**记下**，等 warn 定义好再报。
if PLAY_MAX_EDGE not in ("short", "long"):
    _PLAY_EDGE_BAD = PLAY_MAX_EDGE
    PLAY_MAX_EDGE = "short"
else:
    _PLAY_EDGE_BAD = None
# ★ mpv 注入的源 clip 长度是**哨兵值 2^28 = 268435456**，表示"长度由播放器
#   运行时决定"，不是真实帧数。任何在它之上做**帧数翻倍**的滤镜（Interleave /
#   拼接 Trim）都会让它溢出 → mpv 拿到非法帧数就静默禁用整条滤镜。
#   判据：超过 2^20 的 num_frames 一律当成"动态长度"哨兵，别当真实帧数用。
SENTINEL_MIN = 1 << 20        # 268435456 远远超过任何真实片长，留足余量
# 补帧目标：优先「源帧率 ×n」（更好懂，任何源都不会选错），
# 正数才当「绝对目标帧率」用（60/144，对齐显示器刷新率）。
# 设 0 才回退到「倍率」（兼容旧命令行）。二者都支持非整数。
# "auto" = 自动倍数：源 ≥60fps 取 2x，否则取能塞进 60 的最大整数倍。
AUTO_FPS_MAX = 60
_ifps = _env("LIVE_INTERP_FPS", "auto").strip().lower()
INTERP_FPS_AUTO = _ifps in ("auto", "")
try:
    _ifpsval = 0.0 if INTERP_FPS_AUTO else float(_ifps)
except ValueError:
    # 老调用可能传了非数字（比如 "60fps"）—— 兜回 auto，别让整条滤镜崩
    _ifpsval = 0.0
    INTERP_FPS_AUTO = True
# ★ 负数 = 固定倍数档（源 ×n）：-2/-3/-4 → 2x/3x/4x，与源帧率无关。
INTERP_FPS_MULTI = abs(_ifpsval) if _ifpsval < 0 else 0.0
INTERP_FPS = _ifpsval if _ifpsval > 0 else 0.0
INTERP_MULTI = float(_env("LIVE_INTERP_MULTI", "0"))
# 相似帧阈值（归一化 0~1，0 = 关闭）。k7sfunc 的 stat_th 就干这个：
# 给源帧打上 `_SceneChangeNext`，vsmlrt 的 RIFE 看到就**跳过推理**、直接用左源帧。
STATIC_TH = float(_env("LIVE_STATIC_TH", "0.003"))
RIFE_VER = _env("LIVE_RIFE_VER", "4.26")       # 必须和 models/rife/ 里的文件名对上
DEVICE = int(_env("LIVE_DEVICE", "0"))
# 推理并发流。★ 默认 2 而不是 1 —— 实测 1080p 仅补帧 @60：1 流 0.99x → 2 流 1.09x。
#   （早期"num_streams 无效"的结论是用 丢帧/s 这个指标测的，那个指标会骗人：
#    无音轨 + 窗口不在前台时 time-pos 只走 0.66x，"实时丢帧"根本不可比。
#    现在统一用「处理速度倍率」。）
STREAMS = int(_env("LIVE_STREAMS", "2"))
CUDA_GRAPH = _flag("LIVE_CUDA_GRAPH", True)
# ★ 补帧的 I/O 精度。RIFE 的输入是 11 通道（img0 3 + img1 3 + 时间点 1
#   + 4 个网格量），1080p 下 **fp32 每帧要搬 117 MB**。改成 fp16 后搬运量砍半。
#   协议：喂 RGBH 帧 + backend.output_format=1
#   （vsmlrt 里 input_format 是从帧格式推的，且强制 input==output）。
#   实测（1080p 仅补帧 @60 的处理速度）：0.64x → 0.99x（+55%，含下面的“一步直转”）
#   全链路 720p @48：0.91x → 0.97x。**默认开**。
FP16_IO = _flag("LIVE_FP16_IO", True)
# RIFE 实现版本：**只剩 1**（11 通道 onnx，models/rife/）
#   pad 倍数**随模型变**（见 `_interp_rife`）：4.26/4.25_heavy = 64，
#   4.25_lite = **128** —— 日志里的「凑 N 倍数」必须跟着模型走，别写死。
#   ★ 2026-09-30 删除 impl=2（7 通道 rife_v2/）：本机 TRT 11 解析不了它的
#     混合精度 /Mul（`ElementWiseOperation PROD must have same input types`），
#     模型已从磁盘删掉（429MB），这条分支再留着只会误导。
IMPL = 1                                        # 固定 1，不再自动探测
# 补帧**引擎**（不只是模型版本）。这是 2026-09-30 加的第二层选择：
#   mv     = mvtools 传统光流补帧（**默认**：纯 CPU，零显存、零 TRT 依赖）
#   rife   = vsmlrt 的 C++ TensorRT 版 RIFE（最快，但要装 TensorRT、吃显存）
#   dup    = 帧复制（每帧重复一次）。**零成本零插件**，但运动是顿挫的
#   blend  = 帧混合（相邻帧线性插值）。**零成本零插件**，运动处有重影
#   ★ 为什么默认改成 mv：RIFE 走 TRT，而 TensorRT 不是解压即用的东西
#     （要装 nvinfer/nvonnxparser 全家 + 匹配版本），换台机器就未必能跑。
#     mv 只需要 vapoursynth 的 plugins/mvtools.dll，是真正的"拷走就能用"。
#     想要 RIFE 的画质就显式设 LIVE_VFI=rife（本机装了 TRT 时）。
#   ★ 曾有第 5 个引擎 `vsrife`（vs-rife 的 PyTorch 版），**2026-09-30 已删除**：
#     TRT 路径在本机（torch-tensorrt 2.13 + TRT 11.0）编译必失败，非 TRT 路径
#     又只是把 RIFE 换个壳重跑（15.5ms vs 3.5ms，慢 4 倍）且不能与超分同开，
#     **没有任何它独有能力**。不能用的东西留在界面/代码里只会误导。
#   ★ NvOF（NVIDIA Optical Flow）不可用，已查证：官方 mvtools 构建不含该
#     功能；加了 NvOF 支持的 galpt 分支**没有任何 release，CI 23 次全失败、
#     0 个 artifact**，即全世界没有现成的 Windows NvOF DLL。要自己编需
#     VS C++ 工作负载 + CUDA Toolkit + NVIDIA OF SDK（开发者账号 + EULA）。
#     且社区实测 NvOF 的 cost field **不能当置信度**用（静止画面误报向量）。
VFI = _env("LIVE_VFI", "mv").strip().lower()
# mvtools 专有：预设 fast（pel=1 blk=32 无重叠）/
#              mid（pel=1 blk=16 ov=4）/ best（pel=2 blk=16 ov=8）
_MV_PRESET = _env("LIVE_MV_PRESET", "fast").strip().lower()
MV_PARAMS = {"fast": (1, 32, 0), "mid": (1, 16, 4), "best": (2, 16, 8)}.get(
    _MV_PRESET, (1, 32, 0))

# ★★ mvtools 的**内部线程数**（pinterf 版 `opt` 参数），2026-09-30 实测后加。
#   这是本文件里唯一对 mv 补帧吞吐有真实影响的旋钮 —— 说明：
#
#   1) 「mvtools 吃不满 CPU」的观察**是对的**（2026-09-30 晚间口径修正）：
#      psutil 的 Process.cpu_percent 是**单核=100%** 口径（raw%÷100 = 逻辑核数，
#      千万别乘 NCORE/100 —— 早期把 122% 误算成「24.4 核」）。修正后：
#        整链（BlankClip 测试）  ~1.2 核（峰值 2.1）← 静态图走捷径，busy 虚低
#        真播 mpv 全链（4K）     ~3.7 核（含解码+VS+VO）
#      → CPU 远没满核。1080p 整链 3.94 ms/出帧 → 253 fps，播放只要 48，
#        **实时余量 5.3 倍**（fps 是墙钟实测，不受口径影响）。
#      4K 掉帧的根因是**单帧延迟**（Super→Analyse→BlockFPS 串行依赖链），
#      不是吞吐不足、也不是内存带宽饱和。测 CPU 占用用后台线程累计采样
#      （`_t_mv_cpu2.py`），raw%÷100 读数。
#
#   2) **唯一真能拿的加速**：`opt` 默认是 -1（自动），实测自动值偏保守。
#        1280x720 整链「仅 Analyse」：
#          opt=-1  2.32 ms/帧   ← 库自己定的，慢
#          opt=0   2.46          （0 = 关内部线程，最慢，反而佐证 -1 不是 0）
#          opt=4   1.51          ← 比 -1 快 35%
#          opt=8   1.65
#          opt=12  1.47          ← 和 4 差不多
#        整链带上 BlockFPS 后差距被摊薄（1.63 → 1.59 ms，只快 2.5%），
#        因为 BlockFPS 本身是串行大头。所以默认取 4：拿满收益、不冒进。
#      ⚠ 超过 ~12 后收益消失甚至回退（opt=32 → 1.78）：物理核只有 12 个。
#
#   3) **不要调 `core.num_threads` 去治这个**，实测无效：
#        threads=1 → 1.96s   threads=4 → 1.82s   threads=12 → 1.63s
#        threads=20 → 1.61s  threads=32 → 1.64s
#      VS 只负责在 filter **图**这一级调度；mvtools 内部自己开线程池，
#      所以 core.num_threads 在 2 以上就没区别（差距全在 mpv 侧的开销里）。
MV_OPT = int(_env("LIVE_MV_OPT", "4"))
#   -1 是库的合法值（自动），保留；比 -1 还小的是无意义输入，拉回默认。
#   上界不砍 —— 库自己会按实际核数处理，压到 4 反而掩盖"用户想试更大"的意图；
#   只是实测 >12 无收益（物理核 12 个），写在文档里而不是代码里强改。
if MV_OPT < -1:
    MV_OPT = 4

# ★ 4K 自适应开关（2026-09-30 加）：源宽 ≥2560 时 mv 的 blksize 翻倍（≤64）。
#   实测把 4K 仅补帧从 43.4fps（不够 48）拉到 58.5fps（余量 1.22x）。
#   ⚠ 曾同时关色度（chroma=False）多拿 4%，但那会让**整个画面变绿**
#     （mvtools 的 chroma=False = 色度平面填 0，不是「忽略色度匹配」），
#     已在 2026-09-30 深夜撤销，见 `_interp_mv` 里的详细取证。
#   0 = 关闭（回到单一 blksize 的旧行为）。
_MV_HD = _flag("LIVE_MV_HD", True)
#   opt 是 2023+ pinterf 版新增参数；老构建不认 → 传了会抛
#   `Analyse: Function does not take argument "opt"`。谁认不出来就退回不带 opt。
#   （本机 mvtools.dll 8.6MB 支持 opt + search/search_coarse/dct 全套）
_MV_HAS_OPT = True
try:
    _probe = core.mv.Super(core.std.BlankClip(width=64, height=64,
                                              format=vs.YUV420P8, length=2),
                           pel=1, opt=MV_OPT)
    _probe.get_frame(0)
except Exception:
    _MV_HAS_OPT = False

# VS 工作线程数。★ 别用默认值 —— 本机默认是 20（=CPU 核数）。
#   ⚠ 但对 **mv 补帧无效**（见上，mvtools 内部有独立线程池）；它的真实用途是
#     限制 **RIFE/TRT 路线**的 CPU 抢占 —— 那条路 GPU 推理串行，20 个 VS 线程
#     互相抢 CPU 只会加开销。0 = 用 VS 默认。
VS_THREADS = int(_env("LIVE_VS_THREADS", "0"))
if VS_THREADS > 0:
    core.num_threads = VS_THREADS

# ★ 色彩矩阵：**必须做白名单映射**（2026-09-30 修）。
#   zimg 只认全名（709 / 2020ncl / 470bg / 170m ...），喂它 "601" 会：
#       Error: Resize error: bad value: matrix_in_s
#   → 图构建失败 → **整个 vapoursynth 滤镜被 mpv 禁用**（退化成放源码流，
#     用户看到的就是"超分没生效"）。
#   ⚠ GUI 的「BT.601（老 SD 素材）」下拉历史上传的正是 "601" —— 一个
#     选了就必炸的值。这里统一做别名修正，并且**非法值退回默认**而不是抛错，
#     因为"因为我认不出这个矩阵名、所以整条滤镜都不给你用"是最糟的选择。
_MATRIX_ALIAS = {
    "709": "709", "bt709": "709", "rec709": "709",
    "601": "470bg", "bt601": "470bg", "rec601": "470bg", "470bg": "470bg",
    "smpte170m": "170m", "170m": "170m",
    "2020": "2020ncl", "2020ncl": "2020ncl", "bt2020ncl": "2020ncl",
    "2020cl": "2020cl", "bt2020cl": "2020cl",
    "470m": "470m", "fcc": "fcc", "240m": "240m", "ycgco": "ycgco",
}
_MX_RAW = _env("LIVE_MATRIX", "709").strip()
MATRIX = _MATRIX_ALIAS.get(_MX_RAW.lower())
if MATRIX is None:
    MATRIX = "709"
    _MATRIX_BAD = _MX_RAW
else:
    _MATRIX_BAD = None
VERBOSE = _flag("LIVE_VERBOSE", True)


def log(msg: str) -> None:
    if VERBOSE:
        print(f"[live] {msg}", file=sys.stderr, flush=True)


def warn(msg: str) -> None:
    print(f"[live][!] {msg}", file=sys.stderr, flush=True)


# 非法的色彩矩阵名在这里才报（warn 刚定义好）——
# 放在常量区是因为那里还没法 warn，但**必须报出来**：
# 用户选了个 zimg 不认识的名字、被静默换成 709，不说的话他会以为是别的地方出问题。
if _MATRIX_BAD is not None:
    warn(f"色彩矩阵 {_MATRIX_BAD!r} 不认识（zimg 只认全名：709 / 470bg / 170m / "
         f"2020ncl ...）→ 退回 709。不这么做的话 zimg 会报 "
         f"'bad value: matrix_in_s' 并**禁用整条滤镜**。")

# 同理：「限定播放分辨率」的判据边（同 `_MATRIX_BAD` 的延迟报警模式）
if _PLAY_EDGE_BAD is not None:
    warn(f"LIVE_PLAY_MAX_EDGE={_PLAY_EDGE_BAD!r} 不认识（只认 short / long）"
         f"→ 按 short（短边）处理。")

# 同理：「导入分辨率上限」的判据边
if _SR_EDGE_BAD is not None:
    warn(f"LIVE_SR_MAX_EDGE={_SR_EDGE_BAD!r} 不认识（只认 short / long）"
         f"→ 按 short（短边）处理。")


# ---------------------------------------------------------------- 超分档位
# 与 models/engines/ 里实际存在的 .engine 一一对应（不是拍脑袋写的）。
LADDER_ANIMEV3 = {
    (640, 360): "realesr-animevideov3_fp16_{w}x{h}.engine",
    (640, 480): "realesr-animevideov3_fp16_{w}x{h}.engine",
    (720, 480): "realesr-animevideov3_fp16_{w}x{h}.engine",
    (854, 480): "realesr-animevideov3_fp16_{w}x{h}.engine",
    (960, 720): "realesr-animevideov3_fp16_{w}x{h}.engine",
    (1280, 720): "realesr-animevideov3_fp16_{w}x{h}.engine",
    (1920, 1080): "realesr-animevideov3_fp16_{w}x{h}.engine",
}
LADDER_RE2X = {
    (640, 360): "realesr-animevideov3_re2x_fp16_{w}x{h}.engine",
    (854, 480): "realesr-animevideov3_re2x_fp16_{w}x{h}.engine",
    (960, 720): "realesr-animevideov3_re2x_fp16_{w}x{h}.engine",
    (1280, 720): "realesr-animevideov3_re2x_fp16_{w}x{h}.engine",
    (1920, 1080): "realesr-animevideov3_re2x_fp16_{w}x{h}.engine",
}
# 用哪个超分模型：animev3（4x，官方 animevideov3）/ realesr-animevideov3_re2x（2x，轻量，快得多）
#   还支持 AnimeJaNai 系列（2x，见 models/sr_aj/，用 build_aj_engines.py 编）：
#     AnimeJaNai_HD_V3.1_Performance          最快、锐度略低
#     AnimeJaNai_HD_V3.1Sharp1_Performance    同上 + 加锐
#     AnimeJaNai_HD_V3.1_Balanced             质量档（默认推荐）
#     AnimeJaNai_HD_V3.1Sharp1_Balanced       质量档 + 加锐
#
#   ★★ 2026-09-30 新增 Anime4KCPP 系列（**不是 TRT 引擎，是 VS 原生插件**）：
#     值写成 `anime4kcpp:<模型名>`，例如 `anime4kcpp:acnet-gan`。
#     插件 DLL：`.venv/Lib/site-packages/vapoursynth/plugins/anime4kcpp.dll`
#     （源 = Anime4KCPP v3.2.0 release 里的 ac_filter_avs_vs.dll，改名而来）。
#     暴露 `core.anime4kcpp.ACUpscale(clip, factor, processor, device, model)`。
#
#     ⚠⚠ 为什么值得加（本机实测，同输出尺寸、RTX 4080）：
#         源 1280x720 → 2560x1440：  acnet-gan  4.02 ms/f
#                                    TRT re2x  20.34 + 转换 2.85 ≈ 23.19 ms/f
#         → **快 5.8×**。1080p 档（→3840x2160）：7.92 vs 73.42 → **快 9.3×**。
#       原因：插件是 **YUV 原生、零格式转换**（整条链路本来就是 YUV420P8），
#       而 TRT 路径必须 YUV→RGBS(float) →引擎→ RGBS→YUV，两次 float 往返
#       每帧要搬几百 MB 主机内存 —— 这笔搬运费比推理本身贵得多。
#     ⚠ 代价：Anime4KCPP **没有原生 4x 模型**。ACNet/ARNet/ArtCNN/FSRCNNX
#       全部是 2x 网络，`factor` 参数虽然接受 1.5/2/3/4（实测都能出图），
#       但 >2 的部分是插值放大、不是网络输出，画质不如老老实实 2x。
#       → 这里 factor 固定 2.0（原生），**不再凑用户选的输出倍率**，
#         超过 2x 的部分交给最后那步 bicubic 降采样/放大处理。
SR_MODEL = _env("LIVE_SR", "animev3").lower()
# 是否是 Anime4KCPP 插件路线（True 就不进 TRT 那套 ladder / engine 逻辑）
IS_A4K = SR_MODEL.startswith("anime4kcpp")
# 插件路线下要用的模型名（`anime4kcpp:acnet-gan` → `acnet-gan`；不带冒号则用默认）
A4K_MODEL = (SR_MODEL.split(":", 1)[1].strip()
             if ":" in SR_MODEL else "acnet-gan") or "acnet-gan"
# 用户是否显式指定了超分模型（"auto" = 没指定，允许 LR 时自动换成 2x 模型）
_SR_USER_FORCED = SR_MODEL not in ("auto", "")


def _sr_ladder_dirs() -> list[tuple[str, Path]]:
    """返回该模型对应的 (文件名模板, 引擎目录) 候选列表，按优先级。

    ★ 分开成「目录 + 模板」而不是单一 dict：AnimeJaNai 有 4 个变体共享同一目录，
      如果每个都写一份 ladder 会重复四遍；而且它们的**档位集合是固定的**，
      由 build_aj_engines.py 的 SIZES 决定。

    ★ Anime4KCPP 路线（IS_A4K）**不走这里** —— 它是插件不是引擎，
      没有任何 .engine 文件可扫，`factor` 直接决定输出尺寸。返回空列表，
      上层 `_scan_ladder()` 会得到空 LADDER，但 `stage_upscale` 会走 A4K 分支。
    """
    if IS_A4K:
        return []
    if SR_MODEL in ("realesr-animevideov3_re2x", "re2x"):
        return [("realesr-animevideov3_re2x_fp16_{w}x{h}.engine", ENGINE_DIR),
                ("realesr-animevideov3_re2x_fp16io_{w}x{h}.engine", ENGINE_DIR)]
    if SR_MODEL.startswith("animejanai"):
        # 短名 → 磁盘上的短名（大小写不敏感匹配，容错用户手打）
        aj = {"animejanai_hd_v3.1_performance": "AnimeJaNai_HD_V3.1_Performance",
              "animejanai_hd_v3.1sharp1_performance": "AnimeJaNai_HD_V3.1Sharp1_Performance",
              "animejanai_hd_v3.1_balanced": "AnimeJaNai_HD_V3.1_Balanced",
              "animejanai_hd_v3.1sharp1_balanced": "AnimeJaNai_HD_V3.1Sharp1_Balanced",
              "animejanai": "AnimeJaNai_HD_V3.1_Balanced"}      # 别名 → Balanced
        short = aj.get(SR_MODEL, "AnimeJaNai_HD_V3.1_Balanced")
        return [(f"{short}_fp16io_{{w}}x{{h}}.engine", AJ_ENGINE_DIR)]
    # 默认 animev3（4x，官方 animevideov3）
    return [("realesr-animevideov3_fp16_{w}x{h}.engine", ENGINE_DIR),
            ("realesr-animevideov3_fp16io_{w}x{h}.engine", ENGINE_DIR)]


SR_CANDIDATES = _sr_ladder_dirs()
# 档位 = 该模型**实际有引擎**的那些尺寸（扫目录，不写死）
def _scan_ladder() -> dict[tuple[int, int], tuple[str, Path]]:
    out: dict[tuple[int, int], tuple[str, Path]] = {}
    for tmpl, d in SR_CANDIDATES:
        if not d.is_dir():
            continue
        import re as _re
        pat = _re.compile("^" + tmpl.format(w=r"(\d+)", h=r"(\d+)") + "$")
        for f in sorted(d.glob("*.engine")):
            m = pat.match(f.name)
            if m:
                out[(int(m.group(1)), int(m.group(2)))] = (tmpl, d)
    return out


LADDER = _scan_ladder()
# 原生倍率：AnimeJaNai / re2x 都是 2x；官方 animevideov3 是 4x；Anime4KCPP 也是 2x
#   （ACNet/ARNet/ArtCNN/FSRCNNX 网络本身就是 2x；factor>2 只是插值，不算"原生"）
SR_NATIVE_SCALE = 4 if (SR_MODEL == "animev3") else 2
# ★ Anime4KCPP 路线没有引擎可扫，空 LADDER 是**正常状态**，不能报"没找到引擎"。
if not LADDER and not IS_A4K:
    warn(f"超分模型 {SR_MODEL} 在 {[str(d) for _, d in SR_CANDIDATES]} 里没找到任何引擎"
         f" → 超分会被跳过（跑 build_aj_engines.py 或 build_fp16io_engines.py 编）")
# Anime4KCPP 路线的可用性检查：插件必须真的注册上了。
# ★ 做成「只在选了 A4K 时才探测」—— 没选就不该因为插件缺失而影响任何东西。
A4K_OK = False
A4K_PROCESSOR = _env("LIVE_A4K_PROCESSOR", "cuda").strip().lower()
if IS_A4K:
    if A4K_PROCESSOR not in ("cpu", "cuda", "opencl"):
        warn(f"Anime4KCPP processor={A4K_PROCESSOR!r} 不认识"
             f"（可选 cpu/cuda/opencl）→ 退回 cuda")
        A4K_PROCESSOR = "cuda"
    try:
        if getattr(core, "anime4kcpp", None) is None:
            warn("选了 Anime4KCPP 超分，但核心命名空间里没有 `anime4kcpp` —— "
                 "插件没装好。把 Anime4KCPP release 里的 ac_filter_avs_vs.dll "
                 "改名成 anime4kcpp.dll 放到 vapoursynth/plugins/ 下。→ 跳过超分")
        else:
            A4K_OK = True
    except Exception as e:                            # noqa: BLE001
        warn(f"Anime4KCPP 插件探测失败（{type(e).__name__}: {str(e)[:120]}）→ 跳过超分")


def pick_ladder(w: int, h: int) -> tuple[int, int]:
    """给源尺寸挑一个引擎档位。

    优先级：① 宽高比接近（±5%）且宽度不小于源 → 取最窄的；
            ② 退而求其次：只要宽度不小于源 → 取最窄的；
            ③ 都不满足（源比所有档都大）→ 用最大档（会先降采样，损失可接受）。

    ★ SR_MAX 是「档位像素上限」（LIVE_SR_MAX，按**短边**判）：命中时**先砍档位集合**
      再走上面的优先序。为什么需要它：超分成本 ~正比于档位像素数，
      1080p 档（1920x1080→3840x2160）隔离实测 26.9ms/帧，配 RIFE 后全链路 ~30ms，
      在 48fps（预算 20.83ms）下必然丢帧；降到 960x720 档只要 7.9ms。
      代价是中间推理分辨率降低，但 AnimeJaNai 是 2x 模型，最终仍能到 2K 输出。
      ⚠ 但降档**只让"只超分"更宽裕，救不了"超分+补帧"全链路**（见文件头）。

    ⚠ 判据用**短边**（min(w,h)）：「1080p 档」指的就是 1920x1080（短边 1080），
      跟档位命名一致。用长边判反而会把 1920x1080 砍掉、语义拧了。
      实测 srmax=960 时：1080p 源从 1920x1080 档掉到 1280x720 档（26.9ms → 12.4ms），
      720p 源保持 1280x720（短边 720 ≤ 960，本来就在预算内，不必动）。
    """
    """给 src 尺寸挑引擎档位（pick_ladder 见下）。档位集合来自 LADDER。

    ★ SR_MAX + SR_MAX_EDGE：命中时**先砍档位集合**再挑。
      SR_MAX_EDGE="short"（默认）→ 比**短边**：1080p 源填 1080 仍挑 1920x1080；
      SR_MAX_EDGE="long"        → 比**长边**：填 1920 也挑 1920x1080（填宽度更直观）。
      两者表达同一件事，只是"拿哪条边当判据"不同。
    """
    pool = LADDER
    if SR_MAX > 0:
        capped = {t: v for t, v in LADDER.items()
                  if (max(t) if SR_MAX_EDGE == "long" else min(t)) <= SR_MAX}
        if capped:
            pool = capped
        else:
            warn(f"LIVE_SR_MAX={SR_MAX}（按 {SR_MAX_EDGE}）砍掉了所有档位"
                 f"（可用 {sorted(LADDER)}）→ 忽略上限")
    ar = w / h
    cands = [t for t in pool if abs(t[0] / t[1] - ar) / ar < 0.05 and t[0] >= w]
    if not cands:
        cands = [t for t in pool if t[0] >= w]
    if cands:
        return min(cands, key=lambda t: t[0] * t[1])
    return max(pool, key=lambda t: t[0] * t[1])


# ---------------------------------------------------------------- 入口
# ⚠ mpv 的 vapoursynth 滤镜会向脚本注入这些全局变量（实测 R77 + mpv 0.41）：
#     video_in        输入 clip（VideoNode）   ← 用它当源
#     video_in_dw/dh  输入的显示宽高
#     container_fps   容器帧率（float）
#     display_fps     显示器刷新率（0 = 未知）
#     display_res     显示器分辨率
#     user_data       对应 --vf=vapoursynth=user_data=... 传进来的串
#   注意：**没有** `clip` 这个名字 —— 那套是 vspipe 的约定，别混用。
try:
    src = video_in                                # noqa: F821  (mpv 注入)
except NameError:
    raise RuntimeError(
        "本脚本是给 mpv 的 vapoursynth 滤镜用的，必须由 mpv 加载（它会注入 video_in）。"
        "直接 `python live.vpy` 跑不了。"
    )

src_w, src_h = src.width, src.height

# mpv 给的 clip 有时不带 fps 属性（fps_num/den 都 0）。补帧靠 AssumeFPS 把帧率乘上去，
# 属性缺失时那步会静默失效 → 补到 48fps 的流被 mpv 当 24fps 播（等于慢放）。
# 用 mpv 注入的 container_fps 兜底补上。LIVE_ASSUME_FPS 可手工覆盖。
if src.fps_num == 0 or src.fps_den == 0:
    cf = float(_env("LIVE_ASSUME_FPS", "0") or 0) or \
         float(globals().get("container_fps", 0) or 0)
    if cf > 0:
        fr = Fraction(cf).limit_denominator(1001)
        src = src.std.AssumeFPS(fpsnum=fr.numerator, fpsden=fr.denominator)
        log(f"源无 fps 属性 → 按 container_fps={cf} 设为 {fr.numerator}/{fr.denominator}")

log(f"源 {src_w}x{src_h}  格式={src.format.name}  fps={src.fps!s}")

# ★ 2026-09-30 加（默认关，`LIVE_PROBE_DUR=1` 开）：试着在**构建期**读输入帧的真实时长。
#   动机：mpv 算滤镜后的 pts 时**只累加帧属性** `_DurationNum/_DurationDen`，而本脚本
#   开头会无条件 `AssumeFPS(container_fps)` 把逐帧时长**拍平成 1/container_fps**
#   （实测：Input 1/30 → AssumeFPS(24) → 1/24）。若 container_fps 与真实节奏不符、
#   或源是 VFR，时间轴就会整体偏 —— 而 mpv 的 avsync 全程 ≈ 0，看不见。
#   这里先确认"构建期能不能拿到真实时长"，为后续的校正/告警铺路。
#   ⚠ mpv 在 init 期间收到帧请求会回一个黑桩帧（不会死锁），所以这里试一下是安全的。
if str(_env("LIVE_PROBE_DUR", "")).strip() not in ("", "0"):
    try:
        _p0 = src.get_frame(0)
        log(f"[probe] 输入帧0 属性: _DurationNum={_p0.props.get('_DurationNum')} "
            f"_DurationDen={_p0.props.get('_DurationDen')} "
            f"→ 时长={(_p0.props.get('_DurationNum') or 0) / max(1, (_p0.props.get('_DurationDen') or 1)):.6f}s"
            f"（container_fps={globals().get('container_fps', '?')}）")
    except Exception as _pe:                                # noqa: BLE001
        log(f"[probe] 读输入帧0 失败: {type(_pe).__name__}: {str(_pe)[:100]}")


# ------------------------------------------------- 限定播放分辨率（补帧的工作尺寸）
def _native_out_size() -> tuple[int, int]:
    """**没有**「限定播放分辨率」时的输出尺寸 = 源 × 模型原生倍率。

    ★ 2026-09-30 起没有"输出倍率"旋钮了（详见文件头 LIVE_OUT_SCALE 的删除说明）。
      超分关 → 尺寸不变（补帧不改尺寸，且我们只缩不放大）。
      超分开 → 源 × SR_NATIVE_SCALE（animev3 = 4，其余 2）。
    """
    if not UPSCALE_ON:
        return src_w, src_h
    return int(round(src_w * SR_NATIVE_SCALE)), int(round(src_h * SR_NATIVE_SCALE))


def _play_cap_size() -> tuple[int, int] | None:
    """算「限定播放分辨率」下最终该输出多大。返回 None = 不用缩。

    ★ 语义：`源 × 模型原生倍率` 再受 PLAY_MAX_H 压一次。
      —— 原来这里还有个"输出倍率"，已删（和 cap 职责重叠，见文件头）。
    ★ 只管上限：源比上限还小就按源来，**绝不放大**（"限定" ≠ "锁定"）。
    ★ 判据边由 PLAY_MAX_EDGE 决定：short = 短边（默认）/ long = 长边。
      等比缩放；取偶数（YUV420 要求）。
    """
    if PLAY_MAX_H <= 0:
        return None
    ow, oh = _native_out_size()
    # ★ 判据边（与 GUI capped_play_size 的 edge 参数必须一致）
    ref = max(ow, oh) if PLAY_MAX_EDGE == "long" else min(ow, oh)
    if ref <= PLAY_MAX_H:
        return None                     # 本来就没超，不用动
    k = PLAY_MAX_H / float(ref)
    # YUV420 要求宽高都是偶数
    return (((int(round(ow * k)) + 1) // 2) * 2,
            ((int(round(oh * k)) + 1) // 2) * 2)


def fit_play_cap(clip):
    """把 clip 缩到「限定播放分辨率」以内（只缩不放大）。返回 (clip, 是否缩过)。

    ⚠ 必须**在超分之后、补帧之前**调用：
      这个开关要省的是**补帧**那一步（RIFE 对大尺寸极贵），缩在最后就白设了。
      缩在补帧之后只是让显示小一点，补帧的成本一分没少。
    """
    tgt = _play_cap_size()
    if tgt is None:
        return clip, False
    tw, th = tgt
    if (clip.width, clip.height) == (tw, th):
        return clip, False
    out = core.resize.Bicubic(clip, tw, th, filter_param_a=1 / 3,
                              filter_param_b=1 / 3)
    _edge_cn = "长边" if PLAY_MAX_EDGE == "long" else "短边"
    log(f"限定播放分辨率 {PLAY_MAX_H}（按{_edge_cn}）："
        f"{clip.width}x{clip.height} → {tw}x{th}"
        f"（补帧就在这个尺寸上做）")
    return out, True


def _stage_upscale_a4k(clip, src_w: int, src_h: int):
    """Anime4KCPP 插件路线：**YUV 直通**超分，零 float 往返。

    ★ 为什么要单独一条路（本机实测同输出尺寸）：
        1280x720→2560x1440：插件 acnet-gan 4.02 ms/f vs TRT re2x 合计 23.19 ms/f
        1920x1080→3840x2160：插件 7.92 ms/f vs TRT 合计 73.42 ms/f
      差 5.8~9.3 倍，全在「省掉 YUV→RGBS→引擎→RGBS→YUV 那两次 float 往返」上。

    ⚠ 输入要求：Anime4KCPP 只吃 **YUV**（Gray8/16、YUV444P8/16、YUV422P8/16、
      YUV420P8/16，含 float 变体），喂 RGBS 会直接报错。调用方传进来的是 RGBS/RGBH，
      所以这里要做一次 RGB→YUV420P8 的转换（8bit，因为 8bit 显示器上 P8 足够，
      而且 8bit 搬运量减半 —— 实测也是 P8 更快）。
    ⚠ factor 恒为 2.0（原生）：Anime4KCPP **没有原生 4x 模型**（全是 2x 网络），
      旧代码里的 `min(2.0, need)` 是为了凑"用户选的输出倍率" —— 那个旋钮已删
      （2026-09-30），现在老老实实按原生 2x 出，剩下的尺寸调整交给 cap。
    """
    # 目标输出尺寸 = 源 × 原生 2x（和 TRT 路线语义一致：都出"模型原生尺寸"）
    ow, oh = int(round(src_w * 2)), int(round(src_h * 2))
    factor = 2.0
    # ★ 8bit 输入：整条链路是 8bit 源就 P8，否则也仍用 P8 ——
    #   A4K 的 P16 路径实测没有更快，而后面输出反正要转回去。
    yuv = clip
    if yuv.format.color_family != vs.YUV:
        yuv = core.resize.Bicubic(yuv, format=vs.YUV420P8, matrix_s=MATRIX)
    elif yuv.format.bits_per_sample != 8:
        yuv = core.resize.Bicubic(yuv, format=vs.YUV420P8)
    log(f"超分：Anime4KCPP 插件，模型 {A4K_MODEL}，processor={A4K_PROCESSOR}，"
        f"factor={factor:g}（{yuv.width}x{yuv.height} → "
        f"{int(round(yuv.width * factor))}x{int(round(yuv.height * factor))}）")
    t0 = time.perf_counter()
    try:
        hr = core.anime4kcpp.ACUpscale(
            yuv,
            factor=factor,
            processor=A4K_PROCESSOR,
            device=DEVICE,
            model=A4K_MODEL,
        )
    except Exception as e:                        # 插件失败要能降级，不能整条挂掉
        warn(f"Anime4KCPP 超分失败（{type(e).__name__}: {str(e)[:160]}）→ 跳过超分")
        return clip, False
    # 转回 RGBS/RGBH 交给下游（下游 stage_interp 和最终输出都要 RGB float）
    hr = core.resize.Bicubic(hr, format=BASE_FMT)
    # ★ 对齐到「源 × 原生 2x」（clip 尺寸可能≠源尺寸，见上）。
    #   但只在 need>2 时才发生（即用户选了 ×3/×4 输出倍率）——已在上面注释说明。
    if (hr.width, hr.height) != (ow, oh):
        hr = core.resize.Bicubic(hr, ow, oh, filter_param_a=1 / 3,
                                 filter_param_b=1 / 3)
    log(f"  {time.perf_counter() - t0:.2f}s 完成图构建 → 输出 {ow}x{oh}")
    return hr, True


# ---------------------------------------------------------------- 两个处理阶段
def stage_upscale(clip):
    """RGBS 进 → 超分（选档 → TRT 引擎 → 尺寸对齐到 源×原生倍率）→ RGBS 出。

    ★ Anime4KCPP 走另一条分支（见 `_stage_upscale_a4k`）：它是插件不是 TRT 引擎，
      不需要 pick_ladder / .engine 那套，直接 factor 放大。

    返回 (clip, 是否成功)。失败不是抛异常而是降级返回原 clip，
    因为 mpv 那边只看到「滤镜失败」就会把整条链禁掉，用户看不到根因。
    """
    # ★ Anime4KCPP 插件路线（LIVE_SR=anime4kcpp:<模型>）
    if IS_A4K:
        if not A4K_OK:
            return clip, False
        return _stage_upscale_a4k(clip, src_w, src_h)

    lw, lh = pick_ladder(src_w, src_h)
    tmpl, edir = LADDER[(lw, lh)]
    eng = edir / tmpl.format(w=lw, h=lh)
    # ★ fp16 I/O 版引擎：**I/O 也是 fp16**，每帧过主机内存的量直接砍半 ——
    #   1080p re2x 实测 118.6 MB → 59.4 MB（in 23.7→11.9，out 94.9→47.5）。
    #   默认那批 `_fp16_*.engine` 的 `fp16` 只是**构建精度**，I/O 仍是 fp32。
    #   AnimeJaNai 的引擎**天生就是 fp16io**（onnx 自己 I/O 就是 fp16，
    #   文件名里已含 `_fp16io_`），所以这里对它是空操作。
    if FP16_IO and "_fp16io_" not in eng.name:
        alt = edir / tmpl.replace("_fp16_", "_fp16io_").format(w=lw, h=lh)
        if alt.is_file():
            eng = alt
    fp16io = "_fp16io_" in eng.name
    if not eng.is_file():
        warn(f"缺引擎 {eng.name} → 跳过超分。可用档位：{sorted(LADDER)}")
        return clip, False

    if (lw, lh) != (clip.width, clip.height):
        clip = core.resize.Bicubic(clip, lw, lh, filter_param_a=1 / 3,
                                   filter_param_b=1 / 3)
    # 帧格式必须和引擎的 I/O 类型一致，否则 vs-trt 直接报
    # `operator (): bits per sample mismatch` 然后整段超分被跳过。
    if fp16io and clip.format.bits_per_sample != 16:
        clip = core.resize.Bicubic(clip, format=vs.RGBH)
        log("  超分输入转 RGBH（引擎是 fp16 I/O）")
    elif not fp16io and clip.format.bits_per_sample != 32:
        clip = core.resize.Bicubic(clip, format=vs.RGBS)
        log("  超分输入转回 RGBS（引擎是 fp32 I/O）")
    log(f"超分：档位 {lw}x{lh}（输入 {clip.width}x{clip.height}）"
        f" → 原生 {SR_NATIVE_SCALE}x，引擎 {eng.name}"
        f"（{'fp16' if fp16io else 'fp32'} I/O）")
    t0 = time.perf_counter()
    try:
        hr = core.trt.Model(
            [clip], str(eng),
            device_id=DEVICE,
            use_cuda_graph=1 if CUDA_GRAPH else 0,
            num_streams=STREAMS,
            verbosity=2,
        )
    except Exception as e:                        # 引擎加载失败要能降级，不能整条挂掉
        warn(f"TRT 引擎加载失败（{type(e).__name__}: {str(e)[:160]}）→ 跳过超分")
        return clip, False

    # 原生倍率推理 → 尺寸对齐到「源 × 原生倍率」（bicubic 抗锯齿）
    #   ⚠ 需要这一步是因为**档位尺寸**未必等于源尺寸（pick_ladder 可能选了个
    #     不小于源的档位，如 1000x600 源 → 1280x720 档），推理输出是
    #     档位×2，而"该有的输出"是 源×2。两者不等时缩一次对齐。
    ow, oh = int(round(src_w * SR_NATIVE_SCALE)), int(round(src_h * SR_NATIVE_SCALE))
    if (hr.width, hr.height) != (ow, oh):
        hr = core.resize.Bicubic(hr, ow, oh, filter_param_a=1 / 3,
                                 filter_param_b=1 / 3)
    log(f"  {time.perf_counter() - t0:.2f}s 完成图构建 → 输出 {ow}x{oh}")
    return hr, True


# 相似帧检测放在独立模块里 —— 它有单元测试（_t_static_unit.py，钉住前后帧偏移
# 方向、整数/浮点阈值换算），内联在 .vpy 里就没法测了。
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import live_static                                       # noqa: E402


def _report_static(skip: int, total: int) -> None:
    log(f"  相似帧：{skip}/{total}（{skip / total * 100:.0f}%）跳过推理")


def interp_multi(clip):
    """决定补帧倍率：优先按**目标输出帧率**算，否则用倍率。

    ★ 返回 Fraction 而不是 float —— vsmlrt 的 RIFE 对非整数倍率
      **只接受 fractions.Fraction**（`isinstance(multi, (int, Fraction))` 检查），
      传 float 直接 TypeError。而 24fps→60fps 正好是 2.5，是最常用的档。
      正好整数时仍返回 int，走 vsmlrt 的整数分支（更快、更稳）。

    ★★ 2026-09-30 改语义：LIVE_INTERP_FPS 从「绝对目标帧率」改成
      「**源帧率 × n**」编码，三态：
        0 / "auto"  → 自动倍数：源 ≥60fps 取 2x；源 <60fps 取能塞进 60 的
                      最大整数倍（24→2x=48、30→2x=60、25→2x=50）。
                      ★ 旧逻辑对 ≥60fps 源给 1x（= 完全不插帧），等于白选。
        负数 -n     → **固定 n 倍**，与源帧率无关（-2/-3/-4 → 2x/3x/4x）。
                      这是新档位的主力：无论源是 23.976 还是 29.97 都不会选错。
        正数 fps    → 绝对目标帧率（60/144），倍率 = fps / 源帧率。
                      对齐显示器刷新率时才用；非整数倍会多调一次 RIFE。
    ★ 与 live_gui.py 的 `resolve_multi()` / `resolve_target_fps()` 逐条对齐。
    """
    src_fps = clip.fps if getattr(clip, "fps_num", 0) else Fraction(0)
    if INTERP_FPS_AUTO and src_fps > 0:
        # 源 ≥60fps：至少 ×2（旧的 ×1 等于关闭补帧，会被用户当成"没生效"）
        if src_fps >= AUTO_FPS_MAX:
            m = 2
        else:
            m = max(2, int(AUTO_FPS_MAX / src_fps))
    elif INTERP_FPS_MULTI > 0 and src_fps > 0:
        # 固定倍数档（负数编码）—— 直接就是倍率，不看帧率
        m = Fraction(INTERP_FPS_MULTI).limit_denominator(1000)
    elif INTERP_FPS > 0 and src_fps > 0:
        # 绝对目标帧率（60/144）
        m = Fraction(INTERP_FPS).limit_denominator(1000) / src_fps
    else:
        m = Fraction(INTERP_MULTI).limit_denominator(1000)
    return int(m) if m.denominator == 1 else m


def stage_interp(clip):
    """RGBS 进 → 补帧（尺寸不变、帧率 ×MULTI）→ RGBS 出。返回 (clip, 是否成功)。

    按 VFI 分发到各引擎：
      rife（vsmlrt/TRT，默认最快）/ mv（mvtools，纯 CPU 零显存）/
      dup（帧复制）/ blend（帧混合）
    """
    m = interp_multi(clip)
    if not (m > 1):
        _why = (f"源 ×{m:g}" if (INTERP_FPS_MULTI or INTERP_FPS_AUTO)
                else f"目标 {INTERP_FPS:g}fps")
        warn(f"补帧倍率 {m} ≤ 1（{_why}，源 {clip.fps}fps）→ 跳过补帧")
        return clip, False
    if VFI == "mv":
        return _interp_mv(clip, m)
    if VFI in ("dup", "blend"):
        return _interp_simple(clip, m)
    if VFI not in ("rife", ""):
        warn(f"未知 LIVE_VFI={VFI!r}"
             f"（可选 rife / mv / dup / blend）→ 回退 rife")
    return _interp_rife(clip, m)


def _interp_simple(clip, m):
    """帧复制 / 帧混合：**零推理、零显存、零插件**（纯 std）。

    这是"补帧"的成本下限，也是"实在跑不动时"的兜底 ——
    实测 720p **0.31~0.45 ms/帧**（比 mvtools 还快 5 倍），
    而代价明明白白：
      dup   —— 每帧重复一次。帧率数字上去了，但运动是"一顿一顿"的
               （本质没增加信息，只是把台阶摆得更密）。
      blend —— 相邻帧线性混合（2x 的中点正好是 50/50）。比 dup 顺，
               但运动物体是**双层重影**（鬼影），静止区域反而更平滑。
    ★ 适用判断：如果你只是觉得"24fps 看着抖"而片子本身几乎不动
      （比如静态画面、慢摇镜头），blend 的两个假帧比 AI 补帧更自然，
      而且完全不吃 GPU/显存 —— 能和最高档超分同时开。
      真正有快速运动的镜头，这两条都会明显穿帮，还是得上 RIFE。
    ⚠ 只支持整数倍率：非整数（如 2.5x）在纯 std 下要逐帧算权重，
      成本会翻几倍、收益还不明确，所以这里直接拒绝并退回直通。
    """
    if not float(m).is_integer():
        warn(f"dup/blend 只支持整数倍率，当前 {m}x → 跳过补帧（请把目标帧率"
             f"设成源帧率的整数倍，如 24→48）")
        return clip, False
    k = int(m)
    if k < 2:
        warn(f"dup/blend 需要倍率 ≥2，当前 {m}x → 跳过")
        return clip, False
    fps = clip.fps * k
    other = "dup" if VFI == "dup" else "blend"
    # dup 的第二种形态（复制 k-1 次）会连播同一帧 k 次，观感比交错更"卡"；
    # 这里统一走交错（每源帧后跟 k-1 个插值帧），时序更均匀。
    try:
        if VFI == "dup":
            # 复制：每一步把整个序列自身交错一次，重复 log2(k) 轮
            #   （k=2 一轮、k=4 两轮）—— 每轮帧数翻倍，成本极低。
            out = clip
            done = 1
            while done < k:
                out = core.std.Interleave([out, out])
                done *= 2
            # k 不是 2 的幂时上面会超，用 SelectEvery 精确取前 k 倍
            if done != k:
                out = core.std.SelectEvery(out, done, list(range(k)))
        else:
            # 混合：k=2 时中点正好是 50/50。
            #   ⚠ 不能用 std.Merge —— 它只收 "8..16 bit integer or 32 bit float"，
            #     而超分阶段产出的是 RGBH（半精度，32 bit float 的 16 位版）：
            #       Error: Merge: Input clip must be constant format 8..16 bit integer
            #              or 32 bit float, passed RGBH.
            #     实测这正是 blend 之前"静默跳过补帧"的根因。
            #   → 用 std.Expr 做 (a+b)/2：它对所有常量格式都支持（含 RGBH），
            #     且是单趟表达式，比两次 Merge/转格式还快。
            if k == 2:
                cur = clip
                nxt = core.std.Trim(clip, 1, None) + core.std.Trim(clip, 0, 0)
                nxt = core.std.AssumeFPS(nxt, fpsnum=clip.fps.numerator,
                                         fpsden=clip.fps.denominator)
                nxt = core.std.SetFrameProp(nxt, "_Matrix",
                                            intval=clip.get_frame(0).props.get("_Matrix", 1))
                # 逐平面 (a+b)/2；整数格式下 Expr 会自动做四舍五入与钳位。
                mid = core.std.Expr([cur, nxt], expr=["x y + 2 /"])
                out = core.std.Interleave([cur, mid])
            else:
                warn(f"blend 目前只实现了 2x（当前 {k}x）→ 跳过补帧")
                return clip, False
        out = core.std.AssumeFPS(out, fpsnum=fps.numerator, fpsden=fps.denominator)
        # ★★ 恢复帧数（2026-09-30 修）：mpv 注入的源 clip 长度是**哨兵值 2^28**
        #   （表示"长度由 mpv 运行时决定"）。Interleave 会把这个数字**翻倍**：
        #     2^28 → 2^29 = 536870912，再叠加后续操作就溢出成 268435454 这种怪值
        #   → mpv 拿不到合法帧数，整条滤镜**静默失效**（日志里还写着"补帧成功"，
        #   这就是用户看到的"选了 dup/blend 不生效"的根因）。
        #   RIFE 那条路没事，因为它走 video_player=True，自始至终保持哨兵值不变。
        #   修法：把帧数**截回源的哨兵值**，让 mpv 按"动态长度"处理。
        #   ⚠ 用 std.Trim 而不是 std.SetVideoInfo —— 本机 VS 根本没有后者
        #     （`AttributeError: There is no function named SetVideoInfo`）。
        if clip.num_frames > SENTINEL_MIN and out.num_frames > clip.num_frames:
            out = core.std.Trim(out, 0, clip.num_frames - 1)
        log(f"补帧：{m}x  引擎={other}"
            f"（{'每帧复制' if other == 'dup' else '相邻帧线性混合'}，"
            f"零推理零显存）  "
            f"{'帧数动态' if out.num_frames > SENTINEL_MIN else str(out.num_frames) + ' 帧'}"
            f" @ {out.fps!s}")
        return out, True
    except Exception as e:                            # noqa: BLE001
        warn(f"{other} 补帧失败（{type(e).__name__}: {str(e)[:200]}）→ 跳过补帧")
        return clip, False


def _interp_mv(clip, m):
    """mvtools 传统光流补帧：纯 CPU，**不占显存**。

    代价与适用面（说清楚，别让人误以为是 AI 补帧的低配版）：
      · 画质：块匹配光流，运动模糊/大位移/遮挡处会有块状撕裂与重影，
        明显不如 RIFE。适合"只想要更顺、不在乎瑕疵"或**显存被超分吃满**时。
      · 成本：全在 CPU，跟 GPU 抢不到资源 —— 这正是它存在的理由：
        1080p「超分 + 补帧」同开时 GPU 算力/显存都不够，把补帧挪到 CPU
        就能两边都要（代价是 CPU 负载上去、画质降级）。
      · ★ 本机 i7-12700KF（12 物理核 / 20 逻辑核）实测，2026-09-30 重测
        （`_t_mv_cpu2.py` / `_t_mv_pareto.py` / `_t_mv_realtime.py`）：
        **整链** Super + 2×Analyse + BlockFPS，含输出倍率后的「每出帧」成本：
          640x360  ×2   0.38 ms   实测 2606 fps（播放只需 48）→ 54x 余量
          1280x720 ×2   1.57 ms   实测  636 fps（播放只需 48）→ 13x 余量
          1920x1080×2   3.94 ms   实测  253 fps（播放只需 48）→ 5.3x 余量
        → **它并不慢**（实时余量 5.3~13 倍）。CPU 占用其实不高（真播全链
          ~3.7 核，含解码+VS+VO），瓶颈是单帧延迟而非吞吐。
        预设对比（1280x720 ×2 整链，ms/出帧）：
          fast(pel=1 blk=32 ov=0)   1.59   ← 默认
          mid (pel=1 blk=16 ov=4)   5.91   ← 慢 3.7 倍
          best(pel=2 blk=16 ov=8)  11.73   ← 慢 7.4 倍
    ★ 必须输入 YUV（mvtools 吃 YUV，不吃 RGB float）—— 这里把 RGB 转回去。
    ★ 加速接口只有 `opt`（内部线程数），见文件顶部 MV_OPT 的实测表；
      `core.num_threads` 对它**无效**，别去调。
    """
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        yuv = core.resize.Bicubic(clip, format=vs.YUV420P8, matrix_s=MATRIX)
        # 参数从两个环境变量拼，方便现场调"快 / 好"
        _pel, _blk, _ov = MV_PARAMS
        # ★ opt（mvtools 内部线程数）：老构建不认 → 用 _MV_HAS_OPT 兜底不传。
        _ko = {"opt": MV_OPT} if _MV_HAS_OPT else {}
        # ★ 4K 自适应（2026-09-30 实测，`_t_4k_matrix.py`）：
        #   720p 时代的 blk=32 在 4K 产生 ~8100 块（720p 的 9 倍），4K 仅补帧
        #   只有 43.4fps（要 48，余量 0.90x = 掉帧的地基）。blk 翻倍 → 块数 ÷4：
        #     blk=64        17.11ms  58.5fps  余量 1.22x  ← 单项就翻盘
        #     blk=64+无色度  16.21ms  61.7fps  余量 1.29x  ← ✗ 已废弃，见下
        #   画质代价：64px 大块的向量场更平滑（动画上通常**更**干净，少噪声块），
        #   大位移物体边缘略糊。
        #   ⚠ 只对宽 ≥2560 的源生效 —— 720p 用 blk=64 只剩 ~225 块，并行度和
        #     向量精度都崩，别全局改。
        #   ★★ 2026-09-30 深夜撤销 `chroma=False`（用户报「各种绿色残影」）：
        #      mvtools 的 `chroma=False` **不是**「色度不参与块匹配」的意思，
        #      而是「**色度平面整个填 0**」。实测（`_t_4k_green.py`）：
        #        Super(chroma=True ) 输出 plane1 mean=75.05 plane2 mean=150.10
        #        Super(chroma=False) 输出 plane1 mean=0.00  plane2 mean=0.00
        #        BlockFPS 输出 U=0 V=0  ← YUV 里 U=V=0 **就是纯绿色**
        #      即整幅画面色度被清空 → 全屏偏绿 + 运动处绿色残影。
        #      ⚠ 而且它只值 4% 的性能（17.11→16.44ms，1.22x→1.27x 余量），
        #        blk=64 单独就已经达标 —— 拿画质换这点性能完全不值，故删。
        #      教训：**只测速度不取帧内容的基准会漏掉整类画质 bug**，
        #        现在 `_t_4k_matrix.py` 加了「输出色度不得全 0」的断言。
        _mv_hd = _MV_HD and yuv.width >= 2560
        if _mv_hd:
            _blk = min(_blk * 2, 64)
        log(f"补帧：{m}x  引擎=mvtools（纯 CPU 光流，不占显存）  "
            f"pel={_pel} blk={_blk} overlap={_ov} opt="
            f"{MV_OPT if _MV_HAS_OPT else '不支持'}"
            f"{' +4K自适应' if _mv_hd else ''}  "
            f"输入 {yuv.width}x{yuv.height} {yuv.format.name}")
        t0 = time.perf_counter()
        sup = core.mv.Super(yuv, pel=_pel, sharp=1,
                            rfilter=4 if _pel > 1 else 2, **_ko)
        bwd = core.mv.Analyse(sup, isb=True, delta=1, blksize=_blk,
                              overlap=_ov, **_ko)
        fwd = core.mv.Analyse(sup, isb=False, delta=1, blksize=_blk,
                              overlap=_ov, **_ko)
        # BlockFPS 直接给目标帧率：num/den 就是输出 fps
        _fps = clip.fps * m
        out = core.mv.BlockFPS(
            yuv, sup, bwd, fwd,
            num=int(round(_fps * 1000)), den=1000,
            mode=2,          # 2 = 用遮挡掩膜，比 mode=0 干净
            ml=50,           # 掩膜阈值
            blend=False,
        )
        # ★★ 恢复帧数（2026-09-30 修，与 _interp_simple 同款根因）：
        #   mpv 注入的源 clip 长度是**哨兵值 2^28 = 268435456**，表示
        #   "长度由播放器运行时决定"。BlockFPS 会按 num/den 重算帧数，
        #   在哨兵长度上算出 268435453 这种**截断后的怪值** → mpv 不认，
        #   整条滤镜被静默禁用（用户看到的就是"选了 mv 不生效"）。
        #   修法：截回源的哨兵值，让 mpv 继续按"动态长度"处理。
        #   ⚠ 必须在转回 RGB **之前**做，且用 std.Trim 而非 std.SetVideoInfo
        #     （本机 VS 没有后者）。
        if clip.num_frames > SENTINEL_MIN and out.num_frames > clip.num_frames:
            out = core.std.Trim(out, 0, clip.num_frames - 1)
        # ★ 关于"开头几帧发黑"（2026-09-30 查清，别再往这里加补丁）：
        #   实测 mv/blend 输出前 3~4 帧为黑，dup 一帧不黑。逐帧取证后根因是
        #   **mpv 自己的初始化桩帧**，不是本滤镜吐的：
        #     mpv 在建 VS 图期间会先要几帧，此时图未就绪 →
        #     `[vapoursynth] Frame requested during init! ... Returning black dummy frame`
        #   · dup 的图是 Interleave 一行、瞬间建完 → 0 次桩请求 → 0 黑帧；
        #   · blend 的图要 Expr+Trim+Interleave → 1 次 → 帧 0 黑；
        #   · mv 要 Super+2×Analyse+BlockFPS（最重）→ 2~3 次 → 帧 0~2 黑。
        #   （曾试过用 std.Splice 把帧 1 换成源帧：黑帧数**一个没少**，只是把
        #    纯黑 0 变成暗帧 15，纯属画蛇添足，已删。）
        #   → 正解在 mpv 侧（预热/容忍启动黑屏），本文件不做任何修补。
        log(f"  {time.perf_counter() - t0:.2f}s 完成图构建 → "
            f"{'帧数动态' if out.num_frames > SENTINEL_MIN else str(out.num_frames) + ' 帧'}"
            f" @ {out.fps!s}")
        # 转回 RGB float 交给后续（超分/输出）
        # ⚠ 方向反了：YUV → RGB 要 matrix_**in**_s（标"输入的矩阵"），
        #   写成 matrix_s 会报 `RGB color family cannot have YUV matrix coefficients`，
        #   整条滤镜被禁用 → 你只会看到"原始视频在放，0 丢帧"的假象。
        return core.resize.Bicubic(out, format=clip.format,
                                   matrix_in_s=MATRIX), True
    except Exception as e:                        # noqa: BLE001
        warn(f"mvtools 补帧失败（{type(e).__name__}: {str(e)[:200]}）→ 跳过补帧")
        return clip, False




def _interp_rife(clip, m):
    """vsmlrt 的 C++ TensorRT 版 RIFE（默认引擎，最快）。"""
    try:
        if str(VSMLRT_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(VSMLRT_SCRIPTS))
        import vsmlrt

        # ★ 把模型目录指到本脚本自己的文件夹，别去动 site-packages
        #   （vsmlrt.models_path 是普通模块变量，可以安全覆盖）
        vsmlrt.models_path = str(LIVE_MODELS)

        # 模型：固定用 rife/ 的 **11 通道**版（img0+img1+timestep + 4 个网格量）。
        #   ★ 2026-09-30：7 通道的 rife_v2/ 已删除（本机 TRT 11 解析不了它的
        #     混合精度 /Mul），impl=2 这条分支一并去掉，不再探测。
        sub = "rife"
        model_onnx = LIVE_MODELS / sub / f"rife_v{RIFE_VER}.onnx"
        enum_name = "v" + RIFE_VER.replace(".", "_")
        if not model_onnx.is_file():
            # ★ 只留 4.26 / 4.25_lite 两个模型（2026-09-30 清理）；其它版本
            #   已从磁盘删除，这里给一句能照着做的提示。
            warn(f"缺 RIFE 模型 {model_onnx} → 跳过补帧。"
                 f"（本机只保留 rife_v4.26.onnx / rife_v4.25_lite.onnx，"
                 f"要别的版本请把 rife_v{RIFE_VER}.onnx 放回 models/rife/）")
            return clip, False

        # fp16 输入：把帧转成 RGBH（每像素 12 字节 → 6 字节）
        if FP16_IO and clip.format.bits_per_sample != 16:
            clip = core.resize.Bicubic(clip, format=vs.RGBH)
            log("  输入转 RGBH（fp16）—— 每帧搬运量减半")

        # 相似帧跳过必须在**送进 RIFE 之前**打标（属性要能传到它读的左帧上）
        if STATIC_TH > 0:
            live_static.reset_stats()
            clip = live_static.mark_static(clip, STATIC_TH, core=core,
                                           report=_report_static)
            log(f"  相似帧跳过：阈值 {STATIC_TH}"
                f"（与上一帧的平均绝对差低于它就跳过推理，直接用源帧）")

        # 后端固定 TensorRT，不给选项：ncnn 跑不了 RIFE（它依赖 GridSample，
        # ncnn 会报 `GridSample not supported yet!`，图能建、fps 也对，推理是废的）。
        # 本机缺 trtexec.exe（pip 版 VS 的 wheel 只带 dll、全盘也搜不到），
        # 用 TensorRT Python API 顶替它 —— 详见 trtexec_py.py 顶部注释。
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import trtexec_py
        trtexec_py.install(vsmlrt)
        be = vsmlrt.Backend.TRT(
            fp16=True, device_id=DEVICE, num_streams=STREAMS,
            use_cuda_graph=CUDA_GRAPH, static_shape=True,
            workspace=4 << 30,
            output_format=1 if FP16_IO else 0,
        )

        _sf = clip.fps
        if INTERP_FPS_AUTO:
            _mode = "auto→自动倍数"
        elif INTERP_FPS_MULTI:
            _mode = f"源 ×{INTERP_FPS_MULTI:g}"
        else:
            _mode = f"目标 {INTERP_FPS:g}fps"
        log(f"补帧：{m}x（{_mode}）  后端=trt  impl=1  "
            f"{'fp16' if FP16_IO else 'fp32'} IO  "
            f"输入 {clip.width}x{clip.height} {clip.format.name}"
            f"（{_sf!s} → {_sf * m if _sf else '?'} fps）")
        t0 = time.perf_counter()

        # ★ tile 倍率（见 vsmlrt.RIFEMerge 里的 tilesize_requirement）：
        #   4.26 / 4.25_heavy 是 64，4.25_**lite** 是 128。
        #   写死 64 会在 lite 上报 "must be divisible by 128"。
        #   ⚠ 2026-09-30：日志文案原来把「64」写死了，用户看到
        #     `2560x1440 → 2560x1536（+96，凑 64 倍数）` 直接来问
        #     「为什么不是 +32」——因为 lite 实际要 128 倍数（1440→1536 = +96），
        #     而 64 倍数只要 +32（→1472）。**文案必须跟着 _MULT 走**。
        if RIFE_VER.startswith("4.25") and RIFE_VER.endswith("_lite"):
            _MULT = 128
        elif RIFE_VER.startswith(("4.25", "4.26")):
            _MULT = 64
        else:
            _MULT = 32
        pad_w = (-clip.width) % _MULT
        pad_h = (-clip.height) % _MULT
        feed = clip
        if pad_w or pad_h:
            feed = core.std.AddBorders(clip, right=pad_w, bottom=pad_h)
            log(f"  补帧前 pad {clip.width}x{clip.height} → "
                f"{feed.width}x{feed.height}（+{pad_w}/+{pad_h}，"
                f"凑 {_MULT} 倍数：{RIFE_VER} 的 tile 要求）")

        out = vsmlrt.RIFE(
            feed, multi=m,
            model=vsmlrt.RIFEModel[enum_name],
            backend=be,
            video_player=True,          # 播放器模式（非整数倍率时才生效，传着无害）
        )

        if pad_w or pad_h:
            out = core.std.Crop(out, right=pad_w, bottom=pad_h)
            log(f"  补帧后裁回 {out.width}x{out.height}")
        log(f"  {time.perf_counter() - t0:.2f}s 完成图构建 → "
            # video_player 模式下 RIFE 的帧数是"由源实时决定"的，
            # num_frames 会被设成一个哨兵大数（约 2^28），直接打印会很难看
            f"{'帧数动态' if out.num_frames > 1 << 20 else str(out.num_frames) + ' 帧'}"
            f" @ {out.fps!s}")
        return out, True
    except Exception as e:                        # noqa: BLE001
        warn(f"补帧失败（{type(e).__name__}: {str(e)[:200]}）→ 跳过补帧")
        return clip, False


# ---------------------------------------------------------------- 1) 先统一到 RGB float
# 超分和 RIFE 都要求 RGB float 输入。
# ★ 精度选谁很关键（1080p 实测单价）：YUV420P8 → RGBS **3.65 ms**，
#   RGBS → RGBH（纯 dtype 转换）**2.07 ms**——所以要 fp16 就**一步到位直转 RGBH**，
#   别先转 RGBS 再转 RGBH（那等于白跑一遍 3.65 ms）。
BASE_FMT = vs.RGBH if FP16_IO else vs.RGBS
if src.format.color_family == vs.RGB:
    rgb = core.resize.Bicubic(src, format=BASE_FMT)
else:
    rgb = core.resize.Bicubic(src, format=BASE_FMT, matrix_in_s=MATRIX)


# ---------------------------------------------------------------- 2) 顺序调度
# hr = 先超分后补帧；lr = 先补帧后超分（高分辨率源明显更快，理由见文件头第 4 条）。
# ⚠ `_CPU_VFI_FORCED_HR` **必须在这里先初始化** —— 它只在下面 `ORDER == "auto"`
#   分支里被赋真值，但日志段（第 922 行附近）**无条件**读它。用户手选 HR/LR
#   （LIVE_ORDER=hr）时不进 auto 分支 → 未定义 → `NameError` → **整个滤镜被禁用**、
#   mpv 退化成播放未处理的源码流（VO 分辨率 = 源分辨率，看起来就是"超分没生效"）。
#   ★ 2026-09-30 实际踩到（用户报"超分失效，输出还是源视频的 720p"）。
#   教训：任何"只在某分支里赋值、却在分支外被读"的变量都必须先给默认值。
_CPU_VFI_FORCED_HR = False
if ORDER == "auto":
    # 走 LR 的两个理由（都实测过）：
    #   ① 源宽 ≥1280：RIFE 在大图上很贵，先在源尺寸补帧再超分划算；
    #   ② **目标帧率 ≥60fps：只有 LR + re2x 跑得满** —— 实测 480p 9 秒：
    #        HR + av3  丢 313 帧（超分 4x 的中间步骤要对每个输出帧做）
    #        LR + re2x 丢 0 帧
    #      所以高帧率时不管源多小都得走 LR。
    #   ★★ 2026-09-30 修：原来判的是 `INTERP_FPS >= 60`，那是「绝对 fps」时代的
    #     写法。新档位里倍率用负数编码（-2/-3/-4），全 < 60 → 480p 源选 ×4 会
    #     被误判成"低帧率"走 HR，丢掉 LR 的性能优势。改成用**实际目标帧率**判。
    _tgt_fps = 0.0
    if INTERP_ON and src_w and src_h:
        try:
            _tgt_fps = float(src.fps) * float(interp_multi(src))
        except Exception:                              # noqa: BLE001
            _tgt_fps = 0.0
    _need_lr = src_w >= 1280 or (INTERP_ON and _tgt_fps >= 60)
    ORDER = "lr" if (UPSCALE_ON and INTERP_ON and _need_lr) else "hr"
    # ★★ CPU 侧引擎（mv / dup / blend）例外：**必须走 HR**。2026-09-30 实测修正。
    #   为什么 LR 会崩：LR 是「先补帧 → 再超分」，输出帧数翻倍 → **GPU 超分
    #   要做两倍的次数**（每输出帧一次）。补帧挪到 CPU 只是把补帧那一环从 GPU
    #   卸掉了，但超分的负载被 LR 顺序放大了 —— 而超分才是 GPU 上的大项。
    #   实测（1080p 源、AJ Balanced、SR_MAX=960、out×1、@48、60 秒窗）：
    #     LR（先补帧后超分）→ 丢 15 / 404 帧（临界，看运气）
    #     HR（先超分后补帧）→ 丢 0 / 0 / 0 帧（稳）
    #   HR 下超分只处理**源帧数**（24 帧），补帧在 CPU 上把 24 帧摊成 48 帧 ——
    #   两边的活各归各，20 核 CPU 跑 mvtools 只要 2.1 ms/帧，余量巨大。
    #   dup/blend 更极端（0.3ms/帧），但顺序错了照样被超分拖死，所以一视同仁。
    if VFI in ("mv", "dup", "blend") and UPSCALE_ON and INTERP_ON and ORDER == "lr":
        ORDER = "hr"
        _CPU_VFI_FORCED_HR = True
    else:
        _CPU_VFI_FORCED_HR = False
    # ★★ 4x 模型 + 补帧 = **跑不动**（2026-09-30 实测，与顺序无关）：
    # ★ LR 下超分输入是**补过帧的**（帧数翻倍），所以引擎最好是原生 2x：
    #   4x 模型（animevideov3）在 LR 下那个 5120x2880 的中间步骤要做**两倍次数**，
    #   实测反而比 HR 更慢（37.91 vs 29.91 ms/输出帧）。
    #   ⚠ 「4x + 补帧 = 跑不动」的**无条件降级**见下面 `_SR_FORCE` 那段
    #     （放在 auto 分支外面 —— 显式 LIVE_ORDER 时也要生效）。
    # ⚠ 这里**不再处理**「4x 模型」的降级 —— 统一交给下面 auto 分支之外的
    #   `_SR_FORCE` 块。原先这里有两条 LR 专属的 if/elif，会和那个块**重复触发**：
    #   先打印"是你显式指定的，就按你说的来"，紧接着又被无条件块降级 ——
    #   两条自相矛盾的警告（2026-09-30 实测日志里抓到的）。

# ★★ 2026-09-30：4x 模型 + 补帧 = **跑不动**（实测，与顺序无关）：
#     720p 源 + animev3(4x) + 补帧 48：  LR 丢 159~392 帧 / HR 丢 107 帧
#     同条件换 2x（re2x）：              LR / HR **都丢 0**
#   这是"能不能看"（会不会卡顿/音画不同步）的问题，不是画质偏好，
#   所以**不再看 `_SR_USER_FORCED`** —— 那个判据让 GUI 场景永久失效
#   （GUI 恒传 LIVE_SR → 永远被视为"用户强制"，自动优化一次都没跑过）。
#   ⚠ 这段**必须放在 `if ORDER == "auto":` 之外**：第一版写在了里面，
#     结果显式 `LIVE_ORDER=hr/lr` 时整段被跳过、修复"没生效"（测试抓到的）。
#   ⚠ 留逃生口 `LIVE_SR_FORCE=1`（真要 4x 就设它，后果自负）；
#   ⚠ 只对**大源**（宽 ≥1280）强制 —— 小源推理档位小，4x 未必跑不动。
_SR_FORCE = _flag("LIVE_SR_FORCE", False)
# 触发条件（2026-09-30 深夜**放宽**）：不再要求补帧也开着 ——
#   **只要超分开着、模型是 4x、源宽 ≥1280 就降级。**
#   实测（1920x1080 CFR 源 + 真窗口 + 真音频时钟，`_t_single_modes.py`）：
#     完全不加滤镜        → 丢 0 帧，avsync 0            ✓
#     单独超分 animev3(4x) → 输出 **7680x4320** → 稳定期丢 98 帧、avsync 涨到 +7.9s ❌
#     单独超分 re2x(2x)    → 输出 3840x2160     → 丢 0 帧  ✓
#     单独补帧 rife×2      → 输出 1920x1080@48  → 丢 0 帧  ✓
#   机理：4x 推理**本身**就要吐 7680x4320 的帧，光这一步就供不上 23.976fps，
#        跟补帧开不开无关（补帧只是再翻一倍）。而 1080p 源 ×4 = 7680x4320
#        是 4K 的两倍、超出任何显示器 —— 纯白烧，降级没有画质损失。
#   ⚠ 小源（宽 <1280）保留 4x：例如 960x540 ×4 = 3840x2160 正好 4K，是有意义的用法。
#   ⚠ 补帧 + LR 仍要降级（4x 要把中间步骤做两倍次数）。
if (UPSCALE_ON and SR_NATIVE_SCALE == 4
        and (src_w >= 1280 or (INTERP_ON and ORDER == "lr"))):
    if _SR_FORCE:
        warn(f"LIVE_SR_FORCE=1：保留 4x 模型（{SR_MODEL}）—— "
             f"实测会丢帧（1080p 输出 7680x4320 丢 98 帧/14s、avsync 涨到 +7.9s；"
             f"720p 配补帧丢 100~400 帧），卡顿/音画不同步别怪链路。")
    else:
        warn(f"4x 模型（{SR_MODEL}）在 {src_w}x{src_h} 源上跑不动："
             f"单独超分实测（1080p）输出 7680x4320、丢 98 帧/14s、avsync 涨到 "
             f"+7.9s；配补帧更要丢 100~400 帧。换 2x 则丢 0 帧。"
             f"→ 自动改用 2x 的 realesr-animevideov3_re2x。"
             f"想要 4x 请设 LIVE_SR_FORCE=1 强制保留（小源不受影响）。")
        SR_MODEL = "realesr-animevideov3_re2x"
        SR_CANDIDATES = _sr_ladder_dirs()
        LADDER = _scan_ladder()
        SR_NATIVE_SCALE = 2

# ★★★ 2026-09-30 深夜：**超分+补帧的自动让路**（全部数据来自用户的真片源实测）
#   受测素材：用户自己的 1920x1080 H.264 23.976 + 音轨，走他的 ui_config.json、
#   真 GUI、窗口最大化（osd 2560x1440@143.999Hz），**拖进度条**（20 条异步 seek）后测：
#     LR + 不限  → 视频只跑到 0.73 倍实时，落后 +2.6~3.0 秒（丢 300+ 帧）❌
#     LR + 限1080 → 仍丢 85 帧、+0.53 秒                              ❌
#     HR + 限1080 → **0 丢帧 / -0.04 秒**                              ✓
#     HR + 限1440 → **0 丢帧 / -0.04 秒**                              ✓
#   机理：真正贵的是**补帧要处理的尺寸** ——
#     LR：补帧在源尺寸（便宜），但超分要对**每个输出帧**做一遍（帧数翻倍 → 双倍）
#     HR：超分只做源帧数，但补帧要在超分后的大图上做（2160p 的 RIFE 极贵）
#   把「限定播放分辨率」压到源尺寸后，两条都回到各自单独跑得动的量级。
#   判据仍是像素吞吐（ow*oh*fps_out > 「4K@24」×1.3）；**只对宽 ≥1280 的源**生效
#   ⚠ 闸门用 **短边 ≥1000**（"1080p 级以上的源"）而不是"宽 ≥1280" ——
#     后者对 720p（正好宽 1280）也成立，而 720p 同开实测毫无压力，会被误伤。
#   ⚠ `LIVE_THROUGHPUT_FORCE=1` 可保留用户原样（后果自负）。
_THROUGHPUT_FORCE = _flag("LIVE_THROUGHPUT_FORCE", False)
if UPSCALE_ON and INTERP_ON and min(src_w, src_h) >= 1000:
    try:
        _ow, _oh = _native_out_size()
        _mfps = src.fps * interp_multi(src) if src.fps else 0
        _budget = 3840 * 2160 * 24.0          # 一个 4K@24 流 = 实测能供上的量
        _load = _ow * _oh * float(_mfps or 0)
        if _mfps and _load > _budget * 1.3:
            _cap0 = min(src_w, src_h)
            if _THROUGHPUT_FORCE:
                warn(f"超分+补帧同开：输出 {_ow}x{_oh} @ {_mfps:g}fps，像素吞吐是"
                     f"「4K@24」的 {_load / _budget:.1f} 倍。LIVE_THROUGHPUT_FORCE=1："
                     f"保留原样 —— 实测会丢 300+ 帧、拖进度条后音画错开 2.6~3.0 秒。")
            elif PLAY_MAX_H <= 0:
                warn(f"超分+补帧同开：输出 {_ow}x{_oh} @ {_mfps:g}fps，像素吞吐是"
                     f"「4K@24」的 {_load / _budget:.1f} 倍 —— 实测供不上"
                     f"（视频只跑到 0.73 倍实时，拖一次进度条就落后 2.6~3.0 秒）。"
                     f"→ 自动改成 **HR 顺序 + 限定播放分辨率 {_cap0}**"
                     f"（源尺寸）：同样配置实测 **0 丢帧 / 音画差 -0.04 秒**。"
                     f"想要更大的输出，请把「限定播放分辨率」自己设成 1440 以上"
                     f"（实测 1440 也扛得住），或超分/补帧只留一个；"
                     f"LIVE_THROUGHPUT_FORCE=1 可强制保留原样。")
                ORDER = "hr"
                PLAY_MAX_H = _cap0
                PLAY_MAX_EDGE = "short"
            else:
                warn(f"超分+补帧同开：输出 {_ow}x{_oh} @ {_mfps:g}fps，像素吞吐是"
                     f"「4K@24」的 {_load / _budget:.1f} 倍 —— 实测供不上。"
                     f"你已显式设了「限定播放分辨率 {PLAY_MAX_H}」，不替你改；"
                     f"若仍卡/错位，把它降到 {_cap0} 试试（实测 0 丢帧）。")
    except Exception as _le:                                # noqa: BLE001
        log(f"  （像素吞吐预算检查跳过：{type(_le).__name__}: {str(_le)[:60]}）")

log(f"顺序 {ORDER.upper()}（{'先补帧后超分' if ORDER == 'lr' else '先超分后补帧'}）"
    f"  超分模型 {SR_MODEL}")

if _CPU_VFI_FORCED_HR:
    log(f"  ⚠ {VFI} 引擎自动改成 HR 顺序：LR 下超分要做两倍次数（输出帧翻倍），"
        f"1080p 实测丢 15~404 帧；HR 下超分只做源帧数 → 丢 0 帧")

stage = rgb
ok_up = ok_int = False
ok_cap = False
if ORDER == "lr":
    # LR = 先补帧再超分。★ 补帧是在**源尺寸**上做的，本来就便宜，
    #   所以「限定播放分辨率」在 LR 下没有可省的一步 —— 只把超分结果缩到上限
    #   （缩在超分之后，纯粹是让最终输出/显示小一点）。
    if INTERP_ON:
        stage, ok_int = stage_interp(stage)        # 在**源尺寸**上补帧（便宜）
    if UPSCALE_ON:
        stage, ok_up = stage_upscale(stage)        # 再超分（要对每个输出帧做）
    # ★★ 2026-09-30 修：`fit_play_cap` 必须**独立于超分**。原来它被关在
    #   `if UPSCALE_ON:` 里 —— 见下面 HR 分支的长注释（同一个 bug）。
    stage, ok_cap = fit_play_cap(stage)
else:
    # HR = 先超分再补帧。★ 这才是「限定播放分辨率」真正省成本的地方：
    #   超分出来的大图**先缩到上限**，补帧就在这个尺寸上做 ——
    #   4x 模型（5120x2880）配补帧本来几乎必炸，限住之后才有机会跑得动。
    #   ★★ 这条对「**仅补帧**」尤其重要：仅补帧时 ORDER 必然是 HR
    #      （`ORDER = "lr" if (UPSCALE_ON and INTERP_ON and ...) else "hr"`），
    #      而补帧的成本正比于尺寸 —— 4K 补帧 43.4fps（不够 48）vs
    #      1080p 补帧 215fps，缩到 1080p 就等于白拿 5 倍余量。
    if UPSCALE_ON:
        stage, ok_up = stage_upscale(stage)
    # ★★ 2026-09-30 修 bug（用户报「限定播放分辨率在仅补帧下不生效」）：
    #   原来这行写在 `if UPSCALE_ON:` **内部**，于是 UPSCALE_ON=False（仅补帧）
    #   时它被整块跳过 → 用户设了上限，日志却仍然 `补帧：输入 3840x2160`，
    #   4K 补帧白设上限。缩放和超分是两个独立诉求，必须分开。
    stage, ok_cap = fit_play_cap(stage)
    if INTERP_ON:
        stage, ok_int = stage_interp(stage)

out = stage
UPSCALE_ON, INTERP_ON = ok_up, ok_int              # 反映真实生效状态，给下面的日志用
if PLAY_MAX_H > 0 and not ok_cap:
    _edge_cn = "长边" if PLAY_MAX_EDGE == "long" else "短边"
    log(f"限定播放分辨率 {PLAY_MAX_H}（按{_edge_cn}）：没超出，未缩（只当上限用）")


# ---------------------------------------------------------------- 3) 转回 mpv 能显示的格式
# ⚠ 这次转换是 **CPU 端 zimg**，而且要对**每个输出帧**做一次 —— 1080p@60 就是
#   每秒 60 次「读 25MB + 写 6MB」。
#
# ⚠⚠⚠ 【2026-09-30 重大更正】曾经的 `LIVE_RAW_OUT=1`（输出 RGB 交 GPU 转换）
#     **已确认是错的，已删除。** 当时以为它能省掉上面这次 CPU 转换、
#     并且实测到「丢帧 463 → 0」，但那个 0 是**假象**：
#
#     真因：mpv 的 vapoursynth 滤镜**只接受 YUV 输出**。
#       `vf_vapoursynth.c` 的 `mp_from_vs()` 里第一句就是
#       `if (vsfmt->colorFamily == cfYUV) {...} return 0;`
#       —— 非 YUV（RGBS/RGBH/RGB24/RGB48/GRAYS 全算）一律返回 0，
#       `reinit_vs()` 随即报 `Unsupported output format.`，
#       **整个 vapoursynth 滤镜被禁用**（`Disabling filter vapoursynth.00`）。
#
#     本机实测（逐格式验证）：
#       RGBS/RGBH/RGB24/RGB48/GRAYS → `Unsupported output format.`
#       YUV420P8 / P16 / 444P16 / 420PS → OK
#
#     所以 RAW_OUT=1 时 mpv 是在**播放未处理的原始视频**：
#       RAW_OUT=0 → VO: 2560x1440, vf-fps 47.5, 丢 81   （超分+补帧真的在跑）
#       RAW_OUT=1 → VO: 1280x720,  vf-fps 24.0, 丢 0    （滤镜全挂，放的是源码流）
#     「0 丢帧」= 什么都没算，不是优化成功。基于它推出的一切结论（包括
#     送外部 AI 那份文档的 P0 与数据流表）**全部作废**。
#
#     附带更正：`LIVE_RAW_TAG` / GUI 里那个「RAW 色彩标注」下拉是基于同一个
#     错误前提加的，一并删除。（它本身机制没错 —— mpv 确实不读 VS 帧属性 ——
#     但既然 RGB 输出根本用不了，这个标注也就没了用武之地。）
#
#     → 结论：**输出必须是 YUV**。下面这条分支不再提供 RGB 出口。
#
# 输出格式也有讲究（1080p 实测单价，扣掉上游）：
#   RGBS→YUV420P16 4.17ms / RGBH→YUV420P16 4.20ms
#   RGBS→YUV420P8  2.72ms / RGBH→YUV420P8  3.10ms
# 这次转换对**每个输出帧**都做一次，所以它值钱：1080p@60 实测 P8 比 P16 快约 14%。
# auto = 8bit 源就用 P8（源本来就是 8bit，再高的位深没有信息量），
#        否则用 P16 避免二次量化。
_FMT = _env("LIVE_OUT_FMT", "auto").lower()
if _FMT in ("auto", ""):
    _FMT = "yuv420p8" if src.format.bits_per_sample <= 8 else "yuv420p16"
# ★ 非法格式名要**安全退回**，不能 `getattr(vs, ...)` 硬取 ——
#   取不到就是 AttributeError，照样炸掉整条滤镜（2026-09-30 实测复现）。
#   而且只允许 YUV：RGB 格式名在这里"合法"却会让 mpv 禁用滤镜（见上面长注释）。
_ALLOWED_FMT = ("yuv420p8", "yuv420p16", "yuv444p16", "yuv420ps")
if _FMT not in _ALLOWED_FMT:
    _bad_fmt = _FMT
    _FMT = "yuv420p8" if src.format.bits_per_sample <= 8 else "yuv420p16"
    warn(f"输出格式 {_bad_fmt!r} 不认识（可选 {' / '.join(_ALLOWED_FMT)}）→ 退回 {_FMT}")
shown = core.resize.Bicubic(out, format=getattr(vs, _FMT.upper()), matrix_s=MATRIX)
log(f"输出 {_FMT}（⚠ mpv 的 vapoursynth 滤镜只收 YUV，RGB 输出会让整条滤镜被禁用）")

log(f"链路就绪：超分={'开' if UPSCALE_ON else '关'} "
    f"补帧={'开' if INTERP_ON else '关'} 顺序={ORDER.upper()} → "
    f"{shown.width}x{shown.height} @ {shown.fps!s}")

# ⚠ 输出必须用 **VideoNode.set_output()** 注册 —— 这是 VS API4 的机制。
#   实测确认（R77 + mpv 0.41）：只写 `output = shown`（那套老约定）时，
#   mpv 的 getOutputNode(script, 0) 会返回 NULL，报
#   "Could not get script output node." 然后直接把滤镜禁用掉。
#   `output = ...` 仍然留着 —— vspipe/--info 那条路认的是它。
#
# ⚠ 想在这里插「每帧计时探针」是**行不通的**（2026-09-30 试过并删掉）：
#   包一层 `std.ModifyFrame` 让 python 回调计时 —— mpv 走的是 TRT 的 GPU 路径，
#   根本不触发 CPU 回调，一行日志都不会打。要量单帧耗时请用独立的
#   `_t_noise_cost.py`（VS 侧直接 get_frame 循环 + 逐帧噪声输入防缓存）。
shown.set_output(0)
output = shown
