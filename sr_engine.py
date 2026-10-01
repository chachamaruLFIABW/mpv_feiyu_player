# -*- coding: utf-8 -*-
"""按**源尺寸**现场编译超分 TRT 引擎（编完落盘缓存）。

═══ 为什么需要（2026-10-02）═══
超分引擎都是 **fixed shape**。原来的做法是把任意源尺寸往一张固定档位表里塞：

    2242x1080 的源 > 所有档位宽度（最大 1920）
      → pick_ladder 走兜底，落到 1920x1080 档
      → 源先被 bicubic 缩到 1920x1080（**横向压 14.4%**，比例 2.076 → 1.778）
      → 2x 推理出 3840x2160（此时画面是压扁的）
      → fit_play_cap 再非等比缩到 2990x1440 把比例拉回来

最终几何虽然是对的，但**中间那次横向压缩是纯损失**（水平方向 14.4% 的信息在
超分之前就没了）。改成「源多大就编多大」就取消这一步，2x 输出天然等比、
全程不需要任何非等比缩放。

═══ 代价 ═══
**首次**遇到某个尺寸要等编译（本机实测 opt_level=3：1280x720 = 14s、
2242x1080 = 11s），编完落盘，之后同尺寸秒开。
比补帧便宜得多（RIFE 首次按尺寸现编约 70s）。

═══ ⚠ 环境要求 ═══
必须跑在 **TensorRT 11.0** 的运行时上。11.3.0.99 有回归，同一个 onnx 会稳定
编出 1~4 GB 的退化 plan（详见 build_engines_trt110.py）。本机 `.venv` 已经整体
换成 11.0（python 包 + nvinfer dll），所以 live.vpy 里调本模块是安全的。

═══ 用法 ═══
    import sr_engine
    eng = sr_engine.ensure("realesr-animevideov3_re2x", 2242, 1080, log=print)
    if eng is not None:
        ...  # 引擎已就绪（新编或命中缓存）

命令行单独用（预编某个尺寸，不必等播放）：
    .venv\\Scripts\\python.exe sr_engine.py re2x 2242x1080
"""
from __future__ import annotations

import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 引擎"胖瘦"判据（与 build_engines_trt110.py 一致）：
#   TRT 11.3 的退化 plan 占位区 ≈ 17×64×W×H×2，压缩比 26~200、体积几百 MB~4 GB；
#   正常引擎 1~8 MB、压缩比 1~5。两个条件任一不满足就判胖。
BLOAT_BYTES = 150 << 20
BLOAT_RATIO = 10.0

OPT_LEVEL = 3          # 3 = 默认；实测 11.0 下 opt3/opt5 都能编出瘦引擎，opt3 更快

# 短名 → (onnx 文件名, onnx 所在目录, 引擎输出目录)
MODELS: dict[str, tuple[str, Path, Path]] = {
    "realesr-animevideov3_re2x": (
        "realesr-animevideov3_re2x_fp16io.onnx",
        HERE / "models" / "sr_onnx", HERE / "models" / "sr"),
    "realesr-animevideov3": (
        "realesr-animevideov3_fp16io.onnx",
        HERE / "models" / "sr_onnx", HERE / "models" / "sr"),
    "AnimeJaNai_HD_V3.1_Balanced": (
        "2x_AnimeJaNai_HD_V3.1_Balanced_SPANF3_b8f64_unshuffle_fp16.onnx",
        HERE / "models" / "animejanai", HERE / "models" / "sr_aj"),
    "AnimeJaNai_HD_V3.1Sharp1_Balanced": (
        "2x_AnimeJaNai_HD_V3.1Sharp1_Balanced_SPANF3_b8f64_unshuffle_fp16.onnx",
        HERE / "models" / "animejanai", HERE / "models" / "sr_aj"),
    "AnimeJaNai_HD_V3.1_Performance": (
        "2x_AnimeJaNai_HD_V3.1_Performance_SPANF3_b5f48_unshuffle_fp16.onnx",
        HERE / "models" / "animejanai", HERE / "models" / "sr_aj"),
    "AnimeJaNai_HD_V3.1Sharp1_Performance": (
        "2x_AnimeJaNai_HD_V3.1Sharp1_Performance_SPANF3_b5f48_unshuffle_fp16.onnx",
        HERE / "models" / "animejanai", HERE / "models" / "sr_aj"),
}

# 短名别名（GUI 里的简写 → 完整短名）
ALIASES = {
    "re2x": "realesr-animevideov3_re2x",
    "animev3": "realesr-animevideov3",
    "animejanai": "AnimeJaNai_HD_V3.1_Balanced",
}


def canon(short: str) -> str:
    """把 GUI 用的简写归一成 MODELS 里的完整短名。"""
    s = (short or "").strip()
    low = s.lower()
    if low in ALIASES:
        return ALIASES[low]
    for k in MODELS:
        if k.lower() == low:                 # 大小写不敏感匹配
            return k
    if low.startswith("animejanai"):         # 容错手打
        return "AnimeJaNai_HD_V3.1_Balanced"
    return s


