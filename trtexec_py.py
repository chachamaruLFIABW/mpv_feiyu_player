# -*- coding: utf-8 -*-
"""用 TensorRT Python API 顶替 `trtexec.exe` —— 让 vsmlrt 的 `Backend.TRT` 全功能可用。

═══ 为什么需要它（2026-09-29 实测定位）═══
vsmlrt 的 TRT 后端**不是**自己编引擎，而是命令行调
`plugins/vsmlrt-cuda/trtexec(.exe)` 现场编译（见 vsmlrt.py 的 `trtexec()`）。
而 pip 版 VapourSynth 的 wheel 里 `plugins/vsmlrt-cuda/` **只有 3 个 dll、没有 exe**，
全盘也搜不到；NVIDIA 官方的 TensorRT zip（含 trtexec.exe）要登录账号才能下。
后果：`Backend.TRT` 对超分和补帧**全都**用不了 —— 而补帧又只能靠 TRT
（ncnn 不支持 RIFE 的 GridSample），等于补帧彻底没路。

但 `vsmlrt.trtexec` 只是个**模块级普通函数**，行为完全可以用 tensorrt 的 Python API 复刻：

    ① 用 `vsmlrt.get_engine_path()` 算引擎路径 —— **复用它的规则**，
       这样引擎命名/缓存判定与官方完全一致，将来真拿到 trtexec 也不会冲突重编。
    ② 引擎已存在且 ≥1024 字节 → 直接返回（与官方同样的短路）。
    ③ TRT 11 删了 `BuilderFlag.FP16`，fp16 必须**先把 onnx 转成 fp16**
       （用 vsmlrt 现成的 `convert_model()`，跟官方 trtexec 分支的做法一字不差）。
    ④ 建网 → optimization profile → `build_serialized_network` → 写盘。
返回的仍是 `engine_path` 字符串，vsmlrt 拿它去 `core.trt.Model(clips, engine_path, ...)`，
**对上层完全透明**。

═══ 用法 ═══
在 .vpy 里加载 vsmlrt 之后调用一次即可：

    import vsmlrt, trtexec_py
    trtexec_py.install(vsmlrt)      # 之后 vsmlrt 的 TRT 后端就能用了

⚠️ 引擎是**按尺寸**编的（固定 shape），换分辨率会重新编（几分钟）。
   一部片源尺寸固定，所以一个尺寸编一次就够，之后秒开。
"""
from __future__ import annotations

import os
import sys
import time

_LOGGER = None


def _make_logger(verbose: bool):
    global _LOGGER
    import tensorrt as trt
    if _LOGGER is None:
        _LOGGER = trt.Logger(trt.Logger.VERBOSE if verbose else trt.Logger.WARNING)
    return _LOGGER


def _parse_trt_version(v: int) -> tuple[int, int, int]:
    """与 vsmlrt.parse_trt_version 同义（110300 -> (11, 3, 0)）。"""
    s = str(v)
    if len(s) <= 4:
        return (int(s), 0, 0)
    return (int(s[:-4]), int(s[-4:-2]), int(s[-2:]))


def build_engine(network_path: str, engine_path: str, *, channels: int,
                 opt_shapes: tuple[int, int], min_shapes: tuple[int, int],
                 max_shapes: tuple[int, int], static_shape: bool = True,
                 workspace: int | None = None, verbose: bool = False,
                 input_name: str = "input", builder_optimization_level: int = 3,
                 use_cuda_graph: bool = False) -> None:
    """把 onnx 编成序列化引擎并写到 `engine_path`。"""
    import tensorrt as trt

    lg = _make_logger(verbose)
    builder = trt.Builder(lg)

    # TRT 11 删了 NetworkDefinitionCreationFlag.EXPLICIT_BATCH；10.x 需要它
    try:
        net = builder.create_network(
            1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    except AttributeError:
        net = builder.create_network()

    parser = trt.OnnxParser(net, lg)
    with open(network_path, "rb") as f:
        if not parser.parse(f.read()):
            errs = "\n".join(str(parser.get_error(i)) for i in range(parser.num_errors))
            raise RuntimeError(f"ONNX 解析失败：{network_path}\n{errs}")

    cfg = builder.create_builder_config()
    # workspace：vsmlrt 默认不给（None），这里给足，否则某些层会因显存不足回退到慢实现
    cfg.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE,
                              int(workspace) if workspace else (4 << 30))
    try:
        cfg.builder_optimization_level = int(builder_optimization_level)
    except Exception:                              # 老版本 TRT 没这个属性
        pass

    # 输入名以 onnx 自己声明的为准（vsmlrt 传进来的 input_name 是它期望的，
    # 但真实图里可能叫别的，用网络里的第一个输入最稳）
    real_in = net.get_input(0).name
    if real_in != input_name:
        # 不报错，只提示 —— 形状要给对名字才生效
        print(f"[trtexec_py] 注：onnx 输入名是 {real_in!r}，"
              f"vsmlrt 传的是 {input_name!r}，按 {real_in!r} 设形状",
              file=sys.stderr, flush=True)

    nch = 3
    if len(net.get_input(0).shape) == 4:
        nch = int(net.get_input(0).shape[1])       # 用图里的真实通道数（RIFE 是 11）
    if nch != channels:
        print(f"[trtexec_py] 注：通道数以图为准 {nch}（vsmlrt 传 {channels}）",
              file=sys.stderr, flush=True)

    prof = builder.create_optimization_profile()
    oh, ow = int(opt_shapes[1]), int(opt_shapes[0])   # opt_shapes 是 (w, h)
    if static_shape:
        prof.set_shape(real_in, (1, nch, oh, ow), (1, nch, oh, ow), (1, nch, oh, ow))
    else:
        mh, mw = int(min_shapes[1]), int(min_shapes[0])
        xh, xw = int(max_shapes[1]), int(max_shapes[0])
        prof.set_shape(real_in, (1, nch, mh, mw), (1, nch, oh, ow), (1, nch, xh, xw))
    cfg.add_optimization_profile(prof)

    t0 = time.perf_counter()
    print(f"[trtexec_py] 编引擎 {os.path.basename(engine_path)} "
          f"（{nch}ch {ow}x{oh}{' 固定' if static_shape else ' 动态'}，"
          f"TRT {trt.__version__}）…", file=sys.stderr, flush=True)
    serialized = builder.build_serialized_network(net, cfg)
    if serialized is None:
        raise RuntimeError("TRT 引擎编译失败（build_serialized_network 返回 None）")
    blob = bytes(serialized)
    os.makedirs(os.path.dirname(engine_path) or ".", exist_ok=True)
    tmp = f"{engine_path}.tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, engine_path)                   # 原子替换，避免半截文件被当成有效引擎
    print(f"[trtexec_py] 完成：{len(blob) / 2**20:.1f} MB，"
          f"耗时 {time.perf_counter() - t0:.1f}s → {engine_path}",
          file=sys.stderr, flush=True)


