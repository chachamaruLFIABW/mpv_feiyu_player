# -*- coding: utf-8 -*-
"""用 **TensorRT 11.0.0.114** 重编超分引擎（绕过 TRT 11.3 的退化-plan 回归）。

═══ 为什么需要这个脚本（2026-10-02）═══
TensorRT **11.3.0.99 有回归**：同一个 onnx，它稳定编出 1~4 GB 的退化 plan
（占位区大小 ≈ 17×64×H×W×2），而 **11.0.0.114 稳定编出 1~8 MB** 正常引擎。
把 `vsmlrt-cuda/` 里的 nvinfer 换成 11.0 后，运行时也必须用 11.0 编的引擎
（引擎序列化格式 11.0=243 / 11.3=244，**不跨版本**）。

═══ 用法（必须用 ComfyUI 的 python，那套是 TRT 11.0）═══
    "D:/ComfyUI-aki-v3.2/python/python.exe" build_engines_trt110.py
    ... 1280x720            # 只编指定档位

产出 `models/sr/<短名>_fp16io_<W>x<H>.engine`；已有的瘦引擎会跳过，
旧胖引擎会先改名成 `.bloatbak` 再重编。
"""
from __future__ import annotations

import os
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SR_DIR = ROOT / "models" / "sr"
ONNX_DIR = ROOT / "models" / "sr_onnx"

# onnx 短名 → 要编哪些档位（与 live.vpy 的 LADDER 对齐）
MODELS = {
    "realesr-animevideov3_re2x": [(854, 480), (1280, 720), (1920, 1080)],
    "realesr-animevideov3": [(640, 360), (854, 480), (1280, 720), (1920, 1080)],
}

BLOAT_RATIO = 10.0
BLOAT_BYTES = 150 << 20
OPT_LEVEL = int(os.environ.get("LVL", "5"))


def is_slim(p: Path) -> bool:
    """瘦引擎判定（读全文件算 zlib 压缩比）。"""
    if not p.is_file() or p.stat().st_size < 1024:
        return False
    if p.stat().st_size > BLOAT_BYTES:
        return False
    b = p.read_bytes()
    return len(b) / max(1, len(zlib.compress(b, 6))) <= BLOAT_RATIO


def build_one(onnx: Path, eng: Path, w: int, h: int) -> bool:
    import tensorrt as trt

    lg = trt.Logger(trt.Logger.ERROR)
    b = trt.Builder(lg)
    try:
        net = b.create_network(
            1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    except AttributeError:
        net = b.create_network()
    ps = trt.OnnxParser(net, lg)
    try:
        ok = ps.parse_from_file(str(onnx))
    except AttributeError:
        ok = ps.parse(onnx.read_bytes())
    if not ok:
        errs = "\n".join(str(ps.get_error(i)) for i in range(ps.num_errors))
        print(f"    ONNX 解析失败：{errs[:200]}")
        return False

    cfg = b.create_builder_config()
    cfg.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 4 << 30)
    try:
        cfg.builder_optimization_level = OPT_LEVEL
    except Exception:                                          # noqa: BLE001
        pass
    prof = b.create_optimization_profile()
    in_name = net.get_input(0).name
    prof.set_shape(in_name, (1, 3, h, w), (1, 3, h, w), (1, 3, h, w))
    cfg.add_optimization_profile(prof)

    ser = b.build_serialized_network(net, cfg)
    if ser is None:
        print("    build_serialized_network 返回 None")
        return False
    blob = bytes(ser)
    tmp = eng.with_suffix(eng.suffix + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, eng)                       # 原子替换
    ratio = len(blob) / max(1, len(zlib.compress(blob, 6)))
    tag = "瘦 ✓" if (len(blob) < BLOAT_BYTES and ratio <= BLOAT_RATIO) else "胖!!"
    print(f"    {len(blob)/2**20:8.1f} MB  压缩比 {ratio:6.1f}  {tag}")
    return tag.startswith("瘦")


def main() -> None:
    import tensorrt as trt
    print(f"TensorRT {trt.__version__}   opt_level={OPT_LEVEL}")
    if not trt.__version__.startswith("11.0"):
        sys.exit("  ⚠ 本脚本必须用 TensorRT **11.0** 跑（否则编出来的还是退化引擎）")

    want = set()
    for a in sys.argv[1:]:
        if "x" in a.lower():
            w, h = a.lower().split("x")
            want.add((int(w), int(h)))

    SR_DIR.mkdir(parents=True, exist_ok=True)
    ok_n = fail_n = 0
    for short, sizes in MODELS.items():
        onnx = ONNX_DIR / f"{short}_fp16io.onnx"
        if not onnx.is_file():
            print(f"--- {short}：缺 {onnx.name}，跳过")
            continue
        todo = [s for s in sizes if not want or s in want]
        if not todo:
            continue
        print(f"--- {short}")
        for (w, h) in todo:
            eng = SR_DIR / f"{short}_fp16io_{w}x{h}.engine"
            if is_slim(eng):
                print(f"  [{w}x{h}] 已是瘦引擎（{eng.stat().st_size/2**20:.1f} MB）→ 跳过")
                ok_n += 1
                continue
            if eng.is_file():
                bak = eng.with_suffix(eng.suffix + ".bloatbak")
                if not bak.is_file():
                    eng.rename(bak)
                    print(f"  [{w}x{h}] 旧引擎 {eng.stat().st_size if eng.exists() else 0}"
                          f" → 已改名 {bak.name}")
                else:
                    eng.unlink()
            t0 = time.time()
            good = build_one(onnx, eng, w, h)
            print(f"  [{w}x{h}] 用时 {time.time()-t0:.0f}s")
            ok_n += good
            fail_n += not good

    print(f"\n完成：{ok_n} 个瘦引擎，{fail_n} 个失败")


if __name__ == "__main__":
    main()
