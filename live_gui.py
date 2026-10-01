# -*- coding: utf-8 -*-
r"""mpv 实时超分 + 补帧 —— 播放控制台（PySide6）

★ 2026-09-30：改成本目录**.venv 自包含**
    以前分两个解释器（GUI 用系统 Python310，VS 走 ComfyUI 的 Python 3.13）。
    现在统一到本目录的 `.venv`（自带 Python 3.13 + PySide6 + VapourSynth），
    整个文件夹拷到别的 Windows 上，双击就能用 —— 不需要目标机预装任何东西。
    本进程 **绝不 import vapoursynth** —— 它只做三件事：
        ① 组装环境变量  ② 启动 mpv.exe  ③ 收日志、读播放状态
    所以 GUI 能不能 import vapoursynth 从来都不重要。

★ 解释器/依赖的选取顺序：**.venv 优先，本机兜底**
    ① 本目录 `.venv\python.exe` 存在 → 用它（版本锁定，首选）
    ② 不存在 → 回退到系统里能找到的 Python（有 PySide6 的那个）
    ③ 都没有 → 明确报错，告诉用户怎么装，而不是静默崩溃

为什么不用 live_play.bat：
    bat 里是 `set "LIVE_SR=animev3"` 这种**无条件赋值**，
    外部先 set 也会被它盖掉。GUI 直接自己启动 mpv，参数实时可调，
    就不用"复制一份 bat 改一行"了。

用法：双击同目录的「实时播放器.lnk」（零闪烁）或「启动控制台.bat」。
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea,
    QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget,
)

# ═══════════════════════════════════════════════════════════════ 路径与常量

if getattr(sys, "frozen", False):
    # 打包成 exe 后，资源（mpv/、live.vpy、models/、mpv_cn/ …）放在 exe 同级目录
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent
MPV = ROOT / "mpv" / "mpv.exe"
SCRIPT = ROOT / "live.vpy"
# 中文界面资源目录 —— 当作 mpv 的配置目录用（--config-dir 指过去），里面有：
#   input.conf            中文键位 / 中文 OSD 提示
#   menu.conf             中文右键菜单（select.lua 默认读 ~~/menu.conf）
#   scripts/stats_zh.lua  中文统计页（独立脚本名，避开内置英文 stats.lua）
#   scripts/context_menu.lua  官方菜单脚本（本构建没自动加载它，见日志会报
#                         "Can't find script 'context_menu'"，所以自己补一份）
CN_DIR = ROOT / "mpv_cn"
ENGINE_DIR = ROOT / "models" / "sr"
RIFE_DIR = ROOT / "models" / "rife"
SELFCHECK = ROOT / "selfcheck.py"
CFG_PATH = ROOT / "ui_config.json"

# VapourSynth / Python 侧 —— **本目录 .venv 优先，本机兜底**
#   ★ 2026-09-30 从「硬编码 D:\ComfyUI-aki-v3.2\python」改成目录内 venv，
#     这样整个文件夹拷到别的电脑就能直接跑。
def _pick_vs_root() -> Path:
    """找一个可用的 VapourSynth 解释器根目录（含 Lib\\site-packages\\vapoursynth）。

    顺序：① 本目录 .venv（自包含，首选）→ ② 本机常见安装位置（兜底）。
    """
    cands = [ROOT / ".venv"]                     # ① 自包含（唯一可靠的那个）
    # ② 兜底：本机已装的（有 vsscript.dll 才算数）
    cands += [
        Path(r"D:\ComfyUI-aki-v3.2\python"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python313",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312",
        Path(r"C:\Python313"),
    ]
    for c in cands:
        if (c / "Lib" / "site-packages" / "vapoursynth" / "vsscript.dll").is_file():
            return c
    return cands[0]                              # 都没有 → 返回 .venv（后续会明确报错）


VSPY = _pick_vs_root()
VSSP = VSPY / "Lib" / "site-packages"
VSROOT = VSSP / "vapoursynth"
VS_PY = VSPY / "python.exe"

FFMPEG = VSSP / "imageio_ffmpeg" / "binaries" / "ffmpeg-win-x86_64-v7.1.exe"

VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".ts", ".m2ts",
             ".webm", ".rmvb", ".rm", ".mpg", ".mpeg", ".m4v", ".vob", ".ogv",
             ".3gp", ".asf", ".f4v", ".mts"}

# 引擎档位表 —— 必须与 live.vpy 里的 LADDER_* 逐条对应（不是拍脑袋写的）
LADDER_ANIMEV3 = [(640, 360), (640, 480), (720, 480), (854, 480),
                  (960, 720), (1280, 720), (1920, 1080)]
LADDER_RE2X = [(640, 360), (854, 480), (960, 720), (1280, 720), (1920, 1080)]

# ═══════════════════════════════════════════════════ 超分模型清单（2026-09-30 加）
# ★ 每个模型 = (live.vpy 用的 LIVE_SR 值, 界面显示名, 原生倍率, 引擎目录, 文件名前缀)
#   引擎目录/前缀用来**扫盘确认真的编过**（没编过就不该出现在下拉里，
#   否则用户选完才发现跳过超分）。
#   ⚠ 第一个模型的 LIVE_SR 值用 `animev3`（不是 `realesr-animevideov3`）：
#     BENCH / 频率判据表 / 旧 ui_config.json 全用这个短名，改了要一起动 4 处。
#
# ★★ Anime4KCPP 系列（2026-09-30 加）：**不是 TRT 引擎，是 VS 原生插件**。
#   → 引擎目录写 None（表示"不用扫盘，没有 .engine 文件"），
#     档位也不适用（factor 直接按需求算）→ 倍率字段单独解释。
#   ★ 实测同输出尺寸比 TRT 快 5.8~9.3 倍（YUV 直通，零 float 往返），
#     代价是**没有原生 4x**（全是 2x 网络）→ 倍率写 2，界面会标注。
AJ_ENGINE_DIR = ROOT / "models" / "sr_aj"
# A4K 插件可用性（决定下拉里要不要出现这一组）：DLL 在就算可用。
A4K_DLL = ROOT / ".venv" / "Lib" / "site-packages" / "vapoursynth" / "plugins" / "anime4kcpp.dll"
_A4K_ON = A4K_DLL.is_file()
SR_MODELS: list[tuple[str, str, int, Path | None, str]] = [
    ("animev3", "animevideov3　4x 原生·画质优先",
     4, ENGINE_DIR, "realesr-animevideov3_"),
    # ★ 2026-09-30 用户要求：显示名从「re2x」改成「animevideov3_re2x」，
    #   说明改成「（重训为原生导出 2X）」—— 名字里带全称才认得出它的出身。
    ("realesr-animevideov3_re2x", "animevideov3_re2x　（重训为原生导出 2X）",
     2, ENGINE_DIR, "realesr-animevideov3_re2x_"),
    ("AnimeJaNai_HD_V3.1_Performance",
     "AnimeJaNai Performance　最快",
     2, AJ_ENGINE_DIR, "AnimeJaNai_HD_V3.1_Performance_"),
    ("AnimeJaNai_HD_V3.1Sharp1_Performance",
     "AnimeJaNai Sharp1 Perf　快+加锐",
     2, AJ_ENGINE_DIR, "AnimeJaNai_HD_V3.1Sharp1_Performance_"),
    ("AnimeJaNai_HD_V3.1_Balanced",
     "AnimeJaNai Balanced　质量档·推荐",
     2, AJ_ENGINE_DIR, "AnimeJaNai_HD_V3.1_Balanced_"),
    ("AnimeJaNai_HD_V3.1Sharp1_Balanced",
     "AnimeJaNai Sharp1 Bal　质量+加锐",
     2, AJ_ENGINE_DIR, "AnimeJaNai_HD_V3.1Sharp1_Balanced_"),
]

# ★★ Anime4KCPP 全模型清单（用户要求「不设默认，全列出」）。
#   ⚠⚠ 这组**不属于 TRT 模型**：它是 VS 原生插件（YUV 直通，零 float 往返），
#     live.vpy 里走完全独立的分支。用户明确要求「只留快的方法，
#     不要出现在 TRT 的模型选择中」—— 所以它**不在 SR_MODELS 里**，
#     而是放在独立的 A4K_MODELS，界面上是**单独的「引擎」下拉**。
#   值 = `anime4kcpp:<模型名>`（传给 LIVE_SR）。
#   第三项 = 720p→1440p、CUDA 的隔离实测 ms/f，**只用于预估，不进显示名**
#   （显示名带上 ms 会过长，下拉里会被省略号截断 —— 2026-09-30 用户反馈）。
#   按速度排序：f8b4/fsrcnnx-f8 2.83 < hdn* 3.10 < gan/f8b8 3.94
#              < f8b18 4.37 < artcnn 5.52 < arnet 7.20
#   hdn0~hdn3 = 降噪强度递增（hdn3 最强，适合噪点多的老片）；
#   gan = 官方默认（细节增强，最锐）；f8b* = VGG 风格；arnet = ResNet 风格；
#   artcnn = Artoriuz 的轻量 CNN；fsrcnnx = 经典 FSRCNNX。
A4K_MODELS: list[tuple[str, str, float]] = [
    ("acnet-f8b4", "ACNet-F8B4　VGG 最省", 2.83),
    ("fsrcnnx-f8", "FSRCNNX-F8　经典最省", 2.83),
    ("acnet-hdn0", "ACNet-HDN0　零降噪", 3.10),
    ("acnet-hdn1", "ACNet-HDN1　轻降噪", 3.10),
    ("acnet-gan", "ACNet-GAN　默认最锐", 3.94),
    ("acnet-hdn2", "ACNet-HDN2　中降噪", 3.10),
    ("acnet-hdn3", "ACNet-HDN3　强降噪", 3.10),
    ("acnet-f8b8", "ACNet-F8B8　VGG 中", 3.94),
    ("acnet-f8b18", "ACNet-F8B18　VGG 重", 4.37),
    ("artcnn-c4f16", "ArtCNN-C4F16　轻量 CNN", 5.52),
    ("arnet-f8b8", "ARNet-F8B8　ResNet 风格", 7.20),
    ("acnet-legacy-gan", "ACNet-Legacy-GAN　旧版权重", 3.94),
]


def sr_ladder(model_key: str) -> list[tuple[int, int]]:
    """扫盘得到某模型**实际可用**的档位（按 live.vpy 的同名规则解析文件名）。

    ★ 前缀匹配有个坑：`realesr-animevideov3_` 是 `realesr-animevideov3_re2x_` 的
      前缀 —— 不做额外约束的话，animev3 会把 re2x 的引擎也算成自己的档位。
      所以前缀后缀用 `fp16_` / `fp16io_` 锚定，且**排除**其它模型的前缀。
    ★ Anime4KCPP 路线**没有引擎也没有档位**（factor 直接按需求算）→ 返回空表。
    """
    import re as _re
    row = next((r for r in SR_MODELS if r[0] == model_key), None)
    if row is None:
        return LADDER_ANIMEV3
    _, _, _, d, prefix = row
    if d is None or not d.is_dir():                  # Anime4KCPP（d=None）→ 无档位
        return []
    # 其它模型的前缀（同一目录下），本模型的匹配要排除它们。
    # ★ 必须比**本模型自己的前缀更长**才算「别人的」：`realesr-animevideov3_`
    #   是 `realesr-animevideov3_re2x_` 的真前缀，如果无脑排除「所有别人的前缀」，
    #   re2x 会被 animev3 反过来吃掉（两个模型档位都算错）。
    others = [p for k, _, _, dd, p in SR_MODELS
              if dd == d and p and len(p) > len(prefix)
              and not prefix.startswith(p)]
    pat = _re.compile("^" + _re.escape(prefix) + r"fp16(?:io)?_(\d+)x(\d+)\.engine$")
    out = []
    for f in sorted(d.glob("*.engine")):
        if any(f.name.startswith(o) for o in others):
            continue
        m = pat.match(f.name)
        if m:
            out.append((int(m.group(1)), int(m.group(2))))
    return sorted(set(out))


def is_a4k(model_key: str) -> bool:
    """这个 LIVE_SR 值是不是 Anime4KCPP 插件路线。"""
    return str(model_key or "").lower().startswith("anime4kcpp")


def sr_available() -> list[tuple[str, str, int, list[tuple[int, int]]]]:
    """返回 (key, 显示名, 原生倍率, 档位) —— 只含有引擎的。

    ★ Anime4KCPP 不在其中（它走独立下拉，见 A4K_MODELS）——用户的明确要求：
      它"只留快的方法"，不要混进 TRT 的模型选择里。
    """
    out = []
    for key, disp, sc, d, _ in SR_MODELS:
        if d is None:
            continue
        lad = sr_ladder(key)
        if lad:
            out.append((key, disp, sc, lad))
    return out


# ═══════════════════════════════════════════════════ 补帧模型清单
# ★ 值 = LIVE_RIFE_VER 的取值（必须和 models/rife/rife_v<VER>.onnx 对上）
#   `impl` 固定 1：7 通道（rife_v2/）在本机 TRT 11 上因混合精度 /Mul 解析失败，
#   实测全灭（4.26 / 4.25_lite 都报 ElementwiseOperation PROD must have same input types）。
#   `ms` = **实机 720p 源、2x、fp16 IO、2 流** 的 ms/输出帧（2026-09-30 实测）。
#   tile 倍率不同（4.25_lite 要 128，其余 64/32），代码里按版本自动取。
#
# ★ 2026-10-01 清理：以前这里列了 4.0/4.4/4.6/4.9/4.10 五条，但
#   models/rife/ 里**只有 4.26 和 4.25_lite 两个 .onnx**，那五条靠
#   `cb_rifev` 的 is_file() 过滤永远显示不出来 —— 属于死条目（只会让
#   维护者以为本机还有别的版本）。要加版本请两步一起做：把 .onnx 放进
#   models/rife/，再在这里补一行。
RIFE_MODELS: list[tuple[str, str, float]] = [
    ("4.26", "RIFE 4.26（完整网络，默认）",              9.14),
    ("4.25_lite", "RIFE 4.25 lite（轻量，省显存）",         7.77),
]




# ═══════════════════════════════════════════════════════════ 实测基准（ms/输出帧）
# 来源：实机播放实测（判据 = **丢帧 0 时能跑到多少 fps**），键 = (顺序, 超分模型, 超分开, 补帧开)
#
# ★★ 2026-09-30 教训（差点把这表毁掉）：我一度用 `mpv --untimed --vo=null` 全速跑
#    「处理速度倍率」来重测这张表，量出 720p LR+re2x 全链路只有 1.086×（判"勉强"），
#    就把整表按那套数替换了。**结果与实际播放矛盾**：同一条配置实播
#    （真实时钟 + --vo=gpu-next）是 vf-fps 48.0 / 丢帧 0 / 进度正常，明明跑得动。
#    → `--untimed --vo=null` 全速跑**不是**实时播放的性能代理，它会偏悲观 2~3 倍
#      （去掉时钟节流后，滤镜链的 host↔device 同步反而退化成串行等待）。
#    所以：**这张表只认"实播丢帧 0"的边界**，不许再用离线倍率去覆盖它。
BENCH: dict[tuple, list[tuple[int, float]]] = {
    ("hr", "animev3", True, True): [(640 * 360, 8.74), (854 * 480, 15.59), (1280 * 720, 34.93)],
    ("hr", "realesr-animevideov3_re2x", True, True): [(1280 * 720, 29.91)],
    ("lr", "realesr-animevideov3_re2x", True, True): [(1280 * 720, 18.08)],
    ("lr", "animev3", True, True): [(1280 * 720, 37.88)],
    ("hr", "animev3", True, False): [(1280 * 720, 14.04)],
    ("hr", "realesr-animevideov3_re2x", True, False): [(1280 * 720, 6.50)],
    ("hr", "__off__", False, True): [(1280 * 720, 6.13)],
    # ═══ 2026-09-30 AnimeJaNai 实测（逐帧噪声输入隔离量，单位 ms）═══
    # ★ 这批数用 `_t_noise_cost.py`（逐帧不同噪声，排除常量折叠/缓存命中）量出，
    #   是本机可复现的**隔离口径**。表值 = 该档位超分耗时 + RIFE（~3.6ms）。
    # ★ 关键结论：AnimeJaNai 比自训 re2x 快 2~3 倍，是 1080p 上唯一现实的 2x 模型。
    #   超分隔离实测（ms，括注 出图尺寸）：
    #     档位        AJ Balanced   AJ Sharp1 Bal   AJ Performance   re2x     animev3 4x
    #     640x360        3.3            3.3             4.2          7.9       19.3
    #     854x480        8.0            6.3             7.3         13.0       26.7
    #     960x720        7.9            8.4             8.9         24.2       52.3
    #     1280x720      12.4           11.2            16.2         27.3       67.6
    #     1920x1080     26.9           26.4            28.6         61.5      156.5
    #   RIFE 隔离实测（1080p）：4.4→3.22 / 4.26→3.56 / 4.9→3.61 / 4.25_lite→4.39 ms
    # ⚠ 隔离口径比真播**悲观 2~3 倍**（无时钟节流时 host↔device 退化成串行等待）。
    #   判"能不能跑"永远以真播丢帧为准，表只用来看相对快慢和挑模型。
    ("lr", "AnimeJaNai_HD_V3.1_Balanced", True, True): [
        (960 * 720, 11.5),      # 7.9 + RIFE 3.6
        (1280 * 720, 16.0),     # 12.4 + RIFE 3.6 —— 720p 源@48 真播 0 丢帧 ✓
        (1920 * 1080, 30.5),    # 26.9 + RIFE 3.6 —— 1080p 源配补帧真播丢 9.0/s ✗
    ],
    ("lr", "AnimeJaNai_HD_V3.1_Performance", True, True): [
        (960 * 720, 12.5), (1280 * 720, 19.8), (1920 * 1080, 32.2),
    ],
    ("lr", "AnimeJaNai_HD_V3.1Sharp1_Balanced", True, True): [
        (960 * 720, 12.0), (1280 * 720, 14.8), (1920 * 1080, 30.0),
    ],
    ("hr", "AnimeJaNai_HD_V3.1_Balanced", True, False): [
        (960 * 720, 7.9), (1280 * 720, 12.4), (1920 * 1080, 26.9),
    ],
    ("hr", "AnimeJaNai_HD_V3.1Sharp1_Balanced", True, False): [
        (960 * 720, 8.4), (1280 * 720, 11.2), (1920 * 1080, 26.4),
    ],
    ("hr", "AnimeJaNai_HD_V3.1_Performance", True, False): [
        (960 * 720, 8.9), (1280 * 720, 16.2), (1920 * 1080, 28.6),
    ],
}

DEFAULTS = {
    "upscale": True, "sr": "animev3", "sr_max": 0, "order": "auto",
    "interp": True, "interp_fps": 0, "static_th": 0.003, "rife_ver": "4.26",
    # 全屏开关（播放组的小勾）。默认 False = 老行为（按视频分辨率开窗）。
    "fullscreen": False,
    "vfi": "rife", "play_max_h": 0, "play_max_edge": "short",
    "play_max_custom": False,
    "mv_preset": "fast", "mv_opt": "4",
    "device": 0, "streams": 2, "matrix": "709", "verbose": True,
    "mpv_verbose": False, "concurrent": 6,
    # mpv 程序路径：留空 = 用自带的 mpv/mpv.exe；填了且文件存在 = 用那个（GUI 里可换）。
    "mpv_path": "",
    # 把统计行推到 mpv 的 OSD（左上角小字）—— 全屏播放时不用切回界面看。
    "osd_stat": True,
    "last_in_dir": "", "files": [],
}


# ═══════════════════════════════════════════════════════════════════ 小工具

def load_cfg() -> dict:
    d = dict(DEFAULTS)
    try:
        got = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        if isinstance(got, dict):
            d.update({k: v for k, v in got.items() if k in DEFAULTS})
    except Exception:
        pass
    return d


def save_cfg(d: dict) -> None:
    try:
        tmp = CFG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CFG_PATH)
    except Exception:
        pass


def resolve_mpv(mpv_path: str | None) -> Path:
    """解析实际要用的 mpv 程序。

    - 配置了且文件存在 → 用那个（GUI 里可换成任意 mpv 构建）。
    - 否则回落到自带的 mpv/mpv.exe。
    """
    if mpv_path:
        p = Path(mpv_path)
        if p.is_file():
            return p
    return ROOT / "mpv" / "mpv.exe"


# ════════════════════════════════════════════════════════════════════
# 「关于」对话框内容（与 README「关于」段保持一致；链接均为实测的上游地址）
ABOUT_HTML = """<p><b>作者</b>：bilibili <b>茶茶丸想大摆特摆</b></p>
<p>完全免费、开源：供个人学习 / 使用永久免费，无付费墙、广告或后台上报；
代码全部开源，可随意查看、修改、二次分发。</p>
<p>本质是一条串联脚本：把 mpv、VapourSynth、vs-mlrt、RIFE、Anime4KCPP 等现成
开源组件用 <code>live.vpy</code> 串起来，真正的超分 / 补帧能力都来自上方这些上游项目。</p>
<p><b>协议</b>：本项目整体以 <b>GPL-3.0-or-later</b> 发布（依赖链含 GPL-2.0 与 GPL-3.0，
均带 "or later"，向上兼容）。完整许可证见仓库根目录的 <code>LICENSE</code> 文件。</p>
<table border="0" cellspacing="4" cellpadding="2">
<tr><th align="left">组件</th><th align="left">角色</th><th align="left">协议</th></tr>
<tr><td><a href="https://mpv.io/">mpv</a></td><td>播放器本体</td><td>GPL-2.0-or-later</td></tr>
<tr><td><a href="https://www.vapoursynth.com/">VapourSynth</a></td><td>滤镜框架</td><td>LGPL-2.1-or-later</td></tr>
<tr><td><a href="https://github.com/AmusementClub/vs-mlrt">vs-mlrt</a></td><td>TRT / ONNX / ncnn 推理后端</td><td>GPL-3.0-or-later</td></tr>
<tr><td><a href="https://github.com/TianZerL/Anime4KCPP">Anime4KCPP</a></td><td>Anime4K 超分</td><td>GPL-3.0</td></tr>
<tr><td><a href="https://github.com/the-database/mpv-upscale-2x_animejanai">AnimeJaNai</a></td><td>AnimeJaNai 超分脚本</td><td>GPL-3.0-or-later</td></tr>
<tr><td><a href="https://github.com/hzwer/ECCV2022-RIFE">RIFE</a></td><td>补帧</td><td>Apache-2.0</td></tr>
<tr><td><a href="https://github.com/xinntao/Real-ESRGAN">Real-ESRGAN</a></td><td>超分（代码 + 模型）</td><td>代码 Apache-2.0 / 模型 BSD-3-Clause</td></tr>
</table>
<p>⚠ <b>模型权重额外限制</b>：AnimeJaNai 系列模型（<code>.onnx</code>）为
<b>CC-BY-NC-SA-4.0</b>（署名-非商业性-相同方式共享），<b>不得用于商业用途</b>；
它们只是被本项目调用，不影响代码本身的 GPL 授权。其余模型
（Real-ESRGAN BSD-3-Clause、RIFE Apache-2.0）无此限制。</p>
<p style="color:#999;">免责声明：本项目按「现状」提供，不对播放效果、硬件兼容性或任何使用后果作担保；
自行编译引擎、替换模型、或接入第三方 mpv 构建的风险由使用者自行承担。</p>"""


def show_about(parent) -> None:
    """标准 About 对话框：图标 + 应用名 + 作者 + 开源/协议声明 + 依赖出处 + 免责。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle("关于 mpv - feiyuplayer")
    dlg.setWindowIcon(QIcon(str(ROOT / "console.ico")))
    dlg.setMinimumWidth(540)
    dlg.setMinimumHeight(420)

    ico = QLabel()
    # 作者头像优先（docs/avatar.png），缺了回落到控制台图标
    pm = QPixmap(str(ROOT / "docs" / "avatar.png"))
    if pm.isNull():
        pm = QPixmap(str(ROOT / "console.ico"))
    if not pm.isNull():
        ico.setPixmap(pm.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    title = QLabel("<b>mpv - feiyuplayer</b>")
    title.setFont(QFont("", 14))
    sub = QLabel("mpv 实时超分 + 补帧 控制台")
    sub.setStyleSheet("color: #888;")
    head = QHBoxLayout()
    head.addWidget(ico)
    hl = QVBoxLayout()
    hl.addWidget(title)
    hl.addWidget(sub)
    head.addLayout(hl)
    head.addStretch(1)

    body = QTextBrowser()
    body.setOpenExternalLinks(True)
    body.setHtml(ABOUT_HTML)

    btn_license = QPushButton("查看完整许可证")
    btn_license.clicked.connect(
        lambda: (QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT / "LICENSE")))
                 if (ROOT / "LICENSE").is_file()
                 else QMessageBox.information(dlg, "许可证", "未找到 LICENSE 文件。"))
    )
    btn_ok = QPushButton("确定")
    btn_ok.setDefault(True)
    btn_ok.clicked.connect(dlg.accept)

    bl = QHBoxLayout()
    bl.addWidget(btn_license)
    bl.addStretch(1)
    bl.addWidget(btn_ok)

    v = QVBoxLayout(dlg)
    v.addLayout(head)
    v.addWidget(body, 1)
    v.addLayout(bl)
    dlg.exec()