def make_trtexec(vsmlrt):
    """返回一个与 `vsmlrt.trtexec` 同签名的替身函数。"""

    def trtexec(
        network_path, channels, opt_shapes, max_shapes, fp16, device_id,
        workspace=None, verbose=False, use_cuda_graph=False, use_cublas=False,
        static_shape=True, tf32=False, log=False, use_cudnn=False,
        use_edge_mask_convolutions=True, use_jit_convolutions=True, heuristic=False,
        input_name="input", input_format=0, output_format=0, min_shapes=(0, 0),
        faster_dynamic_shapes=True, force_fp16=False, builder_optimization_level=3,
        max_aux_streams=None, short_path=None, bf16=False, custom_env={},
        custom_args=[], engine_folder=None, max_tactics=None,
        tiling_optimization_level=0, l2_limit_for_tiling=-1,
    ):
        # ---- ① 引擎路径：完全复用 vsmlrt 的规则，保证缓存判定一致 ----
        trt_ver = _parse_trt_version(int(vsmlrt.core.trt.Version()["tensorrt_version"]))
        if isinstance(opt_shapes, int):
            opt_shapes = (opt_shapes, opt_shapes)
        if isinstance(max_shapes, int):
            max_shapes = (max_shapes, max_shapes)
        if force_fp16:
            fp16, tf32, bf16 = True, False, False
        try:
            dev = vsmlrt.core.trt.DeviceProperties(device_id)["name"].decode()
            dev = dev.replace(" ", "-")
        except AttributeError:
            dev = f"device{device_id}"

        engine_path = vsmlrt.get_engine_path(
            network_path=network_path, min_shapes=min_shapes, opt_shapes=opt_shapes,
            max_shapes=max_shapes, workspace=workspace, fp16=fp16, use_cublas=use_cublas,
            static_shape=static_shape, tf32=tf32, use_cudnn=use_cudnn,
            input_format=input_format, output_format=output_format,
            builder_optimization_level=builder_optimization_level,
            max_aux_streams=max_aux_streams, short_path=short_path, bf16=bf16,
            engine_folder=engine_folder, trt_version=trt_ver, device_name=dev,
        )

        # ---- ② 命中缓存直接返回（与官方 trtexec 同样的短路条件） ----
        if os.access(engine_path, mode=os.R_OK) and os.path.getsize(engine_path) >= 1024:
            return engine_path

        # ---- ③ TRT 11：fp16 得先把 onnx 转 fp16（官方 trtexec 分支同款做法） ----
        src_onnx = network_path
        if (fp16 or bf16) and trt_ver >= (11, 0, 0):
            target = f"{engine_path}.onnx"
            if not os.path.exists(target):
                vsmlrt.convert_model(
                    network_path=network_path, target_network_path=target,
                    fp16=fp16, bf16=bf16,
                    input_format=input_format, output_format=output_format,
                )
            src_onnx = target

        # ---- ④ 编引擎 ----
        build_engine(
            src_onnx, engine_path,
            channels=channels, opt_shapes=opt_shapes, min_shapes=min_shapes,
            max_shapes=max_shapes, static_shape=static_shape, workspace=workspace,
            verbose=verbose, input_name=input_name,
            builder_optimization_level=builder_optimization_level,
            use_cuda_graph=use_cuda_graph,
        )
        return engine_path

    return trtexec


def install(vsmlrt) -> None:
    """把 `vsmlrt.trtexec` 换成 Python API 实现。可重复调用（幂等）。"""
    if getattr(vsmlrt.trtexec, "__trtexec_py__", False):
        return
    fn = make_trtexec(vsmlrt)
    fn.__trtexec_py__ = True
    vsmlrt.trtexec = fn
    print("[trtexec_py] 已接管 vsmlrt.trtexec（用 TensorRT Python API 编引擎）",
          file=sys.stderr, flush=True)
