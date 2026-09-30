# -*- coding: utf-8 -*-
"""把 AnimeJaNai / anime4x 一类的 **1x3xHxW → 2x** 超分 onnx 编成 TRT 引擎。

为什么单开一个脚本（不复用 build_fp16io_engines.py）：
  · 那支脚本是给"自训 realesr-animevideov3"用的，源路径写死在主项目里，
    还带一大段「Resize 残差支路」的图改写逻辑（只对那个图结构有效）。
  · AnimeJaNai 的 onnx **本身就是 fp16、IO 也是 fp16**（`..._fp16.onnx`），
    不需要任何改写，直接编就行 —— 硬套那套改写逻辑反而会报
    「图结构与预期不符」。

模型清单（models/animejanai/）：
  2x_AnimeJaNai_HD_V3.1_Balanced_SPANF3_b8f64_unshuffle_fp16.onnx
  2x_AnimeJaNai_HD_V3.1Sharp1_Balanced_SPANF3_b8f64_unshuffle_fp16.onnx
  2x_AnimeJaNai_HD_V3.1_Performance_SPANF3_b5f48_unshuffle_fp16.onnx
  2x_AnimeJaNai_HD_V3.1Sharp1_Performance_SPANF3_b5f48_unshuffle_fp16.onnx
  2x_AnimeJaNai_SD_V1beta34_Compact_...(动态 H/W，本项目按固定档编译)

命名规范（与 models/sr/ 对齐，方便 live.vpy 用同一套模板拼路径）：
  <短名>_fp16io_<W>x<H>.engine

用法：
    python build_aj_engines.py                    # 编全部（Balanced/Sharp1/Performance × 4 档）
    python build_aj_engines.py performance        # 只编名字含 performance 的
    python build_aj_engines.py 1280x720           # 只编指定档位
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "models" / "animejanai"
OUT_DIR = ROOT / "models" / "sr_aj"
sys.path.insert(0, str(ROOT))

# onnx 文件 → live.vpy 里用的「短名」
MODELS = {
    "2x_AnimeJaNai_HD_V3.1_Performance_SPANF3_b5f48_unshuffle_fp16.onnx":
        "AnimeJaNai_HD_V3.1_Performance",
    "2x_AnimeJaNai_HD_V3.1Sharp1_Performance_SPANF3_b5f48_unshuffle_fp16.onnx":
        "AnimeJaNai_HD_V3.1Sharp1_Performance",
    "2x_AnimeJaNai_HD_V3.1_Balanced_SPANF3_b8f64_unshuffle_fp16.onnx":
        "AnimeJaNai_HD_V3.1_Balanced",
    "2x_AnimeJaNai_HD_V3.1Sharp1_Balanced_SPANF3_b8f64_unshuffle_fp16.onnx":
        "AnimeJaNai_HD_V3.1Sharp1_Balanced",
}
# 各模型要编哪些源尺寸档（和 models/sr/ 的档位保持一致）
SIZES = [(640, 360), (854, 480), (960, 720), (1280, 720), (1920, 1080)]

BLOAT_BYTES = 150 << 20
MAX_TRIES = 4


def _ratio(blob: bytes) -> float:
    import zlib
    return len(blob) / max(1, len(zlib.compress(blob, 6)))


def build_with_retry(onnx_path: Path, eng: Path, w: int, h: int) -> bool:
    """编一个档位。TRT 的序列化退化是**非确定性**的，重跑通常就好。"""
    import trtexec_py
    for k in range(1, MAX_TRIES + 1):
        if eng.is_file():
            eng.unlink()
        trtexec_py.build_engine(
            str(onnx_path), str(eng),
            channels=3, opt_shapes=(w, h), min_shapes=(w, h),
            max_shapes=(w, h), static_shape=True, workspace=2 << 30,
        )
        blob = eng.read_bytes()
        r = _ratio(blob)
        if len(blob) < BLOAT_BYTES and r <= 10.0:
            return True
        print(f"    第 {k} 次是胖引擎（{len(blob) / 2 ** 20:.0f} MB，"
              f"压缩比 {r:.1f}）→ 删掉重编")
    return False


def verify(eng_path: Path) -> None:
    import tensorrt as trt
    lg = trt.Logger(trt.Logger.ERROR)
    eng = trt.Runtime(lg).deserialize_cuda_engine(eng_path.read_bytes())
    if eng is None:
        raise RuntimeError(f"反序列化失败：{eng_path}")
    parts = []
    for i in range(eng.num_io_tensors):
        n = eng.get_tensor_name(i)
        mode = "IN " if eng.get_tensor_mode(n) == trt.TensorIOMode.INPUT else "OUT"
        dt = eng.get_tensor_dtype(n)
        shp = tuple(int(d) for d in eng.get_tensor_shape(n))
        nb = (2 if dt == trt.float16 else 4)
        for d in shp:
            nb *= d
        kind = "fp16" if dt == trt.float16 else ("fp32" if dt == trt.float32 else str(dt))
        parts.append(f"{mode} {kind} {shp} = {nb / 1048576:.1f} MB")
    print("      " + " | ".join(parts))


def main():
    args = [a.lower() for a in sys.argv[1:]]
    want_sizes = set()
    for a in args:
        if "x" in a:
            w, h = a.split("x")
            want_sizes.add((int(w), int(h)))
    name_filters = [a for a in args if "x" not in a]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total_ok = total_fail = 0

    for onnx_name, short in MODELS.items():
        if name_filters and not any(f in short.lower() for f in name_filters):
            continue
        src = SRC_DIR / onnx_name
        if not src.is_file():
            print(f"--- {short}：缺 onnx {src.name}，跳过")
            continue
        todo = [s for s in SIZES if not want_sizes or s in want_sizes]
        print(f"--- {short}  ({src.stat().st_size / 1048576:.1f} MB onnx)")
        for (w, h) in todo:
            eng = OUT_DIR / f"{short}_fp16io_{w}x{h}.engine"
            if eng.is_file() and 1024 < eng.stat().st_size < BLOAT_BYTES:
                print(f"  [{w}x{h}] 已存在（{eng.stat().st_size / 1048576:.2f} MB）")
                verify(eng)
                total_ok += 1
                continue
            t0 = time.perf_counter()
            ok = build_with_retry(src, eng, w, h)
            if ok:
                total_ok += 1
                print(f"  [{w}x{h}] {time.perf_counter() - t0:.1f}s OK"
                      f"（{eng.stat().st_size / 1048576:.2f} MB）")
                verify(eng)
            else:
                total_fail += 1
                print(f"  [{w}x{h}] {time.perf_counter() - t0:.1f}s !! 仍是胖引擎，这档先别用")

    print(f"\n完成：{total_ok} 个可用，{total_fail} 个失败")


if __name__ == "__main__":
    main()