# ★ 外部 mpv（如 mpv-lazy 这种**自带完整 VapourSynth + Python 环境**的便携包）
#   不能用本项目超分的直接原因：Windows 的 DLL 搜索顺序是
#   「应用程序目录(mpv.exe 同级) 优先于 PATH」。这类 mpv 同级自带
#   `VSScript.dll` + `VSScriptPython38.dll` + `python3xx.dll` + `Lib/`，
#   → mpv 加载 `vf=vapoursynth` 时永远先抢到**它自己那套** VSScript
#   → 绑定它自己的 Python → `live.vpy` 跑在它的环境里，缺本项目的
#   `sr_engine` / `live_static` / `vsmlrt` → 超分**静默失败**（视频照常放）。
#   解决办法：启动前把外部 mpv 同级的 VSScript*.dll 临时改名让位，
#   mpv 在应用目录找不到 → 沿 PATH 回落到本项目 .venv 的 VSScript → 超分生效。
#   退出时（finished）还原；下次启动先清残留，防上次崩溃没还原把 lazy 的 VS 弄废。
_VS_DLL_NAMES = ("VSScript.dll", "VSScriptPython38.dll")


def _neutralize_external_vs(mpv: Path) -> list[Path]:
    """临时让外部 mpv 同级的 VSScript*.dll 让位，返回被改名的原文件列表。

    返回空列表 = 该 mpv 没有自带 VS（无需处理，行为同自带 mpv）。
    """
    touched: list[Path] = []
    d = mpv.parent
    # 1) 先清残留：上次若崩了没还原，这里把 .vsbak 还原回去
    for bak in d.glob("VSScript*.dll.vsbak"):
        orig = bak.with_suffix("")  # 去掉末尾 .vsbak → .dll
        try:
            if not orig.exists():
                bak.rename(orig)
        except OSError:
            pass
    # 2) 再让位当前要用的
    for name in _VS_DLL_NAMES:
        f = d / name
        if f.is_file():
            try:
                f.rename(d / (name + ".vsbak"))
                touched.append(f)
            except OSError:
                pass
    return touched


def _restore_external_vs(touched: list[Path]) -> None:
    """把 _neutralize_external_vs 改名的 dll 还原回去。"""
    for f in touched:
        bak = f.parent / (f.name + ".vsbak")
        try:
            if bak.is_file():
                bak.rename(f)
        except OSError:
            pass


def pick_ladder(w: int, h: int, sr: str, srmax: int = 0,
                sedge: str = "short") -> tuple[int, int]:
    """复刻 live.vpy 的 pick_ladder：给源尺寸挑引擎档位。

    ★ 档位来自**扫盘**（sr_ladder），不是写死的两张表 —— 否则 AnimeJaNai 就得上
      第三张表，而且「引擎没编过」时界面还会显示能选，选完播放才发现跳过超分。
    ★ srmax>0 = 档位边长上限（LIVE_SR_MAX），**判据边**由 sedge 决定：
        sedge="short"（默认）→ 比短边：1080p 源填 1080 → 1920x1080 档
        sedge="long"          → 比长边：填 1920 → 同样 1920x1080 档
      两者表达同一件事，只是「拿哪条边比」不同（用户不用自己换算短边 px）。
      砍完一个档都不剩则忽略上限。
    """
    lad = sr_ladder(sr)
    if not lad:
        # 兜底：老两张表（引擎可能在但命名规则不同）
        lad = LADDER_RE2X if sr == "realesr-animevideov3_re2x" else LADDER_ANIMEV3
    if srmax and srmax > 0:
        _ref = (lambda t: max(t)) if str(sedge) == "long" else (lambda t: min(t))
        capped = [t for t in lad if _ref(t) <= srmax]
        if capped:
            lad = capped
    ar = w / h
    cands = [t for t in lad if abs(t[0] / t[1] - ar) / ar < 0.05 and t[0] >= w]
    if not cands:
        cands = [t for t in lad if t[0] >= w]
    if cands:
        return min(cands, key=lambda t: t[0] * t[1])
    # ★ ③ 源比所有档都宽 → 在**比例接近**的档里取**面积最大**的那个。
    #   ⚠ 两个坑都踩过（2026-10-02）：
    #     · 按面积挑 → live.vpy 的 LIVE_SR_BUILD 会按源尺寸现编引擎，那些尺寸也进
    #       sr_ladder（如 2242x1080，比例 2.076、面积 2.42M），会把 16:9 的 4K 源
    #       抢走 → 中间多跑一趟非等比形变；
    #     · 按"绝对偏差最小"挑 → 854x480(1.7792) 对 2.076 的偏差只比 1280x720
    #       (1.7778) 小 0.0014，降档时会白白选到更小的档、白丢画质。
    #   ⇒ 5% 相对容差圈出"比例差不多"的档，再取面积最大的。必须与 live.vpy 一字不差。
    _d = [abs(t[0] / t[1] - ar) for t in lad]
    #   容差下限取 **ar 的 1%**：640x360 / 1280x720 / 854x480 都是 16:9，必须算同一
    #   比例档，否则会为了 0.0014 的偏差选到更小的那个。与 live.vpy 一字不差。
    _tol = max(min(_d) * 1.05, ar * 0.01)
    _near = [t for t in lad if abs(t[0] / t[1] - ar) <= _tol]
    return max(_near, key=lambda t: t[0] * t[1])


def capped_play_size(w: int, h: int, scale: float,
                     play_max_h: int = 0, edge: str = "short") -> tuple[int, int]:
    """复刻 live.vpy 的 `_play_cap_size`：源尺寸 × 输出倍率，再受「限定播放分辨率」约束。

    ★ 语义必须和 live.vpy 一字不差 —— 预估卡上的输出尺寸要是和真跑出来的不一致，
      用户就会照着一个不存在的尺寸理解性能（这类"两处各写一份规则"的坑踩过）。
    ★ 只当**上限**用：源比 `play_max_h` 还小就按源的来，**绝不放大**。
    ★ `edge`：`short` = 拿短边比（默认，旧行为）/ `long` = 拿长边比
      （"我想限成 1920 宽"这种诉求 → 3840x2160 源限长边 1920 → 1920x1080）。
      与 live.vpy 的 `PLAY_MAX_EDGE` 对应，两处必须一致（`_t_playcap_parity.py`）。
    ★ 等比缩放并取偶数（YUV420 要求）。
    """
    ow, oh = int(round(w * scale)), int(round(h * scale))
    if play_max_h and play_max_h > 0:
        ref = max(ow, oh) if edge == "long" else min(ow, oh)
        if ref > play_max_h:
            k = play_max_h / float(ref)
            ow, oh = int(round(ow * k)), int(round(oh * k))
    # YUV420 要求宽高都是偶数
    return ow + (ow & 1), oh + (oh & 1)


def estimate(order: str, sr: str, up: bool, ip: bool, w: int, h: int,
             srmax: int = 0, sedge: str = "short"):
    """按实测点估算每输出帧耗时。返回 (ms|None, 标签)。

    ★ srmax>0 时会先把「查表用的尺寸」换成**档位上限下真正会用的档位尺寸**：
      超分成本由**推理档位**决定，不由源尺寸决定 —— 1080p 源限到 960 档后，
      实际只跑 1280x720 的推理量，查表就该用 1280x720 而不是 1920x1080。
      不换的话界面上会一直显示 30.5ms「跑不动」，明明降档后 16.0ms 是能跑的。
    """
    if not up and not ip:
        return None, "纯播放"
    # ★★ Anime4KCPP 路线：不走 BENCH 表（那是 TRT 的），用**实测的模型耗时**算。
    #   建模：插件是 YUV 直通、无档位重定向 → 成本 ≈ 正比于**输出像素数**。
    #   基准：720p 源 → 2560x1440 出图（=3.69 MP）实测 ms 值见 A4K_MODELS。
    #   ★ 这里用的是**隔离口径**（逐帧噪声，无时钟节流），比真播悲观 2~3 倍，
    #     所以界面上会标"隔离口径"提醒用户别拿它当绝对 fps。
    if up and is_a4k(sr):
        _am = str(sr).split(":", 1)[1] if ":" in str(sr) else "acnet-gan"
        base_ms = next((m for k, _, m in A4K_MODELS if k == _am), 4.0)
        # Anime4KCPP 只做 min(2.0, 需要倍率) 的放大 → 中间像素数按 2x 算
        omp = (w * 2) * (h * 2)
        ms = base_ms * omp / (2560 * 1440)
        if ip:
            # 补帧成本按 BENCH 里最省的 RIFE 点估（1080p 隔离 3.56ms）
            ms += 3.6 * (w * h) / (1920 * 1080)
        return ms, "Anime4KCPP 实测（隔离口径）"
    if up and srmax and srmax > 0:
        lw, lh = pick_ladder(w, h, sr, srmax, sedge)
        # 档位尺寸比源小 → 用档位尺寸查表（超分是主要成本项）
        if lw * lh < w * h:
            w, h = lw, lh
    key = (order, sr if up else "__off__", up, ip)
    pts = sorted(BENCH.get(key) or [])
    if not pts:
        # 配置没测过 → 退到最接近的：先按"超分开/关 + 补帧开/关"找
        for k, v in BENCH.items():
            if k[2] == up and k[3] == ip:
                pts = sorted(v)
                break
    if not pts:
        return None, "无数据"
    px = w * h
    # ① 正好压在某条实测点上 → 直接用，别再插值（否则会被邻居拉偏）
    hit = next((m for p, m in pts if abs(px - p) / p < 0.03), None)
    if hit is not None:
        return hit, "实测"
    # ② 比最小实测点还小的源 → **取端点，不往小外推**。
    #    线性外推在这里会给出虚高的 fps：re2x 只超分只有 720p/1080p 两个点，
    #    480p 一外推就成 5.1ms ≈ 197fps，比 720p 的 69fps 还快 3 倍 —— 假的。
    #    端点值偏保守，但"宁保守勿乐观"是这张表唯一的活路。
    if px < pts[0][0]:
        return pts[0][1], "端点（按最小实测档）"
    if px > pts[-1][0]:
        if len(pts) == 1:
            # ★ 表里只有这一个点 → 只能往大按**像素面积比例**外推。
            #   （走下面的 pts[-2] 会 IndexError —— 重构时漏了这一支，被冒烟测试抓到。）
            return pts[0][1] * px / pts[0][0], "外推"
        (p0, m0), (p1, m1) = pts[-2], pts[-1]
    else:
        p0, p1, m0, m1 = pts[0][0], pts[0][0], pts[0][1], pts[0][1]
        for (pa, ma), (pb, mb) in zip(pts, pts[1:]):
            if pa <= px <= pb:
                p0, m0, p1, m1 = pa, ma, pb, mb
                break
    if p1 == p0:
        return m0, "实测"
    ms = m0 + (m1 - m0) * (px - p0) / (p1 - p0)
    return ms, ("插值" if pts[0][0] <= px <= pts[-1][0] else "外推")


def build_launch(cfg: dict, files: list[str], pipe: str,
                 vfr: bool = False,
                 real_fps: float = 0.0) -> tuple[dict, list[str]]:
    """纯函数：把界面配置翻译成 (LIVE_* 环境变量, mpv 参数)。

    抽出来是为了能离线断言 —— 参数拼错一个字母，界面上完全看不出来。
    `vfr`      = 首个文件实测是**可变帧率**（见 `ProbeWorker._probe_vfr`）。
                 True → env `LIVE_VFR=1`（live.vpy 跳过 AssumeFPS），
                 并（有 real_fps 时）覆盖容器帧率 **只为让显示对**，详见下面那段。
    `real_fps` = 实测平均帧率；0 = 不知道，不覆盖。
    """
    up = bool(cfg["upscale"])
    ip = bool(cfg["interp"])
    env = {
        "LIVE_UPSCALE": "1" if up else "0",
        "LIVE_INTERP": "1" if ip else "0",
        "LIVE_SR": str(cfg["sr"]),
        "LIVE_SR_MAX": str(int(cfg.get("sr_max") or 0)),
        "LIVE_SR_MAX_EDGE": str(cfg.get("sr_max_edge") or "short"),
        # 最终播放（输出）分辨率的短边上限：0 = 不限。同时决定**补帧在哪一档做**
        # （live.vpy 在超分之后、补帧之前按它缩一次）。
        "LIVE_PLAY_MAX_H": str(int(cfg.get("play_max_h") or 0)),
        # ★ 判据边：预设档位恒为 short；只有「自定义」才会传 long
        #   （由 _collect_cfg 保证 —— 预设模式下 cb_cap_edge 根本不可见）。
        "LIVE_PLAY_MAX_EDGE": str(cfg.get("play_max_edge") or "short"),
        "LIVE_ORDER": str(cfg["order"]),
        # ★ 2026-09-30 改语义：档位是「源帧率 ×n」，不再是绝对 fps。
        #   编码：0 = auto / 负数 = 固定 n 倍 / 正数 = 绝对目标 fps（60/144）。
        #   把原值透传，由 live.vpy 统一解释（它知道源帧率）。
        "LIVE_INTERP_FPS": ("auto" if not cfg.get("interp_fps")
                            else str(cfg["interp_fps"])),
        "LIVE_STATIC_TH": str(cfg.get("static_th", 0)),
        "LIVE_RIFE_VER": str(cfg.get("rife_ver") or "4.26"),
        "LIVE_VFI": str(cfg.get("vfi") or "rife"),
        "LIVE_MV_PRESET": str(cfg.get("mv_preset") or "fast"),
        # ★ mv 内部线程数（mvtools `opt`）。库默认 -1 偏保守，实测 4 快 35%。
        "LIVE_MV_OPT": str(cfg.get("mv_opt") or "4"),
        "LIVE_DEVICE": str(cfg["device"]),
        "LIVE_STREAMS": str(cfg["streams"]),
        "LIVE_MATRIX": str(cfg["matrix"]),
        "LIVE_VERBOSE": "1" if cfg["verbose"] else "0",
        # ★ VFR 源：让 live.vpy **跳过 AssumeFPS**（详见文件末尾「VFR 源」段）。
        "LIVE_VFR": "1" if vfr else "0",
    }
    # ★★ 音频缓冲：mpv 默认 0.2，这里历史上用的是 0.6（3 倍）。
    #   mpv 官方文档原文（--audio-buffer）：
    #     「Making this larger will make soft-volume and other filters react slower,
    #       introduce additional issues on playback speed change, and block the
    #       player on audio format changes. … This option should be used for
    #       testing only. … Default: 0.2 (200 ms).」
    #   —— 它会在**设备缓冲之外再加一层软件缓冲**。这一层如果没被 ao_get_delay
    #   完整算进"音频时钟"，mpv 的时钟就会**领先于你实际听到的声音**：
    #   画面跟着时钟走 → 整体跑到声音前面，而 avsync 全程 ≈0（它只自己跟自己比）。
    #   → 排查音画错位时用 LIVE_AUDIO_BUFFER 二分：0.2（mpv 默认）/ 0.1 / 0。
    #   ★ 2026-10-01：默认值 0.6 → **改回 mpv 自己的默认 0.2**。
    #     起因：本机默认音频输出是**蓝牙（BT66）**，蓝牙渲染链自己还有一层
    #     缓冲（常态 150~300ms），再叠 0.6 的软件缓冲只会把"声音迟到"放大。
    #     想还原旧行为：LIVE_AUDIO_BUFFER=0.6。
    _abuf = (os.environ.get("LIVE_AUDIO_BUFFER") or "").strip() or "0.2"
    _adelay = (os.environ.get("LIVE_AUDIO_DELAY") or "").strip()
    #   LIVE_AUDIO_DEVICE：换输出设备（排查蓝牙延迟用）。取值直接抄
    #   `mpv --audio-device=help` 里的字符串，例如
    #     wasapi/{6563f293-a289-453e-8d23-098a042b5d87}   ← 扬声器(Realtek 有线)
    _adev = (os.environ.get("LIVE_AUDIO_DEVICE") or "").strip()
    args = [
        # ★ 不能用 --no-config：那会连 --config-dir 里的配置和 scripts 一起禁掉。
        #   --config-dir 本身就替代了默认配置位置，所以也不会读到别处的配置。
        f"--config-dir={CN_DIR.as_posix()}",
        f"--vf=vapoursynth=file=live.vpy:concurrent-frames={cfg['concurrent']}",
        "--cache=yes",
        "--demuxer-max-bytes=512MiB",
        "--demuxer-readahead-secs=30",
        f"--audio-buffer={_abuf}",
        "--framedrop=vo",
        # ★★★ 2026-10-01：**「单击进度条 → 画面快过音频」的真凶就是这一行。**
        #   mpv 默认 `--hr-seek-framedrop=yes`：做精确 seek 时靠**丢帧**来加速
        #   （从关键帧解码、把中间那些帧扔掉）。但在 `vf_vapoursynth` 场景下，
        #   这些被扔掉的帧会让 `p->out_pts`（mpv 唯一的时钟来源）与**画面真实内容**
        #   脱节 —— 偏移**恒定**，而且 mpv 自己完全看不见：
        #   `frame-drop-count` 一直是 0、`avsync` 一直 +0.0000。
        #
        #   实测（真 GUI + 你的真片源 + 真鼠标点进度条；把 pts 烧进画面再截图读数）：
        #     默认       ：内容比音频**超前 4.7 ~ 5.3 秒**（超分/补帧/直通**全都中**）
        #     framedrop=no：偏差回到 **0.08 ~ 0.17 秒** ✓
        #   取证：一帧进 VS 前 pts=511.135，出 VS 后被改写成 506.214
        #   （差 4.92s ≈ 118 帧，正是精确 seek 跳过的帧数）；之后 pts 同步累加，
        #   所以这个固定偏移会**一直存在到下一次 seek**。
        #   ⇒ 表现就是"跳完进度条画面和声音差好几秒"。
        #
        #   代价：精确 seek 不再丢帧省事，要多解码一点、略慢（实测生效时间基本不变）。
        #   想还原 mpv 默认（会重新出现错位）：LIVE_HRSEEK_FRAMEDROP=1
        "--hr-seek-framedrop=" + (
            "yes" if (os.environ.get("LIVE_HRSEEK_FRAMEDROP") or "").strip() == "1"
            else "no"),
        "--force-window=yes",
        f"--input-ipc-server={pipe}",
        "--title=mpv live",
    ]
    # ★ 全屏开关（播放组那个小勾）：勾上才加 `--fullscreen`。
    #   不加时 mpv 按**视频自身分辨率**开窗（例如 1080p 源 → 1920x1080）。
    #   放最前面是因为万一 mpv.conf 里有 geometry/fullscreen 之类的设置，
    #   命令行参数在后出现的才生效 —— 但我们这里是同一批参数，顺序不影响；
    #   真正的作用是让它显式、易读。
    if cfg.get("fullscreen"):
        args.insert(0, "--fullscreen")
    # ★ 在画面上显示统计（默认开）：`--osd-level=3` 才会显示 level 3 的 OSD 消息，
    #   GUI 每秒用 IPC 推一行 `show-text` 上去 —— 位置在**左上角小字**，和 mpv
    #   自己的状态行（时间/进度）**分两行、互不覆盖**（截图实测过）。
    #   关掉 = 回到 osd-level 默认（1）：mpv 连自己的状态行都不显示。
    if cfg.get("osd_stat", True):
        args.append("--osd-level=3")
    if cfg["mpv_verbose"]:
        args.append("--msg-level=all=info")
    # ★ 音频延迟微调（LIVE_AUDIO_DELAY，秒，可负）：音画错位按耳朵校准用。
    #   mpv 的 --audio-delay 正值 = **推迟音频**。若"画面快过音频"（音频迟到）
    #   就给负值，例如 -0.2。
    if _adelay:
        try:
            _dv = float(_adelay)
            if abs(_dv) <= 10.0:
                args.append(f"--audio-delay={_dv}")
            else:
                print(f"[warn] LIVE_AUDIO_DELAY={_adelay} 太大（限 ±10s），已忽略")
        except ValueError:
            print(f"[warn] LIVE_AUDIO_DELAY={_adelay!r} 不是数字，已忽略")
    # ★ 换音频输出设备（LIVE_AUDIO_DEVICE）：排查蓝牙/无线音箱的渲染延迟用。
    #   本机默认输出是蓝牙（BT66），蓝牙栈的缓冲 mpv 看不见 → 表现成
    #   "画面快过声音"，且丢帧 0、avsync 恒 0。换成有线设备一听就知道。
    if _adev:
        args.append(f"--audio-device={_adev}")
        print(f"[info] 音频输出设备改为 {_adev}")
    # ★ LIVE_SHOW_TC=1：把**画面内容自己的时间戳**烧在左上角（诊断音画错位用）。
    #   为什么需要：`time-pos` / `audio-pts` / `avsync` 全是 mpv 自己累加出来的数，
    #   彼此天然一致 —— **帧内容有没有错位，它们结构上看不见**。
    #   烧进画面的这个秒数随内容流过整条滤镜链，是唯一能跟 `audio-pts` 对照的真相。
    #   ⚠ 图注里**绝不能有反斜杠**：`%{pts\:hms}` 经 QProcess 传参会多一层转义 →
    #     lavfi 解析失败 → **mpv 把视频轨整个丢弃**（窗口全黑、只放音频）。
    #     所以这里用纯秒数的 `%{pts}`。
    if (os.environ.get("LIVE_SHOW_TC") or "").strip().lower() not in (
            "", "0", "no", "false"):
        #   ⚠ 放在**右上角**（x=w-tw-28）：左上角留给观察窗（Alt+Y 的 OSD），
        #     两个叠在一起会互相压字，读不出来。
        _tc = ("drawtext=fontfile=_arial.ttf:text='%{pts}':fontsize=60:"
               "fontcolor=white:box=1:boxcolor=black@0.78:boxborderw=16:"
               "x=w-tw-28:y=28")
        _new = []
        for a in args:
            if a.startswith("--vf=vapoursynth="):
                _new.append(f"--vf=lavfi=[{_tc}]")
                _new.append("--vf-add=" + a[len("--vf="):])
            else:
                _new.append(a)
        args = _new
        print("[info] LIVE_SHOW_TC=1：左上角烧录「画面内容时间戳」（诊断用，会略增 CPU）")
    # ★★★ 2026-10-01 定案：修「VFR 源（rmvb 这类「容器帧率 ≠ 真实帧率」的假 CFR）
    #   超分/补帧后音画持续漂移」。
    #
    #   机制：mpv 给 VS 的帧**带真实的逐帧时长**（探针实测 `_DurationNum=83000/1000000`
    #   = 0.083s，逐帧在 0.042 / 0.083 之间跳 —— 这就是 VFR 的真身）。但这类源的
    #   容器头写的是 `r_frame_rate`（本片写 30fps，真实平均只有 **18.71fps**），
    #   于是 live.vpy 开头的 `AssumeFPS(container_fps=30)` 把**逐帧时长全拍平成 1/30**
    #   → 视频时间轴按假帧率匀速走 → 与音频持续漂移、且随时间累积。
    #   全程 `avsync`≈0（mpv 拿自己那套错的时间轴跟自己比）、`frame-drop-count`=0
    #   —— **mpv 自己完全看不见**，只有烧 pts / 数帧数才看得见。
    #
    #   ⚠ 别用 `--container-fps-override` 去"修**时间轴**"（2026-10-01 走过的弯路）：
    #     · 它只是把 AssumeFPS 的输入从「假的 30」换成「采样出的另一个数」，
    #       **仍然是拍平**，总时长照样对不上 —— 本片按采样值 22.8fps 算，
    #       25997 帧 = 1140s ≠ 音频 1389.76s；
    #     · 这类源片内帧率本身就在漂（实测 0s→22.80、600s→14.73、1350s→21.20），
    #       **不存在一个能覆盖全片的单一帧率**；
    #     · 而且它会反过来改掉 mpv 注入的 `_DurationNum`（实测把它设成 23.5 后，
    #       输入帧时长跟着变成 2/47），等于把真值也一并弄脏。
    #
    #   ✓ 正解：**VFR 源根本不要 AssumeFPS** —— 保留 mpv 给的原始逐帧时长，时间轴
    #     自然正确（实测 400 帧：不做 = **17.0s**（真值）/ 做了 = 13.0s）。
    #     超分不改帧数、也不丢时长属性（实测「跳过 AssumeFPS + TRT 超分」= 17.0s ✓），
    #     所以超分照常。判别放在 GUI 侧 `ProbeWorker._probe_vfr`（VS 构建期读不到真实
    #     帧，理由见 live.vpy 里的说明），结果走 env `LIVE_VFR=1`。
    #     代价：补帧必须要恒定帧率，VFR 源会**自动跳过补帧**（live.vpy 里 src.fps=0
    #     → interp_multi 返回 0 → 打日志跳过）。
    #   ⚠ 正常 CFR 片源一律 `LIVE_VFR=0`，行为完全不变。
    #
    #   （这一段没有 mpv 参数要加：开关走的是 env 字典里的 LIVE_VFR。）
    #
    # ★ VFR 源：**不加** `--container-fps-override`。
    #   2026-10-02 实测（D.Gray-man RV40 VFR）：加与不加，VS 层输出帧的 `_DurationNum`
    #   完全相同（0.042 / 0.083 真实间隔）、mpv 的 `duration` / `estimated-vf-fps` 也
    #   一模一样 → 该参数在此场景**无任何效果**（原意是想让统计页别显示文件头写的假
    #   帧率，实测根本改不动）。既然无效又可能在别的 mpv/源组合下反噬 `_DurationNum`，
    #   干脆不加：时间轴完全交给 mpv 注入的真实逐帧时长（live.vpy 已跳过 AssumeFPS）。
    _ = (vfr, real_fps)                             # 保留形参，行为：不加任何参数

    args += files
    return env, args