def engine_path(short: str, w: int, h: int) -> Path:
    """该 (模型, 尺寸) 的引擎路径（不确定文件是否存在）。"""
    name = canon(short)
    ent = MODELS.get(name)
    out_dir = ent[2] if ent else (HERE / "models" / "sr")
    return out_dir / f"{name}_fp16io_{w}x{h}.engine"


def is_slim(p: Path) -> bool:
    """瘦引擎判定（读全文件算 zlib 压缩比；文件不存在/过小一律 False）。"""
    if not p.is_file() or p.stat().st_size < 1024:
        return False
    if p.stat().st_size > BLOAT_BYTES:
        return False
    blob = p.read_bytes()
    return len(blob) / max(1, len(zlib.compress(blob, 6))) <= BLOAT_RATIO


def build_one(onnx: Path, eng: Path, w: int, h: int,
              opt_level: int = OPT_LEVEL) -> tuple[bool, str]:
    """编一个固定尺寸静态引擎，原子落盘。返回 (成功, 说明文本)。

    ★ 用 TensorRT Python API 而不是 trtexec.exe：**不需要外部 exe**，而且
      能直接拿到 `build_serialized_network` 的返回 blob（trtexec 只能写文件，
      失败了也会留个残缺文件）。
    """
    import tensorrt as trt

    lg = trt.Logger(trt.Logger.ERROR)
    b = trt.Builder(lg)
    try:
        net = b.create_network(
            1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    except AttributeError:                       # 老接口兜底
        net = b.create_network()

    ps = trt.OnnxParser(net, lg)
    try:
        ok = ps.parse_from_file(str(onnx))
    except AttributeError:
        ok = ps.parse(onnx.read_bytes())
    if not ok:
        errs = "; ".join(str(ps.get_error(i)) for i in range(ps.num_errors))
        return False, f"ONNX 解析失败：{errs[:160]}"

    cfg = b.create_builder_config()
    cfg.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 4 << 30)
    try:
        cfg.builder_optimization_level = opt_level
    except Exception:                            # noqa: BLE001
        pass

    prof = b.create_optimization_profile()
    in_name = net.get_input(0).name
    prof.set_shape(in_name, (1, 3, h, w), (1, 3, h, w), (1, 3, h, w))
    cfg.add_optimization_profile(prof)

    ser = b.build_serialized_network(net, cfg)
    if ser is None:
        return False, "build_serialized_network 返回 None（该尺寸编译失败）"

    blob = bytes(ser)
    ratio = len(blob) / max(1, len(zlib.compress(blob, 6)))
    slim = len(blob) < BLOAT_BYTES and ratio <= BLOAT_RATIO
    if not slim:                                 # 胖的不落盘，免得被后续加载
        return False, (f"编出退化 plan（{len(blob)/2**20:.1f} MB，压缩比 {ratio:.1f}）"
                       f"→ 丢弃。多半是 TRT 版本不对（需要 11.0）")

    tmp = eng.with_suffix(eng.suffix + ".tmp")
    tmp.write_bytes(blob)
    import os as _os
    _os.replace(tmp, eng)                        # 原子替换：不会留下半截文件
    return True, f"{len(blob)/2**20:.2f} MB（压缩比 {ratio:.1f}）"


def ensure(short: str, w: int, h: int, *, build: bool = True,
           log=None) -> Path | None:
    """确保 (模型, 尺寸) 的引擎可用：命中缓存直接返回，缺则现编。

    返回引擎路径；失败/不允许编译返回 None（调用方据此退回原档位逻辑）。
    """
    name = canon(short)
    ent = MODELS.get(name)
    if ent is None:
        if log:
            log(f"现场编译：不认识的模型 {short!r} → 跳过")
        return None
    onnx_name, onnx_dir, out_dir = ent
    eng = out_dir / f"{name}_fp16io_{w}x{h}.engine"

    if is_slim(eng):
        return eng                               # 命中缓存（含本次前一次编的）
    if not build:
        return None

    onnx = onnx_dir / onnx_name
    if not onnx.is_file():
        if log:
            log(f"现场编译：缺 onnx {onnx.name}（找过 {onnx_dir}）→ 跳过")
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    if log:
        log(f"首次遇到 {w}x{h}：现场编译超分引擎（约 10~20 秒，编完缓存，"
            f"下次同尺寸秒开）…")
    t0 = time.perf_counter()
    ok, msg = build_one(onnx, eng, w, h)
    dt = time.perf_counter() - t0
    if not ok:
        if log:
            log(f"现场编译失败（{dt:.1f}s）：{msg} → 退回原档位")
        return None
    if log:
        log(f"编译完成 {dt:.1f}s → {eng.name} {msg}")
    return eng


def _main(argv: list[str]) -> int:
    """命令行预编：`python sr_engine.py re2x 2242x1080 [更多尺寸...]`"""
    if len(argv) < 3:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    short = argv[1]
    rc = 0
    for a in argv[2:]:
        w, h = (int(x) for x in a.lower().split("x"))
        p = ensure(short, w, h, log=lambda s: print("  " + s, flush=True))
        print(f"  [{w}x{h}] {'OK → ' + p.name if p else '失败'}")
        rc |= 0 if p else 1
    return rc


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv))
