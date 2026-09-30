# -*- coding: utf-8 -*-
"""把超分模型编成 **fp16 I/O** 的 TRT 引擎。

为什么需要：
  现有引擎（`*_fp16_*.engine`）里的 `fp16` 只是**构建精度**，I/O 仍是 fp32 ——
  实测查过：1920x1080 档 input=FLOAT 23.7MB / output=FLOAT 94.9MB，
  **每帧 118.6 MB 要过主机内存**，48fps 就是 5.7 GB/s 的 CPU 侧拷贝。
  换成 fp16 I/O 直接砍半（59 MB/帧）。

做法（和 vsmlrt 的 convert_model 一致）：
  onnx → `convert_float_to_float16(keep_io_types=False)`（**IO 也变 fp16**）
       → TensorRT Python API 编固定尺寸静态引擎 → `*_fp16io_<W>x<H>.engine`

用法：
    python build_fp16io_engines.py                # 编全部需要的档位
    python build_fp16io_engines.py 1920x1080      # 只编指定档位
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SR_DIR = ROOT / "models" / "sr"
ONNX_CACHE = ROOT / "models" / "sr_onnx"          # 放转换后的 fp16io onnx
SRC_ONNX_DIR = Path(r"E:\workbuddy\2026-09-26-22-47-22\models\onnx")

sys.path.insert(0, str(ROOT))

# 模型名 → 源 onnx（主项目里的 fp32 版）
MODELS = {
    "realesr-animevideov3_re2x": "realesr-animevideov3_re2x_fp32.onnx",
    "realesr-animevideov3": "realesr-animevideov3_fp32.onnx",
}
# 各模型需要哪些档位（和 models/sr 里已有的引擎对齐）
SIZES = {
    "realesr-animevideov3_re2x": [(854, 480), (1280, 720), (1920, 1080)],
    "realesr-animevideov3": [(640, 360), (854, 480), (1280, 720), (1920, 1080)],
}


def _uniquify_node_names(m) -> int:
    """给每个节点起唯一名字。

    ★ `convert_float_to_float16` 插入的 Cast 节点是拿**节点名**拼出来的
      （`<node.name>_output_cast_0`）。原图里若有重名/匿名节点，就会插出两个
      同名张量，TRT 直接报
      `ERROR: Output name is not unique: /Resize_output_cast_0`。
      节点名不参与任何计算，改成唯一的是安全的。
    """
    seen = {}
    fixed = 0
    for i, node in enumerate(m.graph.node):
        nm = node.name or ""
        if not nm or nm in seen:
            node.name = f"{node.op_type}_{i}_uniq"
            fixed += 1
        seen[node.name] = True
    return fixed


def _set_cast_to(node, tensor_type: int) -> None:
    for a in node.attribute:
        if a.name == "to":
            a.i = tensor_type
            return
    raise RuntimeError("Cast 节点没有 to 属性")


def _cast_to_of(node) -> int:
    for a in node.attribute:
        if a.name == "to":
            return a.i
    return -1


def to_fp16io(src_fp16: Path, dst: Path) -> Path:
    """把「内部 fp16、IO fp32」的图改成 **IO 也 fp16**。

    为什么不直接 `convert_float_to_float16(keep_io_types=False)`：那个转换器会
    插入 Cast，遇到本模型（Resize 残差支路 + Add 汇合）会**插出两个同名张量**
    （`/Resize_output_cast_0`），TRT 直接报
    `ERROR: Output name is not unique` / `Failed to sort the model topologically`。
    实测无解（节点本身没有重名，是转换器内部撞的）。

    所以只做**边界改写**，语义等价、可控：
      1. 图形输入声明 fp32 → fp16，主干直接吃 fp16（去掉入口那个 Cast）
      2. 残差支路的 Resize 必须是 fp32 → 单独补一个 Cast(fp16→fp32) 喂它
      3. 图形输出声明 fp32 → fp16，去掉出口那个 Cast
    """
    import onnx
    from onnx import TensorProto, helper
    if dst.is_file() and dst.stat().st_size > 1024:
        return dst
    m = onnx.load(str(src_fp16))
    g = m.graph
    in_name = g.input[0].name
    out_name = g.output[0].name

    entry = next((n for n in g.node if n.op_type == "Cast"
                  and n.input[0] == in_name
                  and _cast_to_of(n) == TensorProto.FLOAT16), None)
    exit_ = next((n for n in g.node if n.op_type == "Cast"
                  and n.output[0] == out_name
                  and _cast_to_of(n) == TensorProto.FLOAT), None)
    if entry is None or exit_ is None:
        raise RuntimeError(
            f"图结构与预期不符（entry={entry is not None} exit={exit_ is not None}）"
            f"，没法安全改写 —— 别硬来，先人工看一遍 {src_fp16.name}")

    # 1) 残差支路要 fp32：补一个 Cast 给 Resize 用
    resizes = [n for n in g.node if n.op_type == "Resize" and n.input[0] == in_name]
    if resizes:
        extra = helper.make_node(
            "Cast", [in_name], [in_name + "__fp32_for_resize"],
            name="input_to_fp32_for_resize",
            to=TensorProto.FLOAT)
        g.node.insert(0, extra)
        for r in resizes:
            r.input[0] = extra.output[0]

    # 2) 主干改吃 fp16 输入（entry.output 的消费者全部改指向 in_name）
    body_tensor = entry.output[0]
    for n in g.node:
        for i, t in enumerate(n.input):
            if t == body_tensor:
                n.input[i] = in_name
    g.node.remove(entry)

    # 3) 输入/输出声明翻成 fp16
    g.input[0].type.tensor_type.elem_type = TensorProto.FLOAT16
    g.output[0].name = exit_.input[0]
    g.output[0].type.tensor_type.elem_type = TensorProto.FLOAT16
    g.node.remove(exit_)

    onnx.checker.check_model(m)
    onnx.save(m, str(dst))
    print(f"  改写为 fp16 I/O → {dst.name}（{dst.stat().st_size/1048576:.2f} MB）")
    return dst


def fp16io_onnx(name: str) -> Path:
    """拿到（必要时生成）fp16 I/O 版的 onnx。

    源用主项目里已有的 **内部 fp16、IO fp32** 的 `_fp16.onnx`
    （那是 trt_upscaler.to_fp16_onnx 的产物，权重已经是 fp16）。
    """
    dst = ONNX_CACHE / f"{name}_fp16io.onnx"
    if dst.is_file() and dst.stat().st_size > 1024:
        return dst
    ONNX_CACHE.mkdir(parents=True, exist_ok=True)
    cand = [
        SRC_ONNX_DIR / MODELS[name],                              # fp32 原件
        SRC_ONNX_DIR / MODELS[name].replace("_fp32", "_fp16"),    # fp16 版
    ]
    src_fp16 = next((p for p in cand if p.is_file() and "_fp16" in p.name), None)
    if src_fp16 is None:
        raise FileNotFoundError(f"缺 fp16 源 onnx（找过 {[p.name for p in cand]}）")
    return to_fp16io(src_fp16, dst)


def verify(eng_path: Path) -> None:
    import tensorrt as trt
    lg = trt.Logger(trt.Logger.ERROR)
    eng = trt.Runtime(lg).deserialize_cuda_engine(eng_path.read_bytes())
    if eng is None:
        raise RuntimeError(f"反序列化失败：{eng_path}")
    for i in range(eng.num_io_tensors):
        n = eng.get_tensor_name(i)
        mode = "IN " if eng.get_tensor_mode(n) == trt.TensorIOMode.INPUT else "OUT"
        dt = eng.get_tensor_dtype(n)
        shp = tuple(eng.get_tensor_shape(n))
        nb = 2 if dt == trt.float16 else (4 if dt == trt.float32 else 1)
        for d in shp:
            nb *= int(d)
        kind = "fp16" if dt == trt.float16 else ("fp32" if dt == trt.float32 else str(dt))
        print(f"    [{mode}] {n:8s} {kind} {shp} = {nb/1048576:.1f} MB")


# ---- 胖引擎（TRT 序列化退化）检测：判据与 tools/trt_upscaler.py 保持一致 ----
BLOAT_RATIO = 10.0            # 序列化 plan 的 zlib 压缩比上限
BLOAT_BYTES = 150 << 20       # 绝对上限 150 MB（正常引擎只有 1~8 MB）
MAX_TRIES = 4                 # 这条退化路径是**非确定性**的，重跑通常就好


def _ratio(blob: bytes) -> float:
    import zlib
    return len(blob) / max(1, len(zlib.compress(blob, 6)))


def build_with_retry(onnx_path: Path, eng: Path, w: int, h: int) -> bool:
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
        if len(blob) < BLOAT_BYTES and r <= BLOAT_RATIO:
            return True
        print(f"    第 {k} 次是胖引擎（{len(blob)/2**20:.0f} MB，压缩比 {r:.1f}）"
              f"→ 删掉重编")
    return False


def main():
    want = set()
    for a in sys.argv[1:]:
        if "x" in a:
            w, h = a.lower().split("x")
            want.add((int(w), int(h)))

    for name, sizes in SIZES.items():
        todo = [s for s in sizes if not want or s in want]
        if not todo:
            continue
        print(f"--- {name}")
        onnx_path = fp16io_onnx(name)
        for (w, h) in todo:
            eng = SR_DIR / f"{name}_fp16io_{w}x{h}.engine"
            if eng.is_file() and 1024 < eng.stat().st_size < BLOAT_BYTES:
                print(f"  [{w}x{h}] 已存在（{eng.stat().st_size/1048576:.1f} MB）")
                verify(eng)
                continue
            t0 = time.perf_counter()
            ok = build_with_retry(onnx_path, eng, w, h)
            tag = "OK" if ok else "!! 仍是胖引擎，这档先别用"
            print(f"  [{w}x{h}] 用时 {time.perf_counter()-t0:.1f}s  {tag}")
            if ok:
                verify(eng)


if __name__ == "__main__":
    main()