# ═══════════════ 实测能力上限（决定「目标帧率选多少才跑得动」）═══════════════
# 数据来源：2026-09-29 **实机播放**实测，判据是**丢帧 0**（不是"输出帧率显示达标"——
# cf=2 那次 48fps 显示达标却每秒丢 20 帧，教训见下）。环境：cf=3、GPU 空闲。
#
#   480p + LR + re2x：60fps 丢 0、72fps 丢 0、96fps 丢 776  → 上限 72
#   720p + LR + re2x：48fps 丢 0~4、60fps 丢 373            → 上限 48
#   480p + HR + av3 ：48fps 丢 0~1、60fps 丢 313            → 上限 48
#   （720p + HR 比 LR 更慢，直接按 48 封顶）
# 中间分辨率按源像素线性插值；超过表范围就取端点值。
#
# ★★ 别再用 `--untimed --vo=null` 的离线倍率去改这张表（2026-09-30 踩过）：
#    离线量 720p LR+re2x 只有 1.086×（"勉强"），实播却是 vf-fps 48 / 丢帧 0。
#    两者判据不同 —— 这张表是「实时播放丢帧 0 的边界」，离线倍率偏低 2~3 倍，
#    拿它改表等于把能跑的判成跑不动。
CAP_POINTS = {
    ("lr", "realesr-animevideov3_re2x"): [(854 * 480, 72), (1280 * 720, 48),
                                          (1920 * 1080, 24)],
    ("lr", "animev3"): [(854 * 480, 72), (1280 * 720, 48), (1920 * 1080, 24)],
    ("hr", "realesr-animevideov3_re2x"): [(854 * 480, 72), (1280 * 720, 48),
                                          (1920 * 1080, 24)],
    ("hr", "animev3"): [(854 * 480, 72), (1280 * 720, 48), (1920 * 1080, 24)],
}

# 仅补帧（超分关）的实测上限 —— 2026-09-30 用「处理速度倍率」重测（见下）：
#   720p  @60 → 2.01x（120 fps）✅   @48 → 4.05x
#   1080p @48 → 1.87x（90 fps）✅    @60 → 0.89x ✗
# ⚠ 之前这里是 NO_UPSCALE_MAX = 120（拍脑袋，对 1080p 高估 3 倍多）；
#   后来又按 丢帧/s 测过一版（480p 96 / 720p 72 / 1080p 48）——那个指标会骗人：
#   无音轨 + 窗口不在前台时 time-pos 只走 0.66x，"每秒丢帧"根本不可比。
#   现在统一用「处理速度倍率」，并开了 fp16 I/O（搬运量砍半），上限明显抬高了。
NO_UPSCALE_POINTS = [(854 * 480, 120), (1280 * 720, 120), (1920 * 1080, 48)]

# ⚠ 超分那侧仍是 **fp32 I/O**，每帧过主机内存的量很大：
#   720p→1440p  输入 10.5 MB + 输出 42.2 MB = 52.7 MB
#   1080p→2160p 输入 23.7 MB + 输出 94.9 MB = 118.6 MB
#   反推：超分那步约 **80% 的时间是主机搬运**，不是算力。
#   所以上面表里 1080p + 超分只给到 24 fps —— 1080p 源开超分基本不划算
#   （屏幕是 2K 的话，超到 2160p 再降回来纯属浪费）。


def _interp_cap(pts, px: int) -> int:
    """按像素面积在实测点之间插值；超出范围取端点。"""
    pts = sorted(pts)
    if px <= pts[0][0]:
        return pts[0][1]
    if px >= pts[-1][0]:
        return pts[-1][1]
    for (p0, f0), (p1, f1) in zip(pts, pts[1:]):
        if p0 <= px <= p1:
            return int(round(f0 + (f1 - f0) * (px - p0) / (p1 - p0)))
    return pts[-1][1]


def interp_calls(m: float) -> int:
    """每对输入帧要调用几次 RIFE —— 实测规律是 ⌈m⌉−1。

    2x → 1 次；**2.5x → 2 次**；3x → 2 次；4x → 3 次。
    所以 60fps（24→2.5x）要调 2 次却只产 2.5 帧，是性价比最差的档；
    48fps（2x）调 1 次产 2 帧，同样"每帧插一张"却省一半推理。
    """
    return max(math.ceil(m - 1e-9) - 1, 1)


def max_stable_fps(w: int, h: int, order: str, sr: str,
                   up: bool, ip: bool):
    """这套组合实测最高能稳定输出多少 fps（0 丢帧）。None = 不适用。"""
    if not ip:
        return None
    if not up:
        return _interp_cap(NO_UPSCALE_POINTS, w * h)
    pts = CAP_POINTS.get((order, sr))
    return _interp_cap(pts, w * h) if pts else None


AUTO_FPS_MAX = 60


def resolve_multi(sel, base_fps: float) -> float:
    """把「目标帧率」下拉的取值解析成**补帧倍率**（源帧率 × n）。

    ★ 2026-09-30 改语义：档位从「绝对 fps」改成「源 ×n」。
      sel 编码：
        0       → auto：源 <60fps 时取能塞进 60 的最大整数倍（24→2x、30→2x），
                  源 ≥60fps 时取 2x（旧逻辑给 1x = 不插帧，等于白选）
        负数 -n → 固定 n 倍
        正数 fps → 绝对目标帧率，倍率 = fps / 源帧率
    ★ 与 live.vpy 里的 `interp_multi()` 必须逐条保持一致。
    """
    sel = int(sel or 0)           # None / ""（下拉还没建好）一律当 auto
    if sel == 0:
        if base_fps <= 0:
            return 2.0
        if base_fps >= AUTO_FPS_MAX:
            return 2.0            # 源已经够快，至少 ×2 才有意义
        return float(max(2, int(AUTO_FPS_MAX / base_fps)))
    if sel < 0:
        return float(-sel)        # -2/-3/-4 → 2x/3x/4x
    if base_fps > 0:
        return float(sel) / base_fps
    return float(sel) / 24.0


def resolve_target_fps(sel, base_fps: float) -> float:
    """把「目标帧率」下拉的取值解析成**实际输出帧率**（= 源 × 倍率）。

    ★ 整数倍率很关键：每对输入帧调用 RIFE 的次数 = ⌈倍率⌉ − 1，
      2.5x（24→60）要调 2 次、2x（24→48）只调 1 次 —— 多 100% 成本换 25% 帧。
      与 live.vpy 里的 `interp_multi()` 必须保持一致。
    """
    if base_fps <= 0:
        base_fps = 24.0
    return base_fps * resolve_multi(sel, base_fps)


class LineSplitter:
    """字节级行分割：\\r 算「覆盖当前行」，\\n / \\r\\n 算「新行」。

    单独用 \r 的进度条输出必须覆盖上一行，而 mpv / VS 的日志又是普通 \n
    —— 混在一起时不能一把 readline() 了事。
    """

    def __init__(self) -> None:
        self.buf = b""

    def feed(self, data: bytes) -> list[tuple[str, bool]]:
        self.buf += data
        out: list[tuple[str, bool]] = []
        cur = bytearray()
        i, n = 0, len(self.buf)
        while i < n:
            b = self.buf[i]
            if b == 0x0D:                               # \r
                if i + 1 < n and self.buf[i + 1] == 0x0A:   # \r\n
                    out.append((bytes(cur), False))
                    cur.clear()
                    i += 2
                else:
                    out.append((bytes(cur), True))          # 覆盖
                    cur.clear()
                    i += 1
                continue
            if b == 0x0A:                               # \n
                out.append((bytes(cur), False))
                cur.clear()
                i += 1
                continue
            cur.append(b)
            i += 1
        self.buf = bytes(cur)
        # 避免超长无换行的输出把内存吃掉
        if len(self.buf) > 1 << 16:
            out.append((self.buf, False))
            self.buf = b""
        return [(t.decode("utf-8", "replace"), r) for t, r in out]


# ═════════════════════════════════════════════════════════════ 后台：探分辨率

class ProbeWorker(QThread):
    """用 ffmpeg 读容器头拿分辨率和帧率（不解码），供预估和 auto 判断用。

    ★ 2026-10-01 追加 `vfr`：容器头写的帧率**可能是假的**（rmvb 这类 VFR 冒充
      CFR），必须真解一段才知道。只对**第一个**文件做（要解码，约 1 秒）。
    """

    probed = Signal(str, int, int, float)          # (path, w, h, fps)
    vfr = Signal(str, bool, float)                 # (path, 是否VFR, 实测平均fps)
    vfr_done = Signal(str)                          # (path) VFR 判定**完成**（无论结果）

    def __init__(self, paths: list[str], parent=None) -> None:
        super().__init__(parent)
        self.paths = paths
        self._stop = False

    def run(self) -> None:
        for i, p in enumerate(self.paths):
            if self._stop:
                return
            wh = self._probe(p)
            if wh:
                self.probed.emit(p, wh[0], wh[1], wh[2])
            if i == 0:
                is_vfr, rf = self._probe_vfr(p)
                self.vfr_done.emit(p)               # 判定完成（含 CFR）→ _start 据此避免"首次未判定"
                if is_vfr or rf > 0:
                    self.vfr.emit(p, is_vfr, rf)

    @staticmethod
    def _probe_vfr(path: str, seconds: int = 15) -> tuple[bool, float]:
        """真解前 N 秒 → `(是否可变帧率, 实测平均 fps)`。

        为什么非解不可：容器帧率是**元数据**，rmvb 这类 VFR 会写死一个假值
        （本机实测 D.Gray-man：头里 30fps，真实平均 **18.71fps**）。ffprobe
        只会照抄那个假值，只有解码才知道真相。

        ★ VFR 判据 = **逐帧 pts 间隔不均**：CFR 源即便 29.97fps 抖动也只有
          几个百分点，而本片在 42ms / 83ms 之间整倍跳（max/min = 2.0）。
          取阈值 1.5 倍，两头都留足余量。
        ⚠ 不能拿 ffmpeg 的 `duration_time` 判 —— 它照抄 filter 的固定
          frame_rate，恒为 0.033，什么也看不出来（踩过）。要看 `pts_time`。

        成本：848x480 rv40 解 15 秒 ≈ 1 秒墙钟；只对首个文件做一次。
        """
        if not FFMPEG.is_file():
            return (False, 0.0)
        try:
            r = subprocess.run(
                [str(FFMPEG), "-hide_banner", "-nostdin",
                 "-t", str(seconds), "-i", path,
                 "-map", "0:v:0", "-an", "-sn", "-dn",
                 "-vf", "showinfo", "-f", "null", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            txt = r.stdout.decode("utf-8", "replace")
        except Exception:
            return (False, 0.0)

        # ── VFR 判定：逐帧 pts 间隔
        pts = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", txt)]
        is_vfr = False
        if len(pts) >= 10:
            diffs = [d for d in (pts[i + 1] - pts[i] for i in range(len(pts) - 1))
                     if d > 0]
            if diffs and min(diffs) > 0:
                is_vfr = (max(diffs) / min(diffs)) > 1.5

        # ── 实测平均帧率（ffmpeg 的进度行，\r 分段里抓最后一个）
        fps = 0.0
        fr = re.findall(r"frame=\s*(\d+)", txt)
        tm = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
        if fr and tm:
            n = int(fr[-1])
            hh, mm, ss = tm[-1]
            secs = int(hh) * 3600 + int(mm) * 60 + float(ss)
            if n >= 10 and secs >= 1.0:
                fps = n / secs
        return (is_vfr, fps)

    @staticmethod
    def _probe(path: str):
        if not FFMPEG.is_file():
            return None
        try:
            r = subprocess.run(
                [str(FFMPEG), "-hide_banner", "-i", path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            txt = r.stdout.decode("utf-8", "replace")
        except Exception:
            return None
        m = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", txt)
        if not m:
            m = re.search(r"\s(\d{2,5})x(\d{2,5})[\s,\[]", txt)
        if not m:
            return None
        w, h = int(m.group(1)), int(m.group(2))
        if not (16 <= w <= 8192 and 16 <= h <= 8192):
            return None
        fps = 24.0
        mf = re.search(r"([\d.]+)\s+fps", txt)
        if mf:
            try:
                v = float(mf.group(1))
                if 1.0 <= v <= 480.0:
                    fps = v
            except ValueError:
                pass
        return (w, h, fps)

    def stop(self) -> None:
        self._stop = True


# ══════════════════════════════════════════════════ 后台：mpv IPC 读丢帧状态

class MpvStat(QThread):
    """通过 mpv 的命名管道订阅运行时属性。

    ⚠ Windows 上命名管道的 read 会阻塞，所以这里**用订阅制**——
    observe_property 之后 mpv 会在属性变化时主动推送，readline() 就有数据可读。
    轮询式 get_property 需要非阻塞读，Windows 命名管道没现成办法。

    ★★ 2026-10-02 更正：早先这里把 `estimated-vf-fps` 当成"滤镜实际跑出来的帧率"
      —— **不对**。它是**滤镜链声明的输出帧率**（`estimated` = 推算，不是实测）：
      超分不改帧率 → 它就等于**源帧率**；补帧 2x → 48。**全程恒定不动**，
      反映不了真实速度。真正的实测只有两条路（本类都采了）：
        · `estimated-frame-number` 差分 → 播放推进速率（mpv 降速慢放时它会掉下来）；
        · `frame-drop-count` 差分 → 丢帧速率 ⇒ **实际显示 = 输出 − 丢帧**。
      mpv **没有**"实测渲染 fps"这个属性：`estimated-display-fps` 在本机 mpv 0.41
      上**恒 unavailable**（真窗口 + `--vo=gpu` 实测确认），所以只能这样推。

    ★ 不用信号传数据：走 Signal 要跨线程逐次拷贝。改成「写时复制」——每次都整体
    替换 `snapshot` 的引用，界面直接读这个引用，**零锁且永远是自洽快照**。
    ★ 2026-09-30 更正：早先这里写"mpv 每帧把 16 个属性各推一遍（≈880 次/秒）"，
      **实测高估了 28 倍**（`_t_subrate.py`，1280x720 + re2x 超分）：
        事件 ≈ **31 /s**、**2.2 KB/s**；其中 `time-pos` 占 ~87%，
        其余 15 个属性**初始化时各推 1 次就再也不动**。
      所以本线程的 GIL/拷贝开销**完全可以忽略**（≈2 KB/s）——
      "订阅拖累 mpv"这条排查方向是**错的**，别再往这找音画问题。
    """

    PROPS = {
        1: "estimated-vf-fps",          # 滤镜链**声明**的输出帧率（恒定，见类注释）
        2: "frame-drop-count",          # 显示层丢帧
        3: "mistimed-frame-count",      # 晚点帧（本机 mpv 0.41 恒 unavailable）
        4: "decoder-frame-drop-count",  # 解码器丢帧
        5: "vo-delayed-frame-count",    # 渲染队列积压
        6: "time-pos",
        7: "duration",
        8: "media-title",
        9: "video-out-params/w",        # ★ 滤镜链**之后**的尺寸（超分后的真实输出）
        10: "video-out-params/h",
        11: "estimated-frame-number",   # ★ 实测帧号，递增 → 差分即真实帧率
        12: "pause",
        13: "playlist-pos",
        14: "playlist-count",
        15: "cache-speed",
        16: "demuxer-cache-duration",
        17: "display-fps",              # 显示器刷新率（真窗口下才有值）
    }
    BY_NAME = {v: k for k, v in PROPS.items()}

    def __init__(self, pipe: str, parent=None) -> None:
        super().__init__(parent)
        self.pipe = pipe
        # 写时复制的状态快照：界面直接读这个引用，不用信号、不用锁
        self.snapshot: dict = {}
        # 连接诊断：连不上时界面要能说清是"还没起来"还是"管道打不开"
        self.opened = False
        self.last_error = ""
        self._stop = False
        self._fh = None
        self._wlock = threading.Lock()      # 保护 push() 与 run() 对管道的并发访问

    def run(self) -> None:
        deadline = time.time() + 8.0
        while time.time() < deadline and not self._stop:
            try:
                self._fh = open(self.pipe, "r+b", buffering=0)
                self.opened = True
                self.last_error = ""
                break
            except OSError as e:
                self.last_error = f"{type(e).__name__}: {e}"
                time.sleep(0.25)
        if self._fh is None:
            self.last_error = self.last_error or "打开管道超时"
            return
        try:
            for rid, prop in self.PROPS.items():
                cmd = {"command": ["observe_property", rid, prop]}
                self._fh.write(json.dumps(cmd).encode() + b"\n")
            state: dict = {}
            while not self._stop:
                line = self._fh.readline()
                if not line:
                    break
                try:
                    ev = json.loads(line.decode("utf-8", "replace"))
                except Exception:
                    continue
                if ev.get("event") != "property-change":
                    continue
                name = self.PROPS.get(ev.get("id"))
                if name is None:
                    continue
                # 写时复制：整体换引用，读方拿到的永远是一个完整自洽的快照
                nxt = dict(state)
                nxt[name] = ev.get("data")
                state = nxt
                self.snapshot = state
        except Exception:
            pass
        finally:
            try:
                if self._fh:
                    self._fh.close()
            except Exception:
                pass

    def push(self, command: list) -> bool:
        """从**别的线程**投递一条 IPC 命令（不等回复）。成功返回 True。

        ★ 命名管道是全双工的：本线程是唯一的读者，所以并发**写**不会跟读串味。
          加锁只为挡住 `_fh` 被 run() 打开/关闭的那一瞬间。
        """
        data = json.dumps({"command": list(command)}).encode() + b"\n"
        with self._wlock:
            fh = self._fh
            if fh is None or not self.opened:
                return False
            try:
                fh.write(data)
                return True
            except (OSError, ValueError):
                # 管道已断 / 文件已关（mpv 退出或崩溃）：标记失效，别再写
                self._fh = None
                self.opened = False
                return False

    def stop(self) -> None:
        self._stop = True



class SysStat(threading.Thread):
    """每秒采集 GPU 利用率/显存、CPU 利用率（写时复制快照，同 MpvStat 的风格）。

    ★ GPU 用 `nvidia-smi` 子进程而不是 pynvml：本机实测**单次只要 64ms**，
      每秒一次毫无压力；nvidia-smi 跟着驱动走，比第三方包更不容易坏。
      没装 pynvml 也不用往 .venv 里加依赖。
    ★ CPU 用 psutil（GUI 环境已带 7.2.2）。`cpu_percent(interval=None)` 是
      **非阻塞**的，返回「自上次调用以来」的平均值 —— 所以第一次调用返回 0，正常。
    ★ `CREATE_NO_WINDOW`：GUI 进程里 spawn 子进程**不能弹黑框**。
    """

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.snapshot: dict = {}
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            import psutil
            psutil.cpu_percent(interval=None)       # 第一跳：建立基准
            _ps = psutil
        except Exception:
            _ps = None
        while not self._stop:
            t0 = time.perf_counter()
            snap: dict = {}
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu,"
                                    "memory.used,memory.total",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=3.0,
                    creationflags=subprocess.CREATE_NO_WINDOW).stdout
                _u, _mu, _mt = (x.strip() for x in out.strip().split(","))
                snap = {"gpu_util": int(_u), "gpu_mem_used": int(_mu),
                        "gpu_mem_total": int(_mt)}
            except Exception:
                pass                                 # 没 N 卡 / 驱动忙 → 本轮跳过
            if _ps is not None:
                try:
                    snap["cpu_util"] = int(_ps.cpu_percent(interval=None))
                except Exception:
                    pass
            if snap:
                self.snapshot = snap                 # 写时复制：读方永远拿到自洽快照
            time.sleep(max(0.2, 1.0 - (time.perf_counter() - t0)))


def _hw_names() -> tuple[str, str]:
    """一次性取 (GPU 短名, CPU 短名)，失败给空串。

    GPU:  "NVIDIA GeForce RTX 4080" → "RTX 4080"（剥掉厂商前缀，OSD 上够用）
    CPU:  "12th Gen Intel(R) Core(TM) i7-12700KF" → "i7-12700KF"（取型号 token；
          AMD 的 "…Ryzen 9 7950X 16-Core Processor" 也走正则拿 "Ryzen 9 7950X"）
    """
    gpu = cpu = ""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3.0,
            creationflags=subprocess.CREATE_NO_WINDOW).stdout
        gpu = re.sub(r"^(NVIDIA\s+|GeForce\s+)+", "",
                     out.strip().splitlines()[0], flags=re.I).strip()
    except Exception:
        pass
    try:
        import winreg
        _k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        _raw, _ = winreg.QueryValueEx(_k, "ProcessorNameString")
        _m = re.search(r"(i[3579]-\w+|Ryzen[ \w]*?\d{3,5}\w*)", _raw, re.I)
        cpu = _m.group(1) if _m else " ".join(_raw.split())
    except Exception:
        pass
    return gpu, cpu


# ═══════════════════════════════════════════════════════════════════ 主窗口

QSS = """
QWidget { background: #1b1b1f; color: #dcdce4; font-size: 13px; }
QGroupBox { border: 1px solid #33333c; border-radius: 8px;
            margin-top: 16px; padding: 12px 10px 10px 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px;
                   color: #9d9db0; }
QPushButton { background: #2a2a33; border: 1px solid #3a3a46;
              border-radius: 6px; padding: 5px 12px; }
QPushButton:hover { background: #33333f; border-color: #4a4a58; }
QPushButton:disabled { color: #575764; background: #202027; border-color: #2c2c36; }
QPushButton#primary { background: #1d6fd0; border-color: #2b82e6; color: #ffffff; }
QPushButton#primary:hover { background: #2a7de0; }
QPushButton#primary:disabled { background: #26364a; border-color: #2f4258; color: #6d7d90; }
QPushButton#danger { background: #7d2b2b; border-color: #9a3a3a; color: #ffffff; }
QPushButton#danger:hover { background: #933333; }
QPushButton#danger:disabled { background: #33232a; border-color: #3d2b32; color: #6d5d64; }
QComboBox, QSpinBox { background: #23232b; border: 1px solid #3a3a46;
                      border-radius: 6px; padding: 4px 6px; min-height: 22px; }
QComboBox:disabled, QSpinBox:disabled { color: #575764; background: #1f1f26;
                                        border-color: #2c2c36; }
QComboBox QAbstractItemView { background: #23232b; border: 1px solid #3a3a46;
                              selection-background-color: #2b3a52; }
QCheckBox { spacing: 7px; }
QListWidget { background: #15151a; border: 1px solid #33333c; border-radius: 8px;
              padding: 4px; }
QListWidget::item { padding: 5px 6px; border-radius: 5px; }
QListWidget::item:selected { background: #2b3a52; color: #eaeaf2; }
QPlainTextEdit { background: #131317; border: 1px solid #33333c;
                 border-radius: 8px; padding: 4px; }
#rightScroll { background: transparent; border: 0; }
#rightScroll > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: #1b1b1f; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #3c3c48; border-radius: 6px; min-height: 34px; }
QScrollBar::handle:vertical:hover { background: #4a4a58; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: #1b1b1f; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #3c3c48; border-radius: 6px; min-width: 34px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("mpv - feiyuplayer")
        self.setWindowIcon(QIcon(str(ROOT / "console.ico")))
        # ★ 2026-09-30：按**屏幕可用尺寸**算初始窗口，别写死小尺寸。
        #   本机屏 2560x1400，原来写死的 1260x900 在参数面板加宽到 520 后
        #   会把参数区压到必须滚动（内容 ~780px > 可用 ~430px）。
        #   取 min(屏幕 78%, 1680) × min(屏幕 82%, 1180)：大屏铺得开、
        #   小屏也不越界（setMinimumSize 兜底）。
        try:
            _scr = QApplication.primaryScreen().availableGeometry()
            _w = min(int(_scr.width() * 0.78), 1680)
            _h = min(int(_scr.height() * 0.82), 1180)
        except Exception:                             # noqa: BLE001
            _w, _h = 1380, 940
        self.resize(max(_w, 1240), max(_h, 880))
        self.setMinimumSize(1100, 660)
        self.setAcceptDrops(True)

        self.cfg = load_cfg()
        self.media: dict[str, tuple[int, int, float]] = {}
        # 首个文件的 VFR 判定 + 实测平均帧率（解码数出来的），键同 media。
        # 用来判容器帧率是不是假的（rmvb 这类 VFR 冒充 CFR），见 ProbeWorker._probe_vfr。
        self.vfr_src: dict[str, tuple[bool, float]] = {}
        # 已完成 VFR 判定的文件（含 CFR）—— _start 据此判断"是否还需同步补判"
        self._vfr_ready: set[str] = set()
        # 超分引擎预热：已预建/在建的 "模型@WxH" 标签 + 在跑的 QProcess（防重复/防 GC）
        self._warm_tags: set[str] = set()
        self._warm_procs: list = []
        self.engines = self._scan_engines()
        self._probe_worker: ProbeWorker | None = None
        self._stat: MpvStat | None = None
        self._splitter = LineSplitter()
        self._last_was_replace = False
        self._kill_timer: QTimer | None = None

        # 运行时状态（由 mpv IPC 推送，UI 定时刷新）
        self._fps_hist: list[tuple[float, float]] = []      # (monotonic, vf_fps)
        self._drop_hist: list[tuple[float, int]] = []       # (monotonic, 累计丢帧)
        self._fno_hist: list[tuple[float, int]] = []        # (monotonic, 实测帧号)
        self._target_fps = 48.0
        self._plan_fps: float | None = None
        self._last_stat_log = 0.0
        self._last_osd_push = 0.0        # 上次把统计推给 mpv OSD 的时刻
        # 硬件型号（静态，启动时拿一次）；SysStat 在播放开始时才启动
        self._gpu_short, self._cpu_short = _hw_names()
        self._sys: SysStat | None = None
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(250)
        self._ui_timer.timeout.connect(self._refresh_live)

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.setWorkingDirectory(str(ROOT))
        self.proc.readyReadStandardOutput.connect(self._on_mpv_out)
        self.proc.finished.connect(self._on_mpv_finished)
        self.proc.errorOccurred.connect(lambda e: self._log(f"[控制台] mpv 进程错误 {e}\n"))

        self._build_ui()
        self._restore_cfg()

        # 初始状态统一放在最后刷（此时所有控件都已存在）
        self._refresh_enabled()
        self._update_plan()
        self._log(f"[控制台] 工作目录 {ROOT}\n")
        if not resolve_mpv(self.cfg.get("mpv_path")).is_file():
            self._log(f"[控制台][!] 找不到 {resolve_mpv(self.cfg.get('mpv_path'))}\n")
        if not VSROOT.is_dir():
            self._log(f"[控制台][!] 找不到 {VSROOT}（VapourSynth 位置）\n")

    # ────────────────────────────────────────────────── 扫引擎 / 默认目录

    def _scan_engines(self) -> set[str]:
        try:
            return {p.name for p in ENGINE_DIR.glob("*.engine")}
        except Exception:
            return set()

    # ══════════════════════════════════════════════════════════ 构建界面

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        vsplit = QSplitter(Qt.Vertical)
        outer.addWidget(vsplit, 1)

        # ── 上半：左队列 / 右参数 ─────────────────────────────
        top = QWidget()
        h = QHBoxLayout(top)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)

        h.addWidget(self._build_queue(), 1)
        h.addWidget(self._build_params(), 0)
        vsplit.addWidget(top)

        # ── 下半：日志 ────────────────────────────────────────
        vsplit.addWidget(self._build_log())
        # ★ 2026-09-30 调整比例：上半给足、日志压扁。
        #   参数面板的真实需求：滚动区内容 ~750px + 预估卡 66 + 播放组 196，
        #   合计 ~1010px。日志只需要够看最近几行（用户明确要求"再压扁一点"）。
        #   9:2 的拉伸比 + setSizes([1000, 190]) 让首屏就把参数铺完、不滚动。
        #   真要看日志拖分隔条即可（QSplitter 默认就是可拖的）。
        vsplit.setStretchFactor(0, 20)
        vsplit.setStretchFactor(1, 2)
        vsplit.setSizes([1100, 150])

    # ── 左：播放队列 ─────────────────────────────────────────
    def _build_queue(self) -> QWidget:
        box = QGroupBox("播放队列")
        v = QVBoxLayout(box)
        v.setSpacing(8)

        self.lst_files = QListWidget()
        self.lst_files.setSelectionMode(QListWidget.ExtendedSelection)
        self.lst_files.setMinimumWidth(360)
        self.lst_files.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.lst_files.currentItemChanged.connect(self._on_sel_changed)
        self.lst_files.itemSelectionChanged.connect(self._update_plan)
        v.addWidget(self.lst_files, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        for text, slot, tip in (
            ("添加文件", self._add_files, "选择视频文件（也可以直接拖进来）"),
            ("添加文件夹", self._add_folder, "把文件夹里所有视频加进来"),
            ("移除", self._del_selected, "移除选中的条目"),
            ("清空", self._clear_files, "清空整个队列"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        self.btn_up = QPushButton("上移")
        self.btn_down = QPushButton("下移")
        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_down.clicked.connect(lambda: self._move(1))
        row.addWidget(self.btn_up)
        row.addWidget(self.btn_down)
        row.addStretch(1)
        v.addLayout(row)

        hint = QLabel("把视频或文件夹直接拖进窗口也可以。双击条目播放该文件。")
        hint.setStyleSheet("color: #7f7f93;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self.lst_files.itemDoubleClicked.connect(lambda *_: self._start())
        return box

    # ── 右：参数面板（套滚动区，窗口拉小也不压扁控件）─────────
    def _build_params(self) -> QWidget:
        side = QWidget()
        # ★ 2026-09-30 加宽 400 → 520 → **540**：超分模型的显示名很长
        #   （「ACNet-F8B18（VGG，重，4.37ms）」这种），400 宽时下拉里**看不全**
        #   —— 用户明确反馈"根本看不清里面的模型名"。
        #   再 +20（520→540）是因为模型名改成「animevideov3_re2x　（重训为原生导出 2X）」
        #   后实测需要 400px 而下拉只分到 398px —— **差 2px 就出省略号**。
        #   `minimumContentsLength` 只管"最小要求"，真正拿到多少由 label 列挤剩下的
        #   空间决定，所以唯一的解法是加宽整个侧栏。
        side.setFixedWidth(540)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(8)

        scroll = QScrollArea()
        scroll.setObjectName("rightScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setMinimumHeight(110)   # 窗口再小也留一条，先压日志别压参数

        host = QWidget()
        # ★ 跟着 side 一起加宽（360 → 470 → 490，2026-09-30）：滚动区里内容窄于视口时
        #   控件仍会按 minimumWidth 挤，导致下拉又变窄、白加宽外层。
        host.setMinimumWidth(490)
        pv = QVBoxLayout(host)
        pv.setContentsMargins(0, 0, 6, 0)
        # ★ 从 10 → 6：四个分组（超分/补帧/顺序/高级）之间的空白累计能省 ~16px，
        #   是参数区能不能塞进上半屏的关键零头。
        pv.setSpacing(6)

        # 超分
        g_up = QGroupBox("超分")
        gu = QGridLayout(g_up)
        gu.setHorizontalSpacing(8)
        gu.setVerticalSpacing(5)
        self.chk_up = QCheckBox("启用超分（放大画面）")
        gu.addWidget(self.chk_up, 0, 0, 1, 3)
        # ★★ 「超分引擎」——2026-09-30 加 Anime4KCPP 时引入。
        #   两类引擎的实现方式完全不同，**不该混在一个下拉里**（用户明确要求）：
        #     TRT        = 预编译 .engine，走 core.trt.Model；要 pick_ladder 选档
        #     Anime4KCPP = VS 原生插件，YUV 直通；factor 直接算，无档位概念
        #   切引擎时下面的「模型」下拉**整表换掉**（见 _on_sr_engine）。
        self.lb_sreng = QLabel("超分引擎")
        self.cb_sreng = QComboBox()
        self.cb_sreng.addItem("TensorRT（预编译引擎，画质档位多）", "trt")
        if _A4K_ON:
            self.cb_sreng.addItem("Anime4KCPP（VS 插件，明显更快）", "a4k")
        self.cb_sreng.setToolTip(
            "两套超分实现（插件那条快得多）：\n\n"
            "  TensorRT： 预编译 .engine 走 core.trt.Model。\n"
            "             链路是 YUV→RGBS(float)→引擎→RGBS→YUV，\n"
            "             两次 float 往返每帧要搬几百 MB 主机内存。\n"
            "  Anime4KCPP：VapourSynth 原生插件（anime4kcpp.dll）。\n"
            "             整条链路本来就是 YUV420P8，**零格式转换**。\n"
            "             ACNet / ARNet / ArtCNN / FSRCNNX 共 12 个模型。\n\n"
            "⚠ Anime4KCPP **没有原生 4x 模型**（全是 2x 网络）。\n"
            "  输出倍率选 ×3/×4 时，它只做 2x，剩下的靠 bicubic 放大。\n"
            "  要真 4x 就用 TensorRT 那档的 realesr-animevideov3。\n\n"
            "★ 追求速度 → Anime4KCPP；追求 4x 画质 → TensorRT。")
        gu.addWidget(self.lb_sreng, 1, 0)
        gu.addWidget(self.cb_sreng, 1, 1, 1, 2)
        self.lb_sr = QLabel("模型")
        self.cb_sr = QComboBox()
        # ★★ 2026-09-30 用户要求的说明：模型倍率是**固定的**，跟导出分辨率无关。
        #   这条认知是理解整个尺寸链路的前提 —— 选错倍率只能靠 cap 兜，
        #   而"该选几倍"取决于**导入（源）图像**有多大。
        self.cb_sr.setToolTip(
            "超分模型。**模型实际运行时都是固定倍率**，和导出分辨率无关 ——\n"
            "它只会把输入的图像放大名字里那个倍数（2x / 4x），仅此而已。\n"
            "\n"
            "所以「倍率后缀」怎么选，要**根据导入图像**来定：\n"
            "  720p 源 + 2x → 输出 1440p（一般够用）\n"
            "  720p 源 + 4x → 输出 2880p（远超屏幕，得用「限定播放分辨率」压住）\n"
            "  1080p 源 + 2x → 输出 2160p\n"
            "\n"
            "★ 倍率不改变推理成本（选档只看源尺寸），它决定的是**输出多大**：\n"
            "  输出 = 源 × 模型倍率，再受「限定播放分辨率」压一次。\n"
            "  想\"只清洗不放大\"（1080p 源 + 2x 模型仍出 1080p）→ 把上限设成 1080。\n"
            "\n"
            "各条目的耗时/特性看鼠标悬停在下拉项上（每种模型都有单独说明）。")
        # ★★ 下拉文字被截断的真因（2026-09-30 用户反馈"看不清模型名"）：
        #   QComboBox 的默认 sizeHint 只按**当前项**算，不按最长项 ——
        #   QGridLayout 于是只给它一个"够放当前项"的宽，长名字（ACNet-F8B18…）
        #   全被省略号吃掉。两个修法一起上：
        #     ① setMinimumContentsLength：把宽度按**字符数**钉死（不依赖字体度量）
        #     ② SizeAdjustPolicy=AdjustToMinimumContentsLengthWithIcon
        #   这样它要到的宽度是稳定的，且切换模型（名字长短变化）不会再跳。
        # ⚠ 40 → 42（2026-09-30）：模型名改成「animevideov3_re2x　（重训为原生导出 2X）」
        #   后实测需要 400px，而 40 字符只给到 398px —— 差 2px 就出省略号。
        #   `_t_a4k_ui.py` 的截断断言当场抓到。这个值必须 ≥ 最长显示名。
        self.cb_sr.setMinimumContentsLength(42)
        self.cb_sr.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        # ★ 固定垂直尺寸策略：AdjustToMinimumContentsLengthWithIcon 会让控件
        #   **横向** sizeHint 随内容变，而 QComboBox 的默认垂直策略是 Preferred，
        #   于是切换引擎（6 项 ↔ 12 项）时整个「超分」分组的高度会被撑大
        #   且**不会缩回去**（实测 206 → 221px，多出的 15px 直接变成滚动条）。
        #   钉死垂直方向后，换表只影响宽度，高度恒定。
        self.cb_sr.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        gu.addWidget(self.lb_sr, 2, 0)
        gu.addWidget(self.cb_sr, 2, 1, 1, 2)
        # ★ 引擎下拉同理（「TensorRT（预编译引擎，画质档位多）」也不短）
        self.cb_sreng.setMinimumContentsLength(30)
        self.cb_sreng.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cb_sreng.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # ★ 标签列**不参与拉伸**：不设的话标签会把余量吃走、下拉得不到全部剩余宽。
        gu.setColumnStretch(0, 0)
        gu.setColumnStretch(1, 1)
        gu.setColumnStretch(2, 0)
        # ★★ 「输出倍率」下拉 **2026-09-30 已删除**（用户提问后查证确认冗余）。
        #   为什么删（三条依据，不是手感）：
        #     ① 改不了推理倍率 —— 模型倍率是设计好的（animev3=4x / 其余 2x）。
        #     ② 改不了选档、也不影响推理成本 —— `pick_ladder` 只看**源尺寸**。
        #     ③ 它唯一作用是"最后那次 resize 的目标"，而那正是
        #        「限定播放分辨率」的职责；cap 生效时它**完全被覆盖**
        #        （源 3840x2160 + cap 1080/short：无论倍率 1 还是 2 都出 1920x1080）。
        #   ⚠ 还误导人：选 ×4 配 2x 模型只是**假放大**（推理 2x 再 bicubic 拉到 4x）。
        #   → 现在的语义：**输出尺寸 = 源 × 模型原生倍率，再受「限定播放分辨率」压**。
        #     想"只清洗不放大"（1080p 源 + 2x 模型仍出 1080p）→ 把 cap 设成 1080，
        #     比原来的"倍率 ×1"更直观。
        # ★ 导入分辨率上限（LIVE_SR_MAX）：跑不动时的降本开关。
        #   超分成本 ~正比档位像素数。1080p 源默认吃 1920x1080 档，
        #   配补帧后必然丢帧；限制到 720（短边）就掉到 1280x720 档。
        #   ★ 2026-10-01：改成「预设 / 自定义」二选一（与下面「限定播放分辨率」同款），
        #     并**删掉「960p 档」** —— 实测档位表只有
        #     640x360 / 854x480 / 960x720 / 1280x720 / 1920x1080，
        #     而 SR_MAX=960 与 SR_MAX=720 **挑到的都是 1280x720 档**
        #     （960x720 是 4:3，被宽高比筛选排除），留着纯属误导。
        #     预设标签直接写成档位本身的 WxH，一眼能对上引擎文件名。
        self.lb_srmax = QLabel("导入分辨率上限")
        self.cb_srmax = QComboBox()
        for lbl, v in (("不限（画质优先）", 0), ("1920*1080", 1080),
                       ("1280*720", 720)):
            self.cb_srmax.addItem(lbl, v)
        self.cb_srmax.setToolTip(
            "限制超分**推理档位**的短边上限（只按**短边**判）。\n"
            "超分只在这个上限以内挑档 —— 档位越小，超分越快。\n"
            "本机档位表（WxH / 短边）：\n"
            "  1920x1080（1080） / 1280x720（720） / 960x720（720）\n"
            "  854x480（480）   / 640x360（360）\n"
            "\n"
            "预设各档的实际效果：\n"
            "  不限      → 1080p 源用 1920x1080 档（画质最好）\n"
            "  1920*1080 → 仍是 1920x1080 档（1080p 源不降）\n"
            "  1280*720  → 掉到 1280x720 档（1080p 源降一档，最省）\n"
            "\n"
            "★★ 关键认知：超分模型的运行速度以**导入（推理）分辨率**为准，\n"
            "   和导出分辨率**无关** —— 所以卡顿时调这里是有效的，改输出尺寸没用。\n"
            "   卡顿就两条路：① 这里降档；② 换更快的模型（或切回 Anime4KCPP\n"
            "   引擎，它是 2x 插件、比 TRT 快得多）。\n"
            "\n"
            "★ 判据永远是**真播丢帧**（不是帧率显示）：\n"
            "  只超分 / 只补帧 各自都稳；1080p 上两者同开稳不住。\n"
            "  ⚠ estimated-vf-fps 照样可能报达标，但 VO 一直在丢帧保同步 ——\n"
            "    只看帧率会误判成「能跑」。\n"
            "  → 1080p 想要稳，**超分和补帧二选一**。\n"
            "  （720p 源两者同开毫无压力）\n"
            "\n"
            "⚠ 这个上限管的是**超分**；管最终输出尺寸的是下面「限定播放分辨率」。")
        # page0 = 预设下拉 / page1 = 自定义数值
        self.srmax_stack = QStackedWidget()
        self.srmax_stack.addWidget(self.cb_srmax)
        _pg_sm = QWidget()
        _hs = QHBoxLayout(_pg_sm)
        _hs.setContentsMargins(0, 0, 0, 0)
        _hs.setSpacing(4)
        self.sp_srmax = QSpinBox()
        self.sp_srmax.setRange(0, 8192)
        self.sp_srmax.setSingleStep(8)
        self.sp_srmax.setSuffix(" px")
        self.sp_srmax.setToolTip(
            "自定义上限的数值 —— 跟**哪条边**比由右边的下拉决定。\n"
            "  0 = 不限（跟源尺寸走）\n"
            "  按短边：1080p 源填 720  → 走 1280x720 档\n"
            "  按长边：1080p 源填 1920 → 仍走 1920x1080 档（按宽度思考更直观）")
        _hs.addWidget(self.sp_srmax)
        self.cb_srmax_edge = QComboBox()
        self.cb_srmax_edge.addItem("按短边", "short")
        self.cb_srmax_edge.addItem("按长边", "long")
        self.cb_srmax_edge.setToolTip(
            "拿哪条边跟上面的数值比：\n"
            "  按短边 → 填 1080 ⇒ 允许 1920x1080（及其以下）档\n"
            "  按长边 → 填 1920 ⇒ 同样允许到 1920x1080 档\n"
            "\n"
            "两种填法表达同一件事 —— 挑一个你算得出来的。\n"
            "按长边的好处：想「限成 1920 宽」时直接填 1920，不用换算成短边 1080。")
        _hs.addWidget(QLabel("按"))
        _hs.addWidget(self.cb_srmax_edge)
        self.srmax_stack.addWidget(_pg_sm)

        self.rb_srmax_preset = QRadioButton("预设")
        self.rb_srmax_custom = QRadioButton("自定义")
        self.rb_srmax_preset.setChecked(True)
        self.rb_srmax_preset.setToolTip(
            "用现成档位选（不限 / 1920*1080 / 1280*720）。\n"
            "标签本身就是档位名，判据边固定按短边，不用管。")
        self.rb_srmax_custom.setToolTip(
            "自己填数值，并指定按短边还是长边判。\n"
            "想「限成 1920 宽」就直接填 1920 + 选按长边。")
        self.w_srmax = QWidget()
        _hsm = QHBoxLayout(self.w_srmax)
        _hsm.setContentsMargins(0, 0, 0, 0)
        _hsm.setSpacing(6)
        _hsm.addWidget(self.rb_srmax_preset)
        _hsm.addWidget(self.rb_srmax_custom)
        _hsm.addWidget(self.srmax_stack)
        gu.addWidget(self.lb_srmax, 3, 0)
        gu.addWidget(self.w_srmax, 3, 1, 1, 2)
        # ★ 限定播放分辨率（LIVE_PLAY_MAX_H）：最终输出（也是补帧的工作分辨率）
        #   的上限。★ 为什么需要它：4x 模型（animevideov3）在 720p 源上会出
        #   5120x2880，比屏幕还大 —— 白白让补帧和输出搬运都放大 4 倍。
        #   限住之后超分出来的大图会先缩到这个尺寸，**补帧就在这个尺寸上做**。
        #
        #   ★ 2026-09-30 改成**单选二选一**（用户要求）：
        #     ○ 预设 —— 用现成档位（不限/4K/2K/1080p/720p），判据固定「短边」
        #     ○ 自定义 —— 自己填数值 + 指定按短边/长边判
        #   两者共用同一行（radio 组 + QStackedWidget），**不能新增行** ——
        #   参数区高度是按像素预算的，多一行就出滚动条（见 _t_a4k_ui 的布局断言）。
        self.lb_play_h = QLabel("限定播放分辨率")
        self.cap_stack = QStackedWidget()          # page0=预设下拉 / page1=自定义

        self.cb_play_h = QComboBox()
        self.cb_play_h.addItem("不限（跟源尺寸走）", 0)
        self.cb_play_h.addItem("4K（2160）", 2160)
        self.cb_play_h.addItem("2K（1440）", 1440)
        self.cb_play_h.addItem("1080p（1080）", 1080)
        self.cb_play_h.addItem("720p（720）", 720)
        self.cb_play_h.setToolTip(
            "最终**播放（输出）分辨率**的上限，同时决定**补帧在哪一档做**。\n\n"
            "★ 为什么需要它：4x 模型会把 720p 源超成 5120x2880，比屏幕还大 ——\n"
            "  多出来的像素全都要过补帧和输出搬运，纯属白烧。\n"
            "  限住之后：超分照旧按档位推理 → 缩到上限尺寸 → **补帧在这个尺寸做**。\n\n"
            "  不限    → 输出 = 源尺寸 × 输出倍率（超分完多大就多大）\n"
            "  1440    → 短边不超过 1440（2560x1440 屏就选这个）\n"
            "  1080    → 短边不超过 1080\n"
            "  720     → 短边不超过 720\n\n"
            "⚠ 只管**上限**：源比它小就按源的来，绝不放大。\n"
            "⚠ 会和「输出倍率」取更小的那个 —— 例如 720p 源 + 倍率 ×4 + 限 1080，\n"
            "  最终是 1920x1080 而不是 2560x1440。\n"
            "⚠ 预设档位一律按**短边**判。想按长边判（如「限成 1920 宽」），\n"
            "  切到右边的「自定义」自己填。\n\n"
            "★★ **只补帧（超分关）时它是唯一的尺寸控制**，而且收益最大：\n"
            "  4K 源直接在 4K 补帧供不上 48fps（会掉帧），\n"
            "  限到 1080p 后补帧余量非常大。\n"
            "  （2026-09-30 修：此前它在仅补帧时**完全失效**——缩放被错关在\n"
            "   超分分支里，日志仍显示 4K 补帧。）")
        self.cap_stack.addWidget(self.cb_play_h)

        _pg_custom = QWidget()
        _hc = QHBoxLayout(_pg_custom)
        _hc.setContentsMargins(0, 0, 0, 0)
        _hc.setSpacing(4)
        self.sp_cap = QSpinBox()
        self.sp_cap.setRange(0, 8192)
        self.sp_cap.setSingleStep(2)         # YUV420 只接受偶数，步长直接给 2
        self.sp_cap.setSuffix(" px")
        self.sp_cap.setToolTip(
            "自定义上限的数值。\n"
            "  0 = 不限（跟源尺寸走）\n"
            "  ⚠ 建议填偶数：YUV420 要求宽高都为偶数，奇数会被进位到下一个偶数。")
        self.cb_cap_edge = QComboBox()
        self.cb_cap_edge.addItem("按短边", "short")
        self.cb_cap_edge.addItem("按长边", "long")
        self.cb_cap_edge.setToolTip(
            "拿哪条边跟上面的数值比：\n"
            "  按短边 → 3840x2160 限 1080 ⇒ 1920x1080（同预设档位的语义）\n"
            "  按长边 → 3840x2160 限 1920 ⇒ 1920x1080（「我想限成 1920 宽」）\n"
            "  按长边 → 3840x2160 限 2560 ⇒ 2560x1440\n\n"
            "⚠ 效果一样是**等比缩放**，只是「拿哪条边当判据」不同。")
        _hc.addWidget(self.sp_cap)
        _hc.addWidget(QLabel("按"))
        _hc.addWidget(self.cb_cap_edge)
        self.cap_stack.addWidget(_pg_custom)

        self.rb_cap_preset = QRadioButton("预设")
        self.rb_cap_custom = QRadioButton("自定义")
        self.rb_cap_preset.setChecked(True)
        self.rb_cap_preset.setToolTip("用现成档位选（不限 / 4K / 2K / 1080p / 720p），按短边判")
        self.rb_cap_custom.setToolTip("自己填数值，并指定按短边还是长边判")
        self.w_cap = QWidget()
        _hcap = QHBoxLayout(self.w_cap)
        _hcap.setContentsMargins(0, 0, 0, 0)
        _hcap.setSpacing(6)
        _hcap.addWidget(self.rb_cap_preset)
        _hcap.addWidget(self.rb_cap_custom)
        _hcap.addWidget(self.cap_stack)
        gu.addWidget(self.lb_play_h, 4, 0)
        gu.addWidget(self.w_cap, 4, 1, 1, 2)
        self.lb_engines = QLabel()
        self.lb_engines.setWordWrap(True)
        self.lb_engines.setStyleSheet("color: #7f7f93;")
        gu.addWidget(self.lb_engines, 5, 0, 1, 3)
        pv.addWidget(g_up)

        # 补帧
        g_ip = QGroupBox("补帧")
        gi = QGridLayout(g_ip)
        gi.setHorizontalSpacing(8)
        gi.setVerticalSpacing(5)
        # 4 列：col0=label, col1=combo, col2=label, col3=combo。
        #   col1/col3 吃掉多余宽度，两个 label 列保持窄。
        gi.setColumnStretch(0, 0)
        gi.setColumnStretch(1, 1)
        gi.setColumnStretch(2, 0)
        gi.setColumnStretch(3, 1)
        self.chk_ip = QCheckBox("启用补帧（提高帧率）")
        gi.addWidget(self.chk_ip, 0, 0, 1, 4)
        self.lb_multi = QLabel("目标帧率")
        # 存的是**目标输出帧率**（不是倍率）；0 = auto（取整数倍率）。
        # ★ 为什么默认 auto：每对输入帧调用 RIFE 的次数 = ⌈倍率⌉−1 ——
        #   24→60（2.5x）要调 2 次，24→48（2x）只调 1 次：多 100% 成本只多 25% 帧。
        #   而且 144Hz 屏上 48fps 是 3:1 整数分频，比 60fps 更顺。
        self.cb_multi = QComboBox()
        # ★ 2026-09-30 改：语义从「绝对 fps 档」改成「**源帧率 × n**」。
        #   旧下拉全是绝对 fps（48/60/72/96/120/144），源一旦不是 24fps 就会
        #   选错档：25fps 源选 48 → 1.92x（非整数，RIFE 要调 2 次还只出 1.92 帧）；
        #   50fps 源选 48 → 倍率 0.96 < 1 → **直接跳过补帧**，等于白选。
        #   新语义：档位直接就是倍率，跟源帧率无关，任何源都不会选错。
        #     倍数用负数编码（-2/-3/-4），和绝对 fps（60/144）在同一 data 位区分。
        #   60 / 144 保留为**绝对目标帧率**（对齐显示器刷新率时才需要）。
        self.cb_multi.addItem("auto（自动倍数：60fps 以内取整）", 0)
        self.cb_multi.addItem("源 ×2（最省：每帧插 1 张）", -2)
        self.cb_multi.addItem("源 ×3（每帧插 2 张）", -3)
        self.cb_multi.addItem("源 ×4（每帧插 3 张）", -4)
        for fps in (60, 144):
            self.cb_multi.addItem(f"{fps} fps（绝对目标）", fps)
        gi.addWidget(self.lb_multi, 1, 0)
        gi.addWidget(self.cb_multi, 1, 1, 1, 3)
        # ★ 补帧**引擎**（2026-09-30 加）—— 换算法，不只是换模型版本。
        #   这几条路差别很大，尤其"占不占显存"决定了 1080p 能不能全都要。
        #   ★ 2026-09-30 删除 vs-rife：TRT 路径本机编译必失败，非 TRT 只是把
        #     RIFE 换个壳重跑且更慢、画质没变好 —— 没有任何独有能力，不能用的
        #     东西留在下拉里只会让人误选。live.vpy 里那条实现也一并删了。
        self.lb_vfi = QLabel("补帧引擎")
        self.cb_vfi = QComboBox()
        for lbl, key in (
            ("RIFE（vsmlrt/TensorRT）", "rife"),
            ("mvtools（纯 CPU，不占显存）", "mv"),
            ("帧复制（零成本零插件，顿）", "dup"),
            ("帧混合（零成本零插件，重影）", "blend"),
        ):
            self.cb_vfi.addItem(lbl, key)
        self.cb_vfi.setToolTip(
            "换的是**算法引擎**，不是模型版本。\n"
            "\n"
            "RIFE（vsmlrt/TensorRT）—— 默认\n"
            "  GPU 推理。⚠ 缺点：占显存，且**平移镜头下线条会抖/出伪影**。\n"
            "\n"
            "mvtools（传统光流，纯 CPU）\n"
            "  ★ 零显存占用，成本全在 CPU。\n"
            "  ★ 它存在的理由：1080p「超分 + 补帧」同开时 GPU 算力不够，\n"
            "    把补帧挪到 CPU 就**两边都要**。\n"
            "  ⚠ 画质弱：块匹配光流，遮挡/大位移处有块状撕裂，明显不如 AI。\n"
            "\n"
            "帧复制（dup）—— 零成本下限\n"
            "  每帧原地重复一次，**不做任何插值**。零推理、零显存、零插件。\n"
            "  画面 = 24fps 的观感塞进 48fps 容器，运动是**顿挫**的（俗称「假\n"
            "  48fps」），只用于排障/对比。\n"
            "  ⚠ 只支持整数倍率；非整数会**跳过补帧**并警告。\n"
            "\n"
            "帧混合（blend）—— 零成本重影\n"
            "  相邻帧各取 50% 线性混合成一帧中间帧。比 dup 略顺滑，\n"
            "  但运动物体**处处有半透明重影**（拖尾）。\n"
            "  ⚠ 目前只实现了 2x；其它倍率会跳过补帧并警告。")
        gi.addWidget(self.lb_vfi, 2, 0)
        gi.addWidget(self.cb_vfi, 2, 1, 1, 3)
        # mv 档位 + mv 线程数：**同一行并排**（2026-09-30 加线程数后为了不让
        #   参数区多出一行高度而合并）。两列各占 label+combo，见 addWidget 的列位。
        self.lb_mv = QLabel("mv 档位")
        self.cb_mv = QComboBox()
        for lbl, key in (
            ("fast（pel=1 blk=32 无重叠，最省）", "fast"),
            ("mid（pel=1 blk=16 ov=4）", "mid"),
            ("best（pel=2 blk=16 ov=8，最干净）", "best"),
        ):
            self.cb_mv.addItem(lbl, key)
        self.cb_mv.setToolTip(
            "mvtools 的 pel / blocksize / overlap 组合：\n"
            "  fast  pel=1 blk=32 ov=0  ← 默认，最省\n"
            "  mid   pel=1 blk=16 ov=4\n"
            "  best  pel=2 blk=16 ov=8  ← 最干净，也最慢\n\n"
            "pel=2（亚像素）明显更准但贵很多；overlap>0 能消块边界但同样变慢。\n"
            "本机实测（1280x720 ×2 整链 ms/出帧）：fast 1.59 / mid 5.91 / best 11.73。\n\n"
            "★ 源宽 ≥2560（4K）时自动把 blksize 翻倍（≤64）：\n"
            "  4K 仅补帧 不开自适应会掉帧，开了就不掉。\n"
            "  64px 大块向量场更平滑，动画上通常更干净；不想要就设 LIVE_MV_HD=0。\n"
            "  ⚠ 旧的「关色度」加速已撤销（mvtools 的 chroma=False 是把色度\n"
            "     平面整个填 0 → 全屏变绿），现在只加倍块尺寸。")
        # ★ mv 线程数（2026-09-30 加）：mvtools 内部线程池的线程数（`opt` 参数）。
        #   这是 mv 补帧**唯一有效**的加速旋钮 —— core.num_threads 对它无效
        #   （实测 1→32 只差 18%），详见 live.vpy 顶部 MV_OPT 的实测表。
        self.lb_mvopt = QLabel("mv 线程数")
        self.cb_mvopt = QComboBox()
        for lbl, key in (
            ("4（推荐，比库默认快 35%）", "4"),
            ("库默认 -1（自动，偏保守）", "-1"),
            ("1 / 2（降压用）", "1"),
            ("8 / 12（吃满物理核）", "8"),
            ("0（关内部线程，最慢）", "0"),
        ):
            self.cb_mvopt.addItem(lbl, key)
        self.cb_mvopt.setToolTip(
            "mvtools 内部线程数（pinterf 版 `opt` 参数）。\n\n"
            "★ 这是 mv 补帧唯一有效的加速旋钮。库默认 -1（自动）偏保守：\n"
            "    仅 Analyse 一级就能快三成左右\n"
            "    但整链会被串行的 BlockFPS 摊薄，收益明显变小\n\n"
            "⚠ 别去调 live.vpy 的 LIVE_VS_THREADS：那是 VS 图级线程，对 mv 无效\n"
            "  （mvtools 自开线程池，实测 1→32 线程总耗时只差 18%）。\n\n"
            "⚠ 超过 ~12 就无收益甚至回退（本机物理核只有 12 个）。\n"
            "⚠ mv 补帧实测并不慢：1080p 有 5.3 倍实时余量。CPU 占用其实不高\n"
            "  （真播全链 ~3.7 核），瓶颈是单帧延迟而非吞吐——这也是「并发帧」\n"
            "  库存能抗抖动的原因。")
        # row 3：左半「mv 档位 + 下拉」，右半「mv 线程数 + 下拉」（4 列并排）
        gi.addWidget(self.lb_mv, 3, 0)
        gi.addWidget(self.cb_mv, 3, 1)
        gi.addWidget(self.lb_mvopt, 3, 2)
        gi.addWidget(self.cb_mvopt, 3, 3)
        # 补帧模型（★ 2026-09-30 加）。只列 rife/ 里的 11 通道版：
        #   7 通道（rife_v2/）在本机 TRT 11 上解析失败（混合精度 /Mul），实测全灭。
        self.lb_rifev = QLabel("补帧模型")
        self.cb_rifev = QComboBox()
        _rife_ok = [(k, d) for k, d, _ms in RIFE_MODELS
                    if (RIFE_DIR / f"rife_v{k}.onnx").is_file()]
        if not _rife_ok:
            self.cb_rifev.addItem("4.26", "4.26")
        for k, d in _rife_ok:
            self.cb_rifev.addItem(d, k)
        self.cb_rifev.setToolTip(
            "补帧模型（只列 models/rife/ 里真的存在的权重）。\n"
            "\n"
            "★ 版本之间画质差异很小 —— 跑不动时换模型不是解法，"
            "该换的是「补帧引擎」或降低目标帧率。\n"
            "\n"
            "各档定位：\n"
            "  4.26  默认。完整网络，不做 tile 分块\n"
            "  4.25 lite  **tile 分块的轻量版**：更省显存、更快一些，\n"
            "    代价是输入尺寸要凑 **128 的倍数**\n"
            "    （pad 的那几行黑边插完会裁掉，不影响画面）。\n"
            "\n"
            "★ 4.26 已经是 RIFE 的**末代版本**（作者 2024-09 后转去做 LLM，\n"
            "  2025 年明确说不再更了）—— 网上说的「4.6」是更老的版本，\n"
            "  不是更新的。别被版本号大小骗到。\n"
            "\n"
            "⚠ 各档相对快慢**跟源分辨率、机器都有关** —— 这里不给耗时数字，\n"
            "  以你自己实跑为准。")
        gi.addWidget(self.lb_rifev, 4, 0)
        gi.addWidget(self.cb_rifev, 4, 1, 1, 3)
        # 后端不给选项，固定 TensorRT：ncnn 跑不了 RIFE（依赖 GridSample，
        # ncnn 会报 `GridSample not supported yet!`），给了选项只会让人选完才发现是废的。
        self.lb_backend = QLabel("RIFE 后端固定 TensorRT；mvtools 跑 CPU，不用后端")
        self.lb_backend.setStyleSheet("color: #7f7f93;")
        gi.addWidget(self.lb_backend, 5, 0, 1, 4)
        pv.addWidget(g_ip)

        # 处理顺序
        g_or = QGroupBox("处理顺序")
        go = QGridLayout(g_or)
        go.setHorizontalSpacing(8)
        go.setVerticalSpacing(5)
        self.cb_order = QComboBox()
        self.cb_order.addItem("auto（按源尺寸自动选）", "auto")
        self.cb_order.addItem("hr —— 先超分后补帧", "hr")
        self.cb_order.addItem("lr —— 先补帧后超分", "lr")
        go.addWidget(self.cb_order, 0, 0, 1, 3)
        # ★ 2026-09-30：3 行 wordWrap 提示改成 tooltip —— 它在 520px 侧栏里
        #   实占 ~60px（134px 组高 vs 120px hint），是参数区唯一超预算的元凶。
        #   信息不删，只是挪进悬停。
        self.cb_order.setToolTip(
            "RIFE 对尺寸很敏感：分辨率越大，每对输入帧的开销涨得越快（超线性）。\n"
            "所以 720p+ 源先在源尺寸补帧、再超分，快 1.65 倍。\n\n"
            "⚠ lr 必须配 realesr-animevideov3_re2x；配 animevideov3 反而更慢。")
        pv.addWidget(g_or)

        # 高级
        g_ad = QGroupBox("高级")
        ga = QGridLayout(g_ad)
        ga.setHorizontalSpacing(8)
        ga.setVerticalSpacing(5)
        self.lb_dev = QLabel("GPU")
        self.sp_dev = QSpinBox()
        self.sp_dev.setRange(0, 7)
        ga.addWidget(self.lb_dev, 0, 0)
        ga.addWidget(self.sp_dev, 0, 1)
        self.lb_streams = QLabel("并发流")
        self.sp_streams = QSpinBox()
        self.sp_streams.setRange(1, 4)
        self.sp_streams.setToolTip(
            "TRT 推理并发流。默认给 2：多流能让 TRT 推理更饱和。\n"
            "（官方基准同样是双流明显优于单流。）")
        self.sp_streams.setMaximum(4)
        ga.addWidget(self.lb_streams, 1, 0)
        ga.addWidget(self.sp_streams, 1, 1)
        self.lb_matrix = QLabel("色彩矩阵")
        self.cb_matrix = QComboBox()
        self.cb_matrix.addItem("BT.709（动漫基本都是）", "709")
        # ★ 值必须是 zimg 认的**全名**（"470bg"），不能写 "601" ——
        #   写 "601" 时 zimg 报 `bad value: matrix_in_s`，整条滤镜被禁用
        #   （2026-09-30 实测：用户选它 → 超分/补帧全失效，看起来像"没生效"）。
        #   live.vpy 那边也做了别名映射兜底，但界面这里就不该给非法值。
        self.cb_matrix.addItem("BT.601（老 SD 素材）", "470bg")
        ga.addWidget(self.lb_matrix, 2, 0)
        ga.addWidget(self.cb_matrix, 2, 1)
        self.lb_conc = QLabel("并发帧")
        self.sp_conc = QSpinBox()
        self.sp_conc.setRange(1, 24)
        self.sp_conc.setToolTip(
            "concurrent-frames：滤镜链一次预取/并行算几帧。\n"
            "★ 双重身份（2026-09-30 实测确认）：\n"
            "  ① **音画不同步的头号开关**——实测 720p LR+re2x+补帧48：\n"
            "    有音轨、真实音频时钟下 cf=3 每秒丢 8 帧（画面追不上声音），\n"
            "    cf=6 起丢帧归零。\n"
            "  ② **抗抖动的帧库存**——mpv 会提前让 VS 渲染这么多帧备着，\n"
            "    播放中某帧偶发变慢（p99 抖动）时先吃库存不卡画面。\n"
            "    4K 补帧建议 12+（@48fps = 250ms 库存）；4K 单帧 YUV 12MB，\n"
            "    24 帧 ≈ 300MB 内存，可接受。\n"
            "⚠ 库存只存在于**播放中**：mpv 是按需拉取模型，暂停时 VO 不要帧、\n"
            "  滤镜链即停（实测 CPU 3.7→0.0 核），**没有「暂停继续攒帧」**。\n"
            "  想要预热效果只能用慢放（speed=0.05，链路持续跑）。\n"
            "⚠ 只有**带音轨**的片子才暴露 ① 这件事：无音轨时 mpv 会放慢视频去\n"
            "  迁就滤镜（time-pos 只走 0.66×），看着「0 丢帧」，其实是在慢放。")
        ga.addWidget(self.lb_conc, 3, 0)
        ga.addWidget(self.sp_conc, 3, 1)
        self.lb_static = QLabel("相似帧阈值")
        self.sp_static = QDoubleSpinBox()
        self.sp_static.setRange(0.0, 0.05)
        self.sp_static.setDecimals(4)
        self.sp_static.setSingleStep(0.001)
        self.sp_static.setToolTip(
            "0 = 关闭。与上一帧的平均绝对差低于它就**跳过 RIFE**、直接用源帧。\n"
            "实拍/番剧里大量静止帧，收益很大；阈值太松会把慢镜头也跳掉（会顿）。\n"
            "播放时日志会打「相似帧：x/y（z%）跳过推理」，按这个调。")
        ga.addWidget(self.lb_static, 4, 0)
        ga.addWidget(self.sp_static, 4, 1)
        self.chk_verbose = QCheckBox("打印 [live] 链路日志")
        ga.addWidget(self.chk_verbose, 5, 0, 1, 2)
        self.chk_mpvlog = QCheckBox("显示 mpv 详细日志")
        self.chk_mpvlog.setToolTip("加 --msg-level=all=info，排查解码/渲染问题时用")
        ga.addWidget(self.chk_mpvlog, 6, 0, 1, 2)
        # ★ mpv 程序路径：GUI 里可换成任意 mpv 构建（如 mpv-lazy 的 mpv.exe）。
        #   留空 = 用自带的 mpv/mpv.exe（resolve_mpv 回落）。
        self.le_mpv = QLineEdit()
        self.le_mpv.setPlaceholderText(f"默认：{MPV}（留空则用自带 mpv）")
        self.le_mpv.setToolTip(
            "指定要用的 mpv 程序。\n"
            "留空 → 用本项目自带的 mpv/mpv.exe。\n"
            "填了且文件存在 → 用那个（比如 D:\\mpv-lazy\\mpv.exe）。\n"
            "可点右侧「浏览…」选，或手动粘贴路径。")
        self.btn_mpv_br = QPushButton("浏览…")
        self.btn_mpv_br.clicked.connect(self._browse_mpv)
        _mpv_row = QWidget()
        _mpv_hl = QHBoxLayout(_mpv_row)
        _mpv_hl.setContentsMargins(0, 0, 0, 0)
        _mpv_hl.addWidget(self.le_mpv, 1)
        _mpv_hl.addWidget(self.btn_mpv_br)
        ga.addWidget(QLabel("mpv 程序"), 8, 0)
        ga.addWidget(_mpv_row, 8, 1)
        self.chk_osd = QCheckBox("画面上显示统计")
        self.chk_osd.setToolTip(
            "把「实时 / 输出 / 实际显示 fps · 丢帧 · 输出分辨率 · 刷新率」用 mpv 的\n"
            "OSD 打在画面左上角小字（和 mpv 自己的时间/进度行分两行，互不覆盖）。\n"
            "全屏播放时不用切回这个窗口看。实现：--osd-level=3 + 每秒一次 show-text。")
        ga.addWidget(self.chk_osd, 7, 0, 1, 2)
        pv.addWidget(g_ad)

        # ⚠ 这里**不要** addStretch —— 它会把 host 的 sizeHint 撑到视口高度，
        #   反而让 QScrollArea 认为"内容装得下"从而不给滚动条余量（表现为
        #   参数区被硬压、控件重叠）。让 host 的高度等于内容真实高度即可。
        scroll.setWidget(host)
        sv.addWidget(scroll, 1)

        # ── 钉在底部的：预估卡 + 主按钮（不进滚动区）──────────
        self.lb_plan = QLabel()
        self.lb_plan.setObjectName("plan")
        self.lb_plan.setWordWrap(True)
        self.lb_plan.setMinimumHeight(48)
        self.lb_plan.setStyleSheet(
            "background: #202027; border: 1px solid #33333c; border-radius: 8px;"
            "padding: 8px 10px; color: #b9b9c8;")
        sv.addWidget(self.lb_plan)

        run = QGroupBox("播放")
        run.setMinimumHeight(196)      # 按钮不许被压扁，否则找不到「开始播放」
        rv = QGridLayout(run)
        rv.setSpacing(7)
        self.btn_run = QPushButton("▶  开始播放")
        self.btn_run.setObjectName("primary")
        self.btn_run.setMinimumHeight(38)
        self.btn_run.clicked.connect(self._start)
        # ★ 2026-10-01 加：全屏开关（用户要的「开始播放旁一个全屏小勾」）。
        #   勾上 → mpv 带 `--fullscreen` 启动；不勾 → 按**视频自身分辨率**开窗
        #   （mpv 默认行为，也是这个勾出现之前的行为）→ 所以默认**不勾**，
        #   保证升级后行为不变，想要全屏的人自己勾一次（会存进 ui_config.json）。
        self.chk_fs = QCheckBox("全屏")
        self.chk_fs.setToolTip(
            "勾上：播放时直接全屏。\n"
            "不勾：按视频自身分辨率开窗（1080p 源 → 1920x1080 的窗口）。\n\n"
            "进全屏后按 f 切回窗口，Esc 退出全屏（mpv 自带快捷键）。")
        self.chk_fs.setChecked(False)
        self.btn_stop = QPushButton("■  停止")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setMinimumHeight(38)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        # 按钮占满左侧、勾选贴右侧（col1 不拉伸，否则勾会被挤到屏幕外）
        rv.setColumnStretch(0, 1)
        rv.setColumnStretch(1, 0)
        rv.addWidget(self.btn_run, 0, 0)
        rv.addWidget(self.chk_fs, 0, 1)
        rv.addWidget(self.btn_stop, 1, 0, 1, 2)

        self.btn_selfcheck = QPushButton("环境自检")
        self.btn_selfcheck.setToolTip("跑一遍 selfcheck.py：检查 mpv / vsscript / 引擎 / 模型")
        self.btn_selfcheck.clicked.connect(self._selfcheck)
        self.btn_open = QPushButton("打开目录")
        self.btn_open.clicked.connect(
            lambda: subprocess.Popen(["explorer", str(ROOT)]))
        rv.addWidget(self.btn_selfcheck, 2, 0, 1, 2)
        rv.addWidget(self.btn_open, 3, 0, 1, 2)
        self.btn_about = QPushButton("关于")
        self.btn_about.setToolTip("作者 / 开源协议 / 依赖出处")
        self.btn_about.clicked.connect(lambda: show_about(self))
        rv.addWidget(self.btn_about, 4, 0, 1, 2)
        sv.addWidget(run)

        # 接线（只改状态，末尾统一调 _refresh_enabled / _update_plan）
        for w in (self.chk_up, self.chk_ip, self.chk_verbose, self.chk_mpvlog,
                  self.chk_osd, self.chk_fs):
            w.toggled.connect(self._on_toggle)
        self.le_mpv.textChanged.connect(self._on_toggle)
        for w in (self.cb_sr, self.cb_srmax, self.cb_srmax_edge, self.cb_multi,
                  self.cb_order, self.cb_matrix, self.cb_rifev, self.cb_vfi,
                  self.cb_mv, self.cb_play_h, self.cb_cap_edge):
            w.currentIndexChanged.connect(self._on_toggle)
        for w in (self.sp_cap, self.sp_srmax):
            w.valueChanged.connect(self._on_toggle)
        # ★ 预设/自定义二选一要**切换 stack 页**并同步值，所以单独接线
        #   （不能只接 _on_toggle —— 那不会翻页）。
        self.rb_cap_preset.toggled.connect(self._on_cap_mode)
        # ★ 导入分辨率上限的预设/自定义同样要翻 stack 页 → 单独接线
        self.rb_srmax_preset.toggled.connect(self._on_srmax_mode)
        # ★ 切引擎要**整表换掉模型下拉**，所以不能接 _on_toggle（那只是改状态）。
        self.cb_sreng.currentIndexChanged.connect(self._on_sr_engine)
        for w in (self.sp_dev, self.sp_streams, self.sp_conc):
            w.valueChanged.connect(self._on_toggle)
        return side

    # ── 日志面板 ─────────────────────────────────────────────
    def _build_log(self) -> QWidget:
        box = QGroupBox("日志")
        v = QVBoxLayout(box)
        v.setSpacing(6)

        self.lb_live = QLabel("○ 未播放")
        self.lb_live.setTextFormat(Qt.RichText)
        self.lb_live.setWordWrap(True)
        # ★ 2026-09-30 从 74 → 58 → 44：这是**日志区的硬下限来源**
        #   （QSplitter 只能压到子控件的 minimumSizeHint，压不动它分给上半屏）。
        #   它是 wordWrap 的，44px 仍能显示 2 行关键状态 + 省略，
        #   而省下的高度直接变成参数区可用高度（用户要求"日志再压扁一点"）。
        self.lb_live.setMinimumHeight(44)
        self.lb_live.setStyleSheet(
            "background: #202027; border: 1px solid #33333c; border-radius: 8px;"
            "padding: 7px 10px; color: #b9b9c8;")
        v.addWidget(self.lb_live)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)      # ★ 限行，否则长播会越来越卡
        self.log.setFont(QFont("Consolas", 9))
        self.log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v.addWidget(self.log, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton("清空日志")
        b.clicked.connect(self.log.clear)
        row.addWidget(b)
        v.addLayout(row)
        return box

    # ══════════════════════════════════════════════════════ 配置往返

    def _restore_cfg(self) -> None:
        c = self.cfg
        # 旧配置兼容：模型标识改过名（2026-09-29 native2x → realesr-animevideov3_re2x）
        if c.get("sr") == "native2x":
            c["sr"] = "realesr-animevideov3_re2x"
        # ★ 旧的默认 concurrent=3 是坏值：**带音轨**的片子、真实音频时钟下会持续丢帧
        #   （720p LR+re2x+补帧48 每秒丢 8 帧）→ 用户看到的就是"音画不同步、画面慢几秒"。
        #   旧配置里存着 3 的必须抬上来，否则用户改了默认值也白改 —— 配置会盖回去。
        if int(c.get("concurrent") or 0) < 6:
            c["concurrent"] = 6
            self._log("[控制台] 并发帧 3 → 6：实测 3 在**带音轨**的片子上会持续丢帧"
                      "（音画不同步）。\n"
                      "　　　　　显存吃紧可在「高级 → 并发帧」里调回，但会重新丢帧。\n")
        # 旧配置存的是倍率（2/3/4），迁移成目标帧率后回落默认值
        # ★ 2026-09-30 改语义后再迁一次：老的绝对值 48/72/96/120 一律映射到
        #   「源 ×2/×3/×4」这种倍数档（因为旧值本就是按 24fps 源挑的整数倍）。
        if "interp_fps" not in c and "multi" in c:
            c["interp_fps"] = {2: -2, 3: -3, 4: -4}.get(int(c["multi"]), 0)
        _ifp = int(c.get("interp_fps") or 0)
        if _ifp in (48, 72, 96, 120):
            c["interp_fps"] = {48: -2, 72: -3, 96: -4, 120: -5}.get(_ifp, 0)
            self._log(f"[控制台] 补帧档位 {_ifp}fps → 源 ×{abs(c['interp_fps'])}"
                      "：档位语义已从「绝对帧率」改成「源帧率 ×n」，"
                      "老配置按 24fps 源折算，请确认。\n")
        # ★★ 超分引擎：2026-09-30 引入「TRT / Anime4KCPP」两层选择。
        #   老配置只有 `sr` —— 但 `anime4kcpp:xxx` 前缀本身就能推出引擎，
        #   所以这里**不写迁移**，直接看 sr 值：以 `anime4kcpp` 开头 → 引擎选 a4k。
        #   （新加一个 `sr_engine` 键反而会和老配置打架：两份状态要同步。）
        self.cb_sreng.setCurrentIndex(
            1 if (str(c.get("sr") or "").startswith("anime4kcpp")
                  and self.cb_sreng.count() > 1) else 0)
        # ★ 必须在设 cb_sr 之前先按引擎填表 —— 否则 findData 找不到 A4K 的模型项。
        self._fill_sr_models()
        self.chk_up.setChecked(bool(c["upscale"]))
        self.chk_ip.setChecked(bool(c["interp"]))
        self.chk_verbose.setChecked(bool(c["verbose"]))
        self.chk_mpvlog.setChecked(bool(c["mpv_verbose"]))
        self.le_mpv.setText(str(c.get("mpv_path") or ""))
        self.chk_osd.setChecked(bool(c.get("osd_stat", True)))
        # ★ 老配置里没有 fullscreen 这个键 → c.get 兜底 False（保持窗口模式）
        self.chk_fs.setChecked(bool(c.get("fullscreen", False)))
        for cb, key in ((self.cb_sr, "sr"), (self.cb_rifev, "rife_ver"),
                        (self.cb_vfi, "vfi"),
                        (self.cb_mv, "mv_preset"), (self.cb_mvopt, "mv_opt"),
                        (self.cb_order, "order"), (self.cb_matrix, "matrix")):
            i = cb.findData(c.get(key))
            cb.setCurrentIndex(i if i >= 0 else 0)
        for cb, key, conv in ((self.cb_srmax, "sr_max", int),
                              (self.cb_multi, "interp_fps", int)):
            i = cb.findData(conv(c[key]))
            cb.setCurrentIndex(i if i >= 0 else 0)
        # ★ 限定播放分辨率：预设下拉 + 自定义输入两条路都要回填，再按 radio 翻页
        _pmv = int(c.get("play_max_h") or 0)
        i = self.cb_play_h.findData(_pmv)
        self.cb_play_h.setCurrentIndex(i if i >= 0 else 0)
        self.sp_cap.setValue(_pmv)
        i = self.cb_cap_edge.findData(c.get("play_max_edge") or "short")
        self.cb_cap_edge.setCurrentIndex(i if i >= 0 else 0)
        _cust = bool(c.get("play_max_custom"))
        self.rb_cap_custom.setChecked(_cust)
        self.rb_cap_preset.setChecked(not _cust)
        self._on_cap_mode()
        self.sp_static.setValue(float(c.get("static_th", 0.003)))
        self.sp_dev.setValue(int(c["device"]))
        self.sp_streams.setValue(int(c["streams"]))
        self.sp_conc.setValue(int(c["concurrent"]))
        files = [f for f in (c.get("files") or []) if Path(f).is_file()]
        if files:
            self._add_paths(files, silent=True)
        # ★ 2026-10-01：导入分辨率上限改成「预设 / 自定义」二选一。
        #   下拉值的还原走上面那个 conv 循环（findData 找不到就落 0）；
        #   这里补 spinbox 与 radio，并**显式设 stack 页** ——
        #   因为 radio 已经是同一个状态时 setChecked 不发信号，翻页不会发生。
        self.sp_srmax.setValue(int(c.get("sr_max") or 0))
        _sme = str(c.get("sr_max_edge") or "short")
        _smi = self.cb_srmax_edge.findData(_sme)
        self.cb_srmax_edge.setCurrentIndex(_smi if _smi >= 0 else 0)
        _smc = bool(c.get("sr_max_custom", False))
        self.rb_srmax_custom.setChecked(_smc)
        self.rb_srmax_preset.setChecked(not _smc)
        self.srmax_stack.setCurrentIndex(1 if _smc else 0)

    def _collect_cfg(self) -> dict:
        return {
            "upscale": self.chk_up.isChecked(),
            "fullscreen": self.chk_fs.isChecked(),
            "interp": self.chk_ip.isChecked(),
            "verbose": self.chk_verbose.isChecked(),
            "mpv_verbose": self.chk_mpvlog.isChecked(),
            "osd_stat": self.chk_osd.isChecked(),
            "sr": self.cb_sr.currentData(),
            "rife_ver": self.cb_rifev.currentData(),
            "vfi": self.cb_vfi.currentData(),
            "mv_preset": self.cb_mv.currentData(),
            "mv_opt": self.cb_mvopt.currentData(),
            "order": self.cb_order.currentData(),
            "matrix": self.cb_matrix.currentData(),
            "sr_max": self._srmax_value(),
            "sr_max_edge": self._srmax_edge(),
            "sr_max_custom": self.rb_srmax_custom.isChecked(),
            "play_max_h": (int(self.sp_cap.value()) if self.rb_cap_custom.isChecked()
                           else int(self.cb_play_h.currentData() or 0)),
            # ★ 预设档位恒为 "short"（档位名"1080p"说的就是短边）；只有自定义才可能 long
            "play_max_edge": (str(self.cb_cap_edge.currentData())
                              if self.rb_cap_custom.isChecked() else "short"),
            "play_max_custom": self.rb_cap_custom.isChecked(),
            "interp_fps": self.cb_multi.currentData(),
            "static_th": self.sp_static.value(),
            "device": self.sp_dev.value(),
            "streams": self.sp_streams.value(),
            "concurrent": self.sp_conc.value(),
            "mpv_path": self.le_mpv.text().strip(),
            "files": [self.lst_files.item(i).data(Qt.UserRole)
                      for i in range(self.lst_files.count())],
            "last_in_dir": self.cfg.get("last_in_dir", ""),
        }

    # ══════════════════════════════════════════════════════ 状态联动

    def _on_toggle(self, *_) -> None:
        self._refresh_enabled()
        self._update_plan()

    # ── 超分引擎切换：整表换掉模型下拉 ─────────────────────────
    def _fill_sr_models(self) -> None:
        """按当前引擎（TRT / Anime4KCPP）重填「模型」下拉。"""
        eng = self.cb_sreng.currentData()
        keep = self.cb_sr.currentData()
        self.cb_sr.blockSignals(True)
        self.cb_sr.clear()
        if eng == "a4k":
            # ★ 全列出，**不设默认**（用户明确要求）—— 12 个模型全在，按速度排。
            #   ⚠ 显示名里**不带 ms 也不带"官方默认"尾巴**：加了会超过下拉宽度
            #     被省略号截断（2026-09-30 用户反馈"看不清模型名"）。
            #     耗时信息放 tooltip 里（鼠标悬停看），不占显示宽度。
            for i, (m, disp, _ms) in enumerate(A4K_MODELS):
                self.cb_sr.addItem(disp, f"anime4kcpp:{m}")
                # ★ 2026-10-01 用户要求：**不显示本机实测 ms** —— 那是 4080 上的数，
                #   换台机器完全不通用，摆出来只会误导。tooltip 只留定性说明。
                self.cb_sr.setItemData(
                    i, f"{m}\n（Anime4KCPP 插件，YUV 直通零格式转换）",
                    Qt.ToolTipRole)
                if m == keep:
                    self.cb_sr.setCurrentIndex(i)
        else:
            # ★★ 恒列出全部 TRT 模型 —— **不再按"本机有没有 .engine"过滤**。
            #   用户要求：不能指望使用者先去跑 build_*_engines.py。
            #   没引擎时由 live.vpy 的 LIVE_SR_BUILD 按**源尺寸**现场编译
            #   （首次遇该分辨率稍慢，之后缓存秒开）。
            #   引擎状态放 tooltip，不占显示名宽度（否则长名被省略号截断）。
            for i, (key, disp, sc, _d, _pfx) in enumerate(SR_MODELS):
                self.cb_sr.addItem(disp, key)
                _lad = sr_ladder(key)
                if _lad:
                    _tip = (f"LIVE_SR = {key}\n\n已编译档位（{len(_lad)} 个）："
                            + "、".join(f"{w}×{h}" for w, h in _lad))
                else:
                    _tip = (f"LIVE_SR = {key}\n\n本机还没有这个模型的 .engine —— "
                            "播放时会按**源尺寸**现场编译（首次该分辨率稍慢，之后缓存秒开）。")
                self.cb_sr.setItemData(i, _tip, Qt.ToolTipRole)
                if key == keep:
                    self.cb_sr.setCurrentIndex(i)
        self.cb_sr.blockSignals(False)

    def _on_sr_engine(self, *_) -> None:
        self._fill_sr_models()
        self._refresh_enabled()
        self._update_plan()
        self._warm_sr_engine()          # 换模型后也把已导入视频的引擎备好

    # ── 限定播放分辨率：预设 / 自定义 二选一 ─────────────────
    def _on_cap_mode(self, *_) -> None:
        """切 radio：翻 stack 页 + 两路数值双向往回同步，然后刷新置灰与预估。"""
        cust = self.rb_cap_custom.isChecked()
        self.cap_stack.setCurrentIndex(1 if cust else 0)
        if cust:
            # ★ 切到自定义：**只在输入框还是 0（没设过）时**才用预设值当初值。
            #   ⚠ 不能无条件覆盖 —— 那会让"设了自定义 1920 → 切预设看两眼 →
            #     切回来"把 1920 冲掉（2026-09-30 自己写测试时抓到的 UX 缺陷）。
            if int(self.sp_cap.value()) <= 0:
                v = int(self.cb_play_h.currentData() or 0)
                if v > 0:
                    self.sp_cap.setValue(v)
        else:
            # 切回预设：数值正好命中某档就选中它，否则保持原档（不乱跳）
            v = int(self.sp_cap.value())
            i = self.cb_play_h.findData(v)
            if i >= 0:
                self.cb_play_h.setCurrentIndex(i)
        self._refresh_enabled()
        self._update_plan()

    def _cap_value(self) -> tuple[int, str]:
        """当前生效的「限定播放分辨率」——(上限值, 判据边)。供预估与 launch 共用。"""
        if self.rb_cap_custom.isChecked():
            return int(self.sp_cap.value()), str(self.cb_cap_edge.currentData())
        return int(self.cb_play_h.currentData() or 0), "short"

    def _native_scale(self, sr: str | None = None) -> int:
        """算某个超分模型的原生倍率（输出 = 源 × 它；超分关时为 1）。

        ★ 2026-09-30 起没有「输出倍率」旋钮了（和「限定播放分辨率」职责重叠，
          且改不了推理倍率 —— 详见 live.vpy 里 LIVE_OUT_SCALE 的删除说明）。
          输出尺寸一律按**模型原生倍率**算，再由 cap 压。

        ⚠ `sr` 必须传**生效模型**（`_current_plan()` 返回的那个），不能默认读控件 ——
          ORDER=auto 会在 LR 场景**自动换模型**（4x 的 animev3 → 2x 的 re2x），
          这时按控件算会得到 4x，预估卡上的输出尺寸就比真跑的大一倍。
          （2026-09-30 截图时发现：伪造 animev3 生效但控件是 AnimeJaNai，
            预估卡显示 2x 尺寸 —— 正是这个不一致的镜像。）
        """
        if not self.chk_up.isChecked():
            return 1
        cur = sr if sr is not None else self.cb_sr.currentData()
        if is_a4k(cur):
            return 2                       # Anime4KCPP 全系原生 2x
        for key, _disp, sc, _lad, *_rest in SR_MODELS:
            if key == cur:
                return int(sc)
        return 2                           # 兜底（re2x / AnimeJaNai 系都是 2x）

    def _srmax_value(self) -> int:
        """当前生效的「导入分辨率上限」（短边，0 = 不限）。供预估与 launch 共用。"""
        if self.rb_srmax_custom.isChecked():
            return int(self.sp_srmax.value())
        return int(self.cb_srmax.currentData() or 0)

    def _srmax_edge(self) -> str:
        """当前生效的上限判据边。

        预设恒为 `short` —— 预设标签本身就是档位名（`1920*1080`），语义已经明确，
        再让用户选"按哪条边"只会自相矛盾（标签是 WxH，值却是单边）。只有自定义
        才需要判据边（用户填的是**一个数**，得知道它比哪条边）。
        """
        if self.rb_srmax_custom.isChecked():
            return str(self.cb_srmax_edge.currentData())
        return "short"

    def _on_srmax_mode(self, *_) -> None:
        """切 radio：翻 stack 页 + 两路数值双向往回同步，再刷新置灰与预估。

        ⚠ 与 _on_cap_mode 同一个坑：切到自定义时**只在输入框还是 0 时**才填初值，
          否则「设了自定义值 → 切预设看两眼 → 切回来」会被冲掉。
        """
        cust = self.rb_srmax_custom.isChecked()
        self.srmax_stack.setCurrentIndex(1 if cust else 0)
        if cust:
            if int(self.sp_srmax.value()) <= 0:
                v = int(self.cb_srmax.currentData() or 0)
                if v > 0:
                    self.sp_srmax.setValue(v)
        else:
            v = int(self.sp_srmax.value())
            i = self.cb_srmax.findData(v)
            if i >= 0:
                self.cb_srmax.setCurrentIndex(i)
        self._refresh_enabled()
        self._update_plan()

    def _refresh_enabled(self) -> None:
        """启用态**只在这里裁决** —— 分散到多个 _on_* 里一定会互相打架。"""
        up = self.chk_up.isChecked()
        ip = self.chk_ip.isChecked()
        _a4k = self.cb_sreng.currentData() == "a4k"
        for w in (self.cb_sr, self.lb_sr,
                  self.lb_engines, self.cb_sreng, self.lb_sreng):
            w.setEnabled(up)
        # ★ 「导入分辨率上限」是 **TRT 专有**的（它管的是 pick_ladder 选哪个档）：
        #   Anime4KCPP 没有档位概念，factor 直接按需求算 → 置灰而不是留着误导。
        #   ⚠ 置灰 w_srmax 容器即可（radio + 当前 stack 页会一起禁用）。
        for w in (self.w_srmax, self.lb_srmax):
            w.setEnabled(up and not _a4k)
        # ★ 「限定播放分辨率」是**输出/补帧工作尺寸**的上限，超分或补帧任一开着
        #   都说得通（不超分时限一个尺寸也同样有用），所以判据是 ip or up。
        #   ⚠ 置灰 w_cap 容器即可（radio + 当前 stack 页会一起禁用）。
        for w in (self.w_cap, self.lb_play_h):
            w.setEnabled(ip or up)
        # 下面这组是**补帧专属**的：目标帧率/相似帧阈值/引擎，只有补帧才谈得上。
        for w in (self.cb_multi, self.lb_multi,
                  self.sp_static, self.lb_static, self.cb_vfi, self.lb_vfi):
            w.setEnabled(ip)
        # 引擎各自的专属控件：只有选中对应引擎时才可编辑（置灰而非隐藏，
        #   藏起来会让面板跳来跳去）。dup/blend 没有任何专属控件 → 全置灰。
        _vfi = self.cb_vfi.currentData()
        for w in (self.cb_rifev, self.lb_rifev):
            # 补帧模型下拉只对 rife 有效：mvtools 跑传统光流、dup/blend 不做推理
            w.setEnabled(ip and _vfi == "rife")
        for w in (self.cb_mv, self.lb_mv, self.cb_mvopt, self.lb_mvopt):
            w.setEnabled(ip and _vfi == "mv")
        self.cb_rifev.setToolTip(
            ("mvtools 引擎不用 RIFE 模型权重（它跑的是传统块匹配光流）。"
             if _vfi == "mv" else
             f"{_vfi} 引擎不做任何推理，没有模型权重可选。"
             if _vfi in ("dup", "blend") else
             "补帧模型（只列 models/rife/ 里真的存在的权重）。\n\n"
             "★ 版本之间画质差异很小 —— 跑不动时换模型不是解法，"
             "该换的是「补帧引擎」或降低目标帧率。"))
        self.lb_backend.setText(
            "mvtools 跑在 CPU 上，不需要推理后端" if _vfi == "mv"
            else "帧复制不做推理，没有后端（纯 std 滤镜）" if _vfi == "dup"
            else "帧混合不做推理，没有后端（纯 std 滤镜）" if _vfi == "blend"
            else "RIFE 后端固定 TensorRT；mvtools 跑 CPU，不用后端")
        self.btn_run.setEnabled(self.lst_files.count() > 0
                                and self.proc.state() == QProcess.NotRunning)
        self.btn_selfcheck.setEnabled(True)
        self.btn_open.setEnabled(True)
        # 引擎缓存状态：只报「当前这片要用哪档、在不在」，比铺 7 个档位有用
        sr = self.cb_sr.currentData() or "animev3"
        it0 = self.lst_files.currentItem()
        if it0 is None and self.lst_files.count():
            it0 = self.lst_files.item(0)
        info = self.media.get(it0.data(Qt.UserRole)) if it0 else None
        # ★★ Anime4KCPP 路线：没有 .engine 文件、没有档位、不需要预编译
        #   → 这个标签改报「模型 + 实时耗时估计」，别去扫盘（会报"一个引擎都没有"）。
        if is_a4k(sr):
            _am = str(sr).split(":", 1)[1] if ":" in str(sr) else "acnet-gan"
            _ms = next((m for k, _, m in A4K_MODELS if k == _am), 0.0)
            # 按**档位像素数**折算耗时：实测 720p(2560x1440 出图) 基准
            _px = (info[0] * 2) * (info[1] * 2) if info else 0
            _est = _ms * (_px / (2560 * 1440)) if _px else _ms
            # ★ 2026-09-30 用户要求：删掉状态/提示类文本，正常情况**留空**。
            #   （原「Anime4KCPP 插件（无需预编译引擎）模型 xxx 本片约 N ms/帧」）
            self.lb_engines.setText("")
            return
        row = next((r for r in SR_MODELS if r[0] == sr), None)
        if row is not None:
            _, _, _, edir, prefix = row
            ladder = sr_ladder(sr)
            # ★ 引擎文件名有**两种**：`_fp16_`（fp32 I/O，默认那批）和
            #   `_fp16io_`（fp16 I/O，AnimeJaNai 天生就是）。live.vpy 的
            #   stage_upscale() 会先试 fp16io、没有再退回 fp16 —— 这里必须
            #   同样**两种都算已存在**，否则界面会误报「缺 7 档，首次要现编 ~25s」，
            #   而实际引擎早就编好了（2026-09-30 实测：换成正确判据前，
            #   animev3 明明 7 档全在，界面却报缺 7 档）。
            def _eng_name(t):
                a = edir / f"{prefix}fp16io_{t[0]}x{t[1]}.engine"
                if a.is_file():
                    return a
                return edir / f"{prefix}fp16_{t[0]}x{t[1]}.engine"
        else:                                     # 老兜底
            ladder = LADDER_RE2X if sr == "realesr-animevideov3_re2x" else LADDER_ANIMEV3
            _pfx = ("realesr-animevideov3_re2x" if sr == "realesr-animevideov3_re2x"
                    else "realesr-animevideov3")
            def _eng_name(t, _p=_pfx):
                a = ENGINE_DIR / f"{_p}_fp16io_{t[0]}x{t[1]}.engine"
                if a.is_file():
                    return a
                return ENGINE_DIR / f"{_p}_fp16_{t[0]}x{t[1]}.engine"
        miss = [t for t in ladder if not _eng_name(t).is_file()]
        if not ladder:
            self.lb_engines.setText(
                '<span style="color:#e07a7a">该模型一个引擎都没有 —— '
                "先跑 build_aj_engines.py / build_fp16io_engines.py</span>")
        elif miss:
            self.lb_engines.setText(
                f'<span style="color:#e0c06a">缺 {len(miss)} 档：'
                + "、".join(f"{a}×{b}" for a, b in miss)
                + "</span>（首次用到要现编，约 25 秒）")
        else:
            # ★ 2026-09-30 用户要求：删掉「本片走 NxM 档 … 已缓存，秒开」这类
            #   状态提示 —— 正常情况**留空**。这行现在只在"缺引擎/缺档"时才说话
            #   （那是防御性警告：缺档会在播放时现编、卡 25 秒）。
            self.lb_engines.setText("")

    # ══════════════════════════════════════════════════════ 预估

    def _current_plan(self):
        """返回 (eff_order, eff_sr, ms, tag, w, h, note, base_fps)。"""
        it = self.lst_files.currentItem()
        if it is None and self.lst_files.count():
            it = self.lst_files.item(0)
        up = self.chk_up.isChecked()
        ip = self.chk_ip.isChecked()
        order = self.cb_order.currentData() or "auto"
        sr = self.cb_sr.currentData() or "animev3"
        if it is None:
            return order, sr, None, "", 0, 0, "", 24.0
        path = it.data(Qt.UserRole)
        info = self.media.get(path)
        if not info:
            return order, sr, None, "分辨率未知", 0, 0, "", 24.0
        w, h, base_fps = info
        eff_order, eff_sr, note = order, sr, ""
        if order == "auto":
            # 与 live.vpy 的 auto 逐条对齐：源宽 ≥1280 或**目标帧率 ≥60fps**
            # （后者是实测结论：只有 LR + re2x 跑得满 60）
            tgt_fps = resolve_target_fps(self.cb_multi.currentData(),
                                         base_fps)
            need_lr = w >= 1280 or (ip and tgt_fps >= 60)
            eff_order = "lr" if (up and ip and need_lr) else "hr"
            # ★ 2026-09-30 更新：LR 时"顺手换模型"只对 animev3（4x）做。
            #   AnimeJaNai 是 2x 且实测比 re2x 快 2~3 倍（1920x1080 档 26.9 vs 61.5 ms），
            #   再把它换成 re2x 是帮倒忙。用户显式选了 AJ 系就尊重它。
            # ★★ Anime4KCPP 更不该换：它压根不是 TRT 引擎，换了就整条路都变了。
            if is_a4k(eff_sr):
                note = "LR + Anime4KCPP（YUV 直通，补帧在源尺寸做，无需换模型）"
            elif (eff_order == "lr"
                  and eff_sr in ("animev3", "realesr-animevideov3_re2x")):
                if eff_sr != "realesr-animevideov3_re2x":
                    eff_sr = "realesr-animevideov3_re2x"
                    note = "auto 顺手把模型换成了 realesr-animevideov3_re2x"
            elif eff_order == "lr" and eff_sr.startswith("AnimeJaNai"):
                note = "LR + AnimeJaNai（2x 模型，不用换 re2x）"
        elif order == "lr" and up and ip and eff_sr != "realesr-animevideov3_re2x":
            note = "⚠ lr + animevideov3 实测比 hr 更慢，别选 lr"
        srmax = self._srmax_value()
        sedge = self._srmax_edge()
        ms, tag = estimate(eff_order, eff_sr, up, ip, w, h, srmax, sedge)
        return eff_order, eff_sr, ms, tag, w, h, note, base_fps

    def _update_plan(self) -> None:
        order, sr, ms, tag, w, h, note, base_fps = self._current_plan()
        up = self.chk_up.isChecked()
        ip = self.chk_ip.isChecked()
        if not self.lst_files.count():
            self.lb_plan.setText(
                '<span style="color:#8a8a9c">队列是空的 —— 把视频拖进来，'
                '这里会提前算出它会用哪套配置、大概多少 fps。'
                '（分辨率用 ffmpeg 读容器头，不解码，很快）</span>')
            return
        if ms is None:
            self.lb_plan.setText(
                f'<span style="color:#c9a227">{tag or "无法预估"}</span>'
                '<br><span style="color:#8a8a9c">读不到分辨率不影响播放，'
                '只是这里算不出预估。</span>')
            return
        # ★ 最终播放尺寸 = 「源 × **模型原生倍率**」再被「限定播放分辨率」压一次。
        #   必须在这里算对 —— 预估卡上的输出尺寸要和 live.vpy 真跑出来的一致，
        #   否则用户会按一个不存在的尺寸去理解性能。
        #   ⚠ 没有「输出倍率」旋钮了（2026-09-30 删）—— 模型倍率是设计好的，
        #     它改不了推理倍率（pick_ladder 只看源尺寸），和 cap 职责重叠。
        _cv, _ce = self._cap_value()
        # ⚠ 传**生效模型** sr（可能已被 auto 换成 re2x），不是控件当前值
        _ns = self._native_scale(sr)
        ow, oh = capped_play_size(w, h, _ns, _cv, _ce)
        target = (resolve_target_fps(self.cb_multi.currentData(), base_fps)
                  if ip else base_fps)
        parts = [f"{order.upper()} 顺序"]
        parts.append(sr if up else "不超分")
        if up:
            _sc = f"输出 {ow}×{oh}"
            if (ow, oh) != (int(w * _ns), int(h * _ns)):
                # 被「限定播放分辨率」压过 —— 写清楚，否则用户对不上账
                _sc += "（已限播放分辨率）"
            parts.append(_sc)
        # ⚠ 必须带上 srmax/sedge —— 漏传的话预估卡显示的是"不限上限时的档"，
        #   和实际跑出来的档不一致（用户会照着一个不存在的档位理解性能）。
        sx = pick_ladder(w, h, sr, self._srmax_value(), self._srmax_edge()) if up else None
        _sel = self.cb_multi.currentData()
        _mult = resolve_multi(_sel, base_fps) if ip else 1.0
        if ip:
            # 档位是倍率语义时写清「源 ×n」，绝对档位（60/144）照旧
            _lbl = (f"源 ×{_mult:g}" if _sel == 0 or _sel < 0
                    else f"目标 {_sel}fps")
            parts.append(f"补帧 {_lbl} → {target:.0f}fps")
        else:
            parts.append("不补帧")
        if sx:
            parts.append(f"超分档 {sx[0]}×{sx[1]}")
        # ★★ 2026-09-30 用户要求：预估卡只留**客观数据**，删掉全部判定/建议类文本
        #   （原「达标 / 实测上限 N fps / 本片走 … 档 / 建议：…」）。
        #   连带 `cap = max_stable_fps(...)` 也不再需要 —— 它只服务于那些判定。
        #   行数刻意压到 3 行以内：lb_plan 的 minimumHeight 是 48px（参数区预算），
        #   超了就会把文字裁掉。
        #   ⚠ 「源 24fps 补帧 ×2 目标 48fps」那行已删 —— 它和第一行的
        #     「补帧 源 ×2 → 48fps」完全重复，白占一行。
        # ★★ 2026-10-01 用户要求：**速度数字全部不上卡片** ——
        #   「xx ms/帧 ≈ xx fps」和「依据：实测」都是**本机 4080 的实测值**，
        #   换台机器完全不通用（原话："别人的电脑又不是 4080，都不通用的"），
        #   摆出来只会误导。预估卡现在只描述**配置**，不给任何速度数字。
        #   （`ms`/`tag` 仍在内部算，留给日志/排障用，只是不再显示。）
        _extra: list[str] = []
        # 非整数倍率的开销说明（保留：它讲的是**机制**，不是本机速度数字）
        if ip and base_fps > 0 and target > base_fps * 2:
            m = target / base_fps
            if abs(m - round(m)) > 0.02:
                n = interp_calls(m)
                alt = int(round(base_fps)) * 2
                save = 1 - 0.5 / (n / m)          # 相对 2x 档省下的比例
                _extra.append(
                    f'<span style="color:#8a8a9c">'
                    f'倍率 {m:.2f}x 是小数 → 每对输入帧要调 {n} 次推理；'
                    f'{alt}fps（2 倍，整数）只需 1 次，约省 {save * 100:.0f}% 开销'
                    f'</span>')
        _txt = f"{w}×{h}　·　" + "　·　".join(parts)
        if _extra:
            _txt += "<br>" + "<br>".join(_extra)
        self.lb_plan.setText(_txt)

    # ══════════════════════════════════════════════════════ 队列操作

    def _add_paths(self, paths: list[str], silent: bool = False) -> None:
        added, new_files = 0, []
        for p in paths:
            pp = Path(p)
            if pp.is_dir():
                for f in sorted(pp.rglob("*")):
                    if f.suffix.lower() in VIDEO_EXT:
                        if self._append(str(f)):
                            added += 1
                            new_files.append(str(f))
                continue
            if pp.suffix.lower() in VIDEO_EXT and self._append(str(pp)):
                added += 1
                new_files.append(str(pp))
        if added and not silent:
            self._log(f"[控制台] 加入 {added} 个文件\n")
        if added:
            self._refresh_enabled()
            self._update_plan()
            self._start_probe(new_files)

    def _append(self, path: str) -> bool:
        for i in range(self.lst_files.count()):
            if self.lst_files.item(i).data(Qt.UserRole) == path:
                return False
        it = QListWidgetItem(Path(path).name)
        it.setData(Qt.UserRole, path)
        it.setToolTip(path)
        self.lst_files.addItem(it)
        if self.lst_files.currentRow() < 0:
            self.lst_files.setCurrentRow(0)
        return True

    def _start_probe(self, files: list[str]) -> None:
        todo = [f for f in files if f not in self.media]
        if not todo:
            return
        w = ProbeWorker(todo, self)
        w.probed.connect(self._on_probed)
        w.vfr.connect(self._on_vfr)
        w.vfr_done.connect(self._on_vfr_done)
        w.start()
        self._probe_worker = w

    def _on_vfr_done(self, path: str) -> None:
        """VFR 判定完成（含 CFR）→ 记录，避免 `_start` 时还没判完导致"首次播放时间轴乱"。"""
        self._vfr_ready.add(path)

    def _on_vfr(self, path: str, is_vfr: bool, fps: float) -> None:
        """记下 VFR 判定（修「VFR 源超分/补帧后音画漂移」用）。"""
        self.vfr_src[path] = (is_vfr, fps)
        if not is_vfr:
            return
        cf = self.media.get(path, (0, 0, 0.0))[2]
        _real = f"实测平均 {fps:.3g}fps" if fps > 0 else "帧率不固定"
        # 列表里标出来（`w×h 30fps` → `w×h 22.8fps VFR`）—— 一眼看得出哪集不适用补帧
        w, h = self.media.get(path, (0, 0, 0.0))[:2]
        for i in range(self.lst_files.count()):
            it = self.lst_files.item(i)
            if it.data(Qt.UserRole) == path:
                if w and h:
                    it.setText(f"{Path(path).name}\n{w}×{h}　{fps:.3g}fps　VFR")
                break
        self._log(f"[控制台][!] 该片源是**可变帧率（VFR）**"
                  f"（容器头写 {cf:.3g}fps，{_real}）\n"
                  f"           → 播放时保留原始时间轴（不做 AssumeFPS），"
                  f"以免音画持续漂移\n"
                  f"           → 统计页 / OSD 的 FPS 会按实测值显示"
                  f"（不再是被写死的 {cf:.3g}，所以「跑不满 30」是正常的）\n"
                  f"           → 代价：该片不能用补帧（补帧要恒定帧率），超分不受影响\n")
        self._update_plan()

    def _on_probed(self, path: str, w: int, h: int, fps: float) -> None:
        self.media[path] = (w, h, fps)
        for i in range(self.lst_files.count()):
            it = self.lst_files.item(i)
            if it.data(Qt.UserRole) == path:
                it.setText(f"{Path(path).name}\n{w}×{h}　{fps:.3g}fps")
                break
        self._refresh_enabled()   # 拿到分辨率才能算「本片走哪档」
        self._update_plan()
        # ★ 导入即按源尺寸把超分引擎备好（用户要求：不指望用户自己跑 build 脚本）
        self._warm_sr_engine(path)

    # ── 超分引擎预热：导入视频后按源尺寸后台预建 TRT 引擎 ─────────────
    def _warm_sr_engine(self, path: str | None = None) -> None:
        """按「当前模型 + 已导入视频的源尺寸」后台预建 TRT 引擎。

        用户要求：不能指望使用者自己去跑 build_*_engines.py —— 导入视频后
        就该把该尺寸的引擎备好。已有缓存则跳过；缺则用 `.venv` 起一个
        **独立进程**跑 sr_engine.py（GUI 进程绝不 import vapoursynth）。
        """
        try:
            if self.cb_sreng.currentData() == "a4k":
                return
            key = str(self.cb_sr.currentData() or "")
            if not key or key.lower().startswith("anime4kcpp"):
                return
            if path is None:
                it = self.lst_files.currentItem()
                if it is None and self.lst_files.count():
                    it = self.lst_files.item(0)
                path = it.data(Qt.UserRole) if it else None
            wh = self.media.get(path) if path else None
            if not wh or not wh[0] or not wh[1]:
                return
            w, h = int(wh[0]), int(wh[1])
            if any(w == a and h == b for a, b in sr_ladder(key)):
                return                                  # 该尺寸已有引擎，无需编
            if not VS_PY.is_file() or not (ROOT / "sr_engine.py").is_file():
                return                                  # 没自包含环境 → 交给 live.vpy 播放时现编
            tag = f"{key}@{w}x{h}"
            if tag in self._warm_tags:
                return                                  # 已在建/建过
            self._warm_tags.add(tag)
        except Exception:
            return

        self._log(f"[控制台][i] 本机没有 {key} 在 {w}×{h} 的引擎 → "
                  f"后台预建（约 10~20 秒，编完缓存，同尺寸下次秒开）\n")
        qp = QProcess(self)
        qp.setProcessChannelMode(QProcess.MergedChannels)
        qp.finished.connect(
            lambda code, st, _k=key, _w=w, _h=h, _q=qp:
            self._on_warm_done(_k, _w, _h, code, _q))
        qp.start(str(VS_PY), [str(ROOT / "sr_engine.py"), key, f"{w}x{h}"])
        self._warm_procs.append(qp)

    def _on_warm_done(self, key: str, w: int, h: int, code: int, qp) -> None:
        out = bytes(qp.readAllStandardOutput()).decode("utf-8", "replace").strip()
        ok = (code == 0 and any(w == a and h == b for a, b in sr_ladder(key)))
        self._log(f"[控制台][{'i' if ok else '!'}] 预建引擎 {key} {w}×{h} "
                  f"{'完成' if ok else '失败'}"
                  + (f"（{out.splitlines()[-1]}）" if out and not ok else "") + "\n")
        if ok:
            self._fill_sr_models()
            self._update_plan()
        try:
            self._warm_procs.remove(qp)
        except ValueError:
            pass
        qp.deleteLater()

    def _add_files(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXT))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频", self._start_dir(),
            f"视频文件 ({exts});;所有文件 (*)")
        if paths:
            self.cfg["last_in_dir"] = str(Path(paths[0]).parent)
            self._add_paths(paths)

    def _add_folder(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "选择文件夹", self._start_dir())
        if d:
            self.cfg["last_in_dir"] = d
            self._add_paths([d])

    def _start_dir(self) -> str:
        for cand in (self.cfg.get("last_in_dir"), Path.home()):
            if cand and Path(cand).is_dir():
                return str(cand)
        return str(ROOT)

    def _on_sel_changed(self, *_) -> None:
        """切文件时同时刷预估卡和引擎档位信息。"""
        self._update_plan()
        self._refresh_enabled()

    def _del_selected(self) -> None:
        for it in self.lst_files.selectedItems():
            self.lst_files.takeItem(self.lst_files.row(it))
        self._refresh_enabled()
        self._update_plan()

    def _clear_files(self) -> None:
        self.lst_files.clear()
        self._refresh_enabled()
        self._update_plan()

    def _move(self, delta: int) -> None:
        r = self.lst_files.currentRow()
        if r < 0:
            return
        t = r + delta
        if not (0 <= t < self.lst_files.count()):
            return
        it = self.lst_files.takeItem(r)
        self.lst_files.insertItem(t, it)
        self.lst_files.setCurrentRow(t)

    # ══════════════════════════════════════════════════════ 拖放

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._add_paths(paths)
            e.acceptProposedAction()

    # ══════════════════════════════════════════════════════ 播放 / 停止

    def _browse_mpv(self) -> None:
        """浏览选择一个 mpv 程序，填入「mpv 程序」输入框。"""
        p, _ = QFileDialog.getOpenFileName(
            self, "选择 mpv 程序", str(ROOT / "mpv"),
            "mpv 可执行文件 (*.exe);;所有文件 (*.*)")
        if p:
            self.le_mpv.setText(p)
            self._on_toggle()

    def _start(self) -> None:
        if self.proc.state() != QProcess.NotRunning:
            return
        files = [self.lst_files.item(i).data(Qt.UserRole)
                 for i in range(self.lst_files.count())]
        if not files:
            return
        cur = self._collect_cfg()
        mpv = resolve_mpv(cur.get("mpv_path"))
        if not mpv.is_file():
            self._log(f"[控制台][X] 找不到 mpv：{mpv}\n")
            return
        # ★ 外部 mpv 若自带 VSScript.dll（如 mpv-lazy），先临时让它让位，
        #   否则超分链路会被它的 VapourSynth 环境抢走、静默失效。退出时还原。
        self._vs_neutralized = _neutralize_external_vs(mpv)
        if self._vs_neutralized:
            self._log(f"[控制台][i] 外部 mpv 自带 VapourSynth，已临时让位"
                      f" {len(self._vs_neutralized)} 个 dll（退出还原）\n")

        env = QProcessEnvironment.systemEnvironment()
        path = env.value("PATH") or ""
        env.insert("PATH", f"{VSROOT};{VSPY};{path}")   # mpv 靠 PATH 找 vsscript.dll
        env.insert("PYTHONPATH", str(VSSP))

        pipe = rf"\\.\pipe\mpv-live-{os.getpid()}"
        # ★ VFR 源（rmvb 这类容器帧率造假的）超分/补帧后音画漂移修复，
        #   详见 build_launch 里那一大段。判到 VFR 就置 LIVE_VFR=1，
        #   让 live.vpy 跳过 AssumeFPS、保留原始时间轴。
        #   正常 CFR 片源 vfr=False，行为完全不变。
        # ★ 首播前**确保** VFR 已判定：ProbeWorker 是异步的（要解码 ~15s），若用户
        #   在探测完成前就点播放，vfr_src 还没有该文件 → 取到默认 (False,0) →
        #   LIVE_VFR=0 → live.vpy 走 AssumeFPS(假 fps) → **时间轴被拍平**。
        #   这正是「第一次播放乱、第二次就好」的病根（第二次探测已完成）。
        #   这里对首个文件做一次**同步**补判（仅首次、约 1 秒），之后走缓存。
        _first = files[0]
        if _first not in self._vfr_ready:
            self._log("[控制台][i] 首次播放：正在判定片源是否可变帧率（VFR）…\n")
            try:
                _iv, _rf = ProbeWorker._probe_vfr(_first)
            except Exception:                            # noqa: BLE001
                _iv, _rf = (False, 0.0)
            self._vfr_ready.add(_first)
            if _iv or _rf > 0:
                self.vfr_src[_first] = (_iv, _rf)
        _vfr_info = self.vfr_src.get(files[0], (False, 0.0))
        env_add, args = build_launch(cur, files, pipe,
                                     vfr=bool(_vfr_info[0]),
                                     real_fps=float(_vfr_info[1]))
        for k, v in env_add.items():
            env.insert(k, v)
        self.proc.setProcessEnvironment(env)

        self._splitter = LineSplitter()
        self._last_was_replace = False
        self._fps_hist = []
        self._drop_hist = []
        self._fno_hist = []
        self._play_t0 = time.monotonic()
        self._last_stat_log = self._play_t0
        self._last_osd_push = self._play_t0
        self.log.clear()
        self._save_cfg()

        # 目标帧率：源帧率 × 补帧倍率
        base = 24.0
        first = self.lst_files.item(0).data(Qt.UserRole)
        if first in self.media:
            base = self.media[first][2]
        # ★ VFR 源：容器帧率是假的（实测会拍平成假时间轴），显示上换成实测平均帧率，
        #   免得日志里报「目标 60fps」而实际补帧根本没跑（VFR 会跳过补帧）。
        if first in self.vfr_src:
            _is_vfr, _rf = self.vfr_src[first]
            if _is_vfr and _rf > 0:
                base = _rf
        self._target_fps = (resolve_target_fps(cur["interp_fps"], base)
                            if cur["interp"] else base)
        o0, s0, pms, ptag, w0, h0, _, _bf0 = self._current_plan()
        cap0 = (max_stable_fps(w0, h0, o0, s0, cur["upscale"], cur["interp"])
                if w0 else None)
        # 存「实时上限」而不是离线吞吐 —— 后者会把输出帧率上限误判成能力不足
        self._plan_fps = cap0 or ((1000.0 / pms) if pms else None)

        self.lb_live.setText("<span style='color:#8a8a9c'>○ 启动中…"
                             "　首次要建图 / 编引擎，可能黑屏几秒到半分钟"
                             "</span>")
        self._log("[控制台] " + " ".join(args[:-len(files)]) + "\n")
        self._log(f"[控制台] 共 {len(files)} 个文件入队"
                  f"　目标 {self._target_fps:.0f} fps"
                  + (f"　预估处理 {self._plan_fps:.0f} fps（{ptag}）"
                     if self._plan_fps else "") + "\n")
        self.proc.start(str(mpv), args)
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)

        self._stat = MpvStat(pipe, self)
        self._stat.start()
        # GPU/CPU 采集线程（OSD 第二行用）：跟播放走，别在空闲时白烧
        if self._sys is None or not self._sys.is_alive():
            self._sys = SysStat()
            self._sys.start()
        self._ui_timer.start()

    def _stop(self) -> None:
        if self.proc.state() == QProcess.NotRunning:
            return
        self.lb_live.setText("<span style='color:#e0c06a'>⏹ 正在停止…</span>")
        self.proc.terminate()
        if self._kill_timer is None:
            self._kill_timer = QTimer(self)
            self._kill_timer.setSingleShot(True)
            self._kill_timer.timeout.connect(self._force_kill)
        self._kill_timer.start(3000)

    def _force_kill(self) -> None:
        if self.proc.state() != QProcess.NotRunning:
            self._log("[控制台] 3 秒未退出，强制结束\n")
            self.proc.kill()

    def _on_mpv_finished(self, code: int, status) -> None:
        # ★ 还原被临时让位的外部 mpv 自带 VSScript.dll（让 lazy 等便携包恢复原状）
        if getattr(self, "_vs_neutralized", None):
            _restore_external_vs(self._vs_neutralized)
            self._vs_neutralized = []
        if self._kill_timer:
            self._kill_timer.stop()
        self._ui_timer.stop()
        d = self._stat.snapshot if self._stat else {}
        if self._stat:
            self._stat.stop()
            self._stat = None
        if self._sys is not None:
            self._sys.stop()
            self._sys = None

        stable = [v for t, v in self._fps_hist
                  if t - getattr(self, "_play_t0", t) > 3.0]
        if stable:
            avg = sum(stable) / len(stable)
            mist = d.get("mistimed-frame-count") or 0
            # 整段播放的平均丢帧速率（比滑动窗口更能代表全程）
            if len(self._drop_hist) > 1:
                span = self._drop_hist[-1][0] - self._drop_hist[0][0]
                total_drop = self._drop_hist[-1][1] - self._drop_hist[0][1]
                rate = total_drop / span if span > 1 else 0.0
            else:
                span = total_drop = 0
                rate = 0.0
            shown = max(avg - rate, 0.0)
            tgt = self._target_fps
            verdict = ("流畅达标" if tgt and shown >= tgt * 0.97 else
                       (f"只到 {100 - shown / tgt * 100:.0f}% 缺口" if tgt else ""))
            self._log(f"[控制台] ══ 播放统计 ══ 输出均值 {avg:.1f} fps"
                      f"　实际显示 {shown:.1f} fps（目标 {tgt:.0f}）　{verdict}"
                      f"　丢帧 {rate:.1f}/s（共 {total_drop}）　晚点帧 {mist}")
            if self._plan_fps:
                self._log(f"[控制台] 实测输出 {avg:.0f} fps / 目标 {tgt:.0f}"
                          f"（这套配置的实时上限约 {self._plan_fps:.0f} fps）")

        self._log(f"[控制台] mpv 退出，返回码 {code}\n")
        self.lb_live.setText(f"<span style='color:#8a8a9c'>○ 已停止"
                             f"（返回码 {code}）</span>")
        self._refresh_enabled()
        self.btn_stop.setEnabled(False)

    # ── 运行时状态：IPC 推得很快，这里只存不算；刷新交给 250ms 的定时器 ──

    @staticmethod
    def _fmt_time(v) -> str:
        try:
            v = float(v)
        except (TypeError, ValueError):
            return "--:--"
        if v < 0 or v != v:
            return "--:--"
        h, m, s = int(v // 3600), int(v % 3600 // 60), int(v % 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    @staticmethod
    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _sample(self, d: dict) -> None:
        """记录一次 vf-fps / 丢帧计数 / **实测帧号**（界面每 250 ms 采一次）。"""
        now = time.monotonic()
        fps = d.get("estimated-vf-fps")
        if (not isinstance(fps, bool) and isinstance(fps, (int, float))
                and math.isfinite(fps) and fps > 0.5):
            self._fps_hist.append((now, float(fps)))
            if len(self._fps_hist) > 6000:
                del self._fps_hist[:3000]
        drop = d.get("frame-drop-count")
        if isinstance(drop, int) and not isinstance(drop, bool):
            self._drop_hist.append((now, drop))
            if len(self._drop_hist) > 6000:
                del self._drop_hist[:3000]
        # ★ 实测帧号：mpv 推进到的帧号，**递增**。差分 = 真实播放帧率（见 _real_fps）。
        #   它和 estimated-vf-fps 的区别就是"实测 vs 声明"。
        fno = d.get("estimated-frame-number")
        if isinstance(fno, int) and not isinstance(fno, bool):
            self._fno_hist.append((now, fno))
            if len(self._fno_hist) > 6000:
                del self._fno_hist[:3000]

    def _drop_rate(self, window: float = 5.0) -> float:
        """每秒丢帧数 —— 这才是"实际卡不卡"的判据。

        只看 estimated-vf-fps 会被骗：它到 48 也可能 VO 一直在丢帧保同步。
        实测 concurrent-frames=2 时输出 48 fps、每秒却丢 20 帧，
        实际只显示 27 fps。**而且这个数必须在"有音轨 + 真音频时钟"下量**：
        无音轨时 mpv 会放慢视频去迁就滤镜（time-pos 只走 0.66×），
        看起来 0 丢帧，其实是慢放 —— 有音轨时 cf=3 照样每秒丢 8 帧。
        """
        if len(self._drop_hist) < 2:
            return 0.0
        now = time.monotonic()
        pts = [(t, c) for t, c in self._drop_hist if now - t <= window]
        if len(pts) < 2:
            return 0.0
        dt = pts[-1][0] - pts[0][0]
        if dt < 0.2:
            return 0.0
        return max((pts[-1][1] - pts[0][1]) / dt, 0.0)

    def _avg_fps(self, window: float = 4.0):
        """最近 window 秒的平均滤镜输出帧率（平滑瞬时抖动）。"""
        if not self._fps_hist:
            return None
        now = time.monotonic()
        vals = [v for t, v in self._fps_hist if now - t <= window]
        if not vals:
            return None
        return sum(vals) / len(vals)

    def _real_fps(self, window: float = 2.0):
        """**实测**帧率 = 播放帧号的推进速率（帧/秒）；样本不足返回 None。

        ★ 为什么必须另算一个（而不是直接用 `estimated-vf-fps`）：后者是滤镜链
          **声明**要输出多少帧 —— 超分不改帧率，它就恒等于源帧率；补帧 2x 就恒
          等于 48。**从头到尾一个数都不动**，看不出实际快慢。
          `estimated-frame-number` 是 mpv 真正推进到的帧号，差值/时间差 = 真实帧率。
        ★ 它能抓到 `estimated-vf-fps` 看不见的一种情况：**mpv 降速慢放**
          （滤镜链供不上、又没有音轨时钟约束时，mpv 不丢帧而是放慢播放）——
          那一刻实测帧率会明显低于源帧率，而"输出 fps"照样纹丝不动。
        """
        if len(self._fno_hist) < 2:
            return None
        now = time.monotonic()
        pts = [(t, n) for t, n in self._fno_hist if now - t <= window]
        if len(pts) < 2:
            return None
        dt = pts[-1][0] - pts[0][0]
        if dt < 0.2:
            return None
        return max((pts[-1][1] - pts[0][1]) / dt, 0.0)

    @staticmethod
    def render_live(d: dict, avg, tgt: float, drop_rate: float = 0.0,
                    real_fps=None) -> str:
        """把 mpv 属性快照渲染成状态面板的 HTML（纯函数，可离线断言）。

        判据用「实际显示帧率 = 输出帧率 − 丢帧速率」，而不是输出帧率本身 ——
        实测 concurrent-frames 不足时输出能到 48 fps 却每秒丢 20 帧，
        实际只显示 27 fps。只看 vf-fps 会被骗。
        """
        drop = d.get("frame-drop-count") or 0
        mist = d.get("mistimed-frame-count") or 0
        ddec = d.get("decoder-frame-drop-count") or 0
        vdelay = d.get("vo-delayed-frame-count") or 0
        # ★ 用 `video-out-params`（**滤镜链之后**）—— `video-params` 是解码器输出，
        #   挂了超分滤镜之后它一直显示**源尺寸**：实测源 1280x720 时面板显示
        #   `→ 1280×720`，而真实输出是 2560×1440（探针实测两种属性都查过）。
        #   回退到 video-params 只为兼容旧快照。
        ow, oh = d.get("video-out-params/w"), d.get("video-out-params/h")
        if not (isinstance(ow, int) and isinstance(oh, int)):
            ow, oh = d.get("video-params/w"), d.get("video-params/h")
        res = (f"{ow}×{oh}" if isinstance(ow, int) and isinstance(oh, int)
               else "—")
        _disp = d.get("display-fps")
        if (isinstance(_disp, (int, float)) and not isinstance(_disp, bool)
                and _disp > 1):
            res += f"　屏 {_disp:.0f}Hz"
        pos = MainWindow._fmt_time(d.get("time-pos"))
        dur = MainWindow._fmt_time(d.get("duration"))
        title = d.get("media-title") or ""
        pn, pc = d.get("playlist-pos"), d.get("playlist-count")
        idx = (f"　<span style='color:#8a8a9c'>{pn + 1}/{pc}</span>"
               if isinstance(pn, int) and isinstance(pc, int) and pc else "")
        paused = bool(d.get("pause"))
        lines = [
            f"<span style='color:{'#e0c06a' if paused else '#8ea8d8'}'>"
            f"{'⏸' if paused else '▶'}</span> <b>{MainWindow._esc(title)}</b>"
            f"<span style='color:#8a8a9c'>　{pos} / {dur}{idx}</span>"
        ]
        if avg is None:
            lines.append("<span style='color:#8a8a9c'>处理速度测量中…"
                         "（等 mpv 统计出第一组数据）</span>")
        else:
            shown = max(avg - drop_rate, 0.0)
            col, tail = "#dcdce4", ""
            if tgt > 0:
                ratio = shown / tgt
                col = ("#7fd6a0" if ratio >= 0.97 else
                       "#e0c06a" if ratio >= 0.85 else "#e08a8a")
                tail = (f"　<span style='color:{col}'>目标 {tgt:.0f}"
                        f"　达成 {ratio * 100:.0f}%</span>")
            # ★ 「实时」这一项才是**实测**（帧号差分），其余两项是声明值/推算值。
            #   拿不到实测帧号时整段不出现，输出与原格式逐字一致。
            _real = (f"<span style='color:{col}'>实时 <b>{real_fps:.1f} fps</b>"
                     f"</span>　" if real_fps else "")
            lines.append(f"{_real}<span style='color:{col}'>输出 "
                         f"<b>{avg:.1f} fps</b></span>　实际显示 "
                         f"<b>{shown:.1f} fps</b>{tail}")
        dcol = ("#7fd6a0" if drop_rate < 1.0 else
                "#e0c06a" if drop_rate < 5.0 else "#e08a8a")
        dtag = ("流畅" if drop_rate < 1.0 else
                "轻微掉帧" if drop_rate < 5.0 else "明显掉帧")
        lines.append(f"<span style='color:{dcol}'>● {dtag}　"
                     f"丢帧 {drop_rate:.1f}/s（累计 {drop}）"
                     f"　晚点帧 {mist}　解码丢 {ddec}　积压 {vdelay}</span>"
                     f"<span style='color:#8a8a9c'>　→ {res}</span>")
        return "<br>".join(lines)

    def _osd_line(self, d: dict, avg, rate: float, real=None) -> str:
        """推给 mpv OSD 的那一行**纯文本**（OSD 不认 HTML，别带标签）。

        内容与面板那行一致，只是压成一行、去掉颜色标记。
        """
        parts = []
        if real:
            parts.append(f"实时 {real:.1f} fps")
        if avg is not None:
            parts.append(f"输出 {avg:.1f} fps")
            parts.append(f"实际显示 {max(avg - rate, 0):.1f} fps"
                         f"（目标 {self._target_fps:.0f}）")
        if rate >= 0.05:
            parts.append(f"丢帧 {rate:.1f}/s（累计 "
                         f"{d.get('frame-drop-count') or 0}）")
        ow, oh = d.get("video-out-params/w"), d.get("video-out-params/h")
        if isinstance(ow, int) and isinstance(oh, int):
            parts.append(f"{ow}×{oh}")
        _disp = d.get("display-fps")
        if (isinstance(_disp, (int, float)) and not isinstance(_disp, bool)
                and _disp > 1):
            parts.append(f"屏 {_disp:.0f}Hz")
        line1 = "　".join(parts)
        # ★ 第二行：GPU/CPU 利用率、显存、硬件型号（SysStat 后台每秒采集，
        #   拿不到就整行不出现）。mpv 的 show-text 里 \n 会渲染成换行。
        snap = self._sys.snapshot if self._sys is not None else {}
        p2 = []
        if "gpu_util" in snap:
            p2.append(f"GPU {snap['gpu_util']}%")
            mu, mt = snap.get("gpu_mem_used"), snap.get("gpu_mem_total")
            if mu is not None and mt:
                p2.append(f"{mu / 1024:.1f}/{mt / 1024:.1f}G")
            if self._gpu_short:
                p2.append(self._gpu_short)
        if "cpu_util" in snap:
            p2.append(f"CPU {snap['cpu_util']}%")
            if self._cpu_short:
                p2.append(self._cpu_short)
        return line1 + ("\n" + " · ".join(p2) if p2 else "")

    def _refresh_live(self) -> None:
        if self.proc.state() == QProcess.NotRunning:
            return
        d = self._stat.snapshot if self._stat else {}
        if not d:
            return
        self._sample(d)
        avg = self._avg_fps()
        rate = self._drop_rate()
        real = self._real_fps()
        self.lb_live.setText(self.render_live(d, avg, self._target_fps, rate,
                                              real))

        now = time.monotonic()
        # ★ 每秒把同一行统计推给 mpv 的 OSD（左上角小字）—— 全屏播放时不用切回界面。
        #   时长给 2.2s（> 1s 间隔）避免闪烁；和 mpv 自己的状态行分两行、互不覆盖。
        if (self._stat and self._stat.opened and (avg is not None or real is not None)
                and now - self._last_osd_push >= 1.0):
            self._last_osd_push = now
            self._stat.push(["show-text", self._osd_line(d, avg, rate, real),
                             2200, 3])

        # 每 5 秒往日志里留一行，播完还能回查
        if avg is not None and now - self._last_stat_log >= 5.0:
            self._last_stat_log = now
            _rt = f"实时 {real:.1f} fps · " if real else ""
            self._log(f"[状态] {_rt}输出 {avg:.1f} fps · 实际显示"
                      f" {max(avg - rate, 0):.1f} fps（目标"
                      f" {self._target_fps:.0f}）· 丢帧 {rate:.1f}/s"
                      f" · {self._fmt_time(d.get('time-pos'))}"
                      f"/{self._fmt_time(d.get('duration'))}")

    # ══════════════════════════════════════════════════════ 日志

    def _on_mpv_out(self) -> None:
        raw = bytes(self.proc.readAllStandardOutput())
        for text, replace in self._splitter.feed(raw):
            self._append_log(text, replace)

    def _append_log(self, text: str, replace: bool) -> None:
        cur = self.log.textCursor()
        # ★ 首次"覆盖式"片段前面是普通日志行，直接覆盖会把它吃掉 —— 加状态位挡一下
        do_replace = replace and self._last_was_replace and self.log.blockCount() > 0
        if do_replace:
            cur.movePosition(QTextCursor.End)
            cur.select(QTextCursor.LineUnderCursor)
            cur.insertText(text)
        else:
            # ★ 空文档不能再 insertBlock，否则开头会多一个空行
            if self.log.toPlainText():
                cur.movePosition(QTextCursor.End)
                cur.insertBlock()
            cur.insertText(text)
        self.log.setTextCursor(cur)
        self.log.ensureCursorVisible()
        self._last_was_replace = replace

    def _log(self, text: str) -> None:
        for line in text.splitlines() or [""]:
            self._append_log(line, False)

    # ══════════════════════════════════════════════════════ 自检

    def _selfcheck(self) -> None:
        if not SELFCHECK.is_file() or not VS_PY.is_file():
            self._log("[控制台][X] 自检脚本或 VS 解释器不存在\n")
            return
        self._log("[控制台] 运行环境自检…\n")
        t = threading.Thread(target=self._run_selfcheck, daemon=True)
        t.start()

    def _run_selfcheck(self) -> None:
        try:
            r = subprocess.run(
                [str(VS_PY), str(SELFCHECK)],
                cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            out = r.stdout.decode("utf-8", "replace")
        except Exception as e:
            out = f"[控制台][X] 自检失败：{e}"
        for line in out.splitlines():
            if "deprecated" in line:
                continue
            QTimer.singleShot(0, lambda l=line: self._log(l))

    # ══════════════════════════════════════════════════════ 关闭

    def closeEvent(self, e) -> None:
        """关窗时的收尾。★ 整段包 try：清理失败也绝不能让窗口关不掉。"""
        self._save_cfg()
        try:
            pw = getattr(self, "_probe_worker", None)
            if pw is not None and pw.isRunning():
                pw.stop()
            st = getattr(self, "_stat", None)
            if st is not None:
                st.stop()
            proc = getattr(self, "proc", None)
            if proc is not None and proc.state() != QProcess.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(2500):
                    proc.kill()
        except Exception:
            pass
        e.accept()

    def _save_cfg(self) -> None:
        self.cfg.update(self._collect_cfg())
        save_cfg(self.cfg)


def _install_crash_guard() -> Path:
    """给「无控制台启动」（pythonw）兜底。

    pythonw 下 sys.stderr 是 None，未捕获异常**静默消失** ——
    用户看到的只是"双击了但没反应"。所以：
      ① stdout/stderr 为 None 时重定向到日志文件（防止任何隐式写入崩掉）
      ② excepthook 把异常落到同一个文件，并尽量弹框告诉用户去哪看
    """
    log = ROOT / "console_error.log"
    try:
        if sys.stdout is None or sys.stderr is None:
            fh = open(log, "a", encoding="utf-8", buffering=1)
            if sys.stdout is None:
                sys.stdout = fh
            if sys.stderr is None:
                sys.stderr = fh
    except Exception:
        pass

    def hook(t, v, tb):
        txt = "".join(traceback.format_exception(t, v, tb))
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(chr(10) + "=" * 62 + chr(10)
                        + time.strftime("%Y-%m-%d %H:%M:%S")
                        + chr(10) + txt)
        except Exception:
            pass
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None, "控制台出错",
                f"{t.__name__}: {v}" + chr(10) * 2 + f"完整堆栈已写入：{chr(10)}{log}")
        except Exception:
            pass

    sys.excepthook = hook
    return log


def main() -> int:
    crash_log = _install_crash_guard()
    try:
        app = QApplication(sys.argv)
        app.setStyleSheet(QSS)
        w = MainWindow()
        w.show()
        return app.exec()
    except BaseException:                 # 连"启动就崩"也要留下证据
        try:
            with open(crash_log, "a", encoding="utf-8") as f:
                f.write(chr(10) + '=' * 62 + chr(10) + traceback.format_exc())
        except Exception:
            pass
        raise


    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
