# -*- coding: utf-8 -*-
r"""实时播放器前置自检 —— **不加载任何模型、不调用 CUDA**，纯静态检查。

用法（用本目录 .venv 那个解释器跑，vapoursynth 就装在里面）：

    .venv\python.exe selfcheck.py
    .venv\python.exe selfcheck.py --link   # 再真跑一次纯 CPU 链路

★ 2026-09-30：整目录已自包含（自带 Python 3.13 + VS + PySide6），
  不再依赖 D:\ComfyUI-aki-v3.2\python。那只作为**回退候选**保留在
  _pick_vs_root() 里（.venv 万一丢了才有用）。

本目录是自包含的播放器工作目录（mpv / vsmlrt / models / 脚本都在这里），
不依赖视频放大项目的任何路径。

为什么要单独一个：live.vpy 是给 mpv 加载的，出错时 mpv 只会说一句
"vapoursynth filter failed"，看不到根因。跑这个能把缺哪一块直接指出来。
"""
from __future__ import annotations

import os
import py_compile
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _pick_vs_py() -> Path:
    """.venv 优先（自包含），回退本机已装的 VapourSynth 解释器。

    ⚠ 返回的是 **python.exe 的完整路径**，不是 python 根目录 —— 调用方全部按
    文件用（`VS_PY.is_file()` / `subprocess.run([str(VS_PY), ...])`）。
    2026-10-02 修：原实现返回的是**目录**（`HERE/".venv"`），于是
    `VS_PY.is_file()` 恒为 False、"VapourSynth 解释器"永远报「找不到」、
    `VS_PY.parent / "Lib" / "site-packages"` 也退多了一层 —— **整套 VS 检查
    全废**（表现为 9 项 FAIL，但实际环境是好的）。
    """
    cands = [HERE / ".venv"]                     # ① 自包含
    # ② 兜底候选（.venv 丢了才有用）
    cands += [
        Path(r"D:\ComfyUI-aki-v3.2\python"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python313",
    ]
    for c in cands:
        exe = c / "Scripts" / "python.exe"
        if exe.is_file() and (c / "Lib" / "site-packages" / "vapoursynth").is_dir():
            return exe
    return cands[0] / "Scripts" / "python.exe"


VS_PY = _pick_vs_py()
VS_SP = VS_PY.parent.parent / "Lib" / "site-packages"    # python.exe → Scripts → venv 根
VS_ROOT = VS_SP / "vapoursynth"
MPV = HERE / "mpv" / "mpv.exe"
ENGINE_DIR = HERE / "models" / "sr"

OK, BAD, WARN = "[ OK ]", "[FAIL]", "[WARN]"
rows: list[tuple[str, str, str]] = []


def add(level: str, name: str, detail: str = "") -> None:
    rows.append((level, name, detail))


def link_test() -> tuple[str, str, str]:
    """真跑一次 mpv + 极简 VS 脚本，验证「mpv→VSScript→python→VapourSynth」整条链路。

    这是唯一能验证**环境变量拼对了**的办法：光看文件在不在不够 ——
    VSScript 找不到 python313.dll、PATH 里缺 vsscript.dll、脚本没调 set_output()，
    这三类错都只有真跑一次才暴露（本次就是它揪出了 video_in / set_output 两个坑）。

    全程 `--vo=null --no-hwdec`，纯 CPU，**不碰 CUDA**，可以放心跑。
    """
    import shutil
    import tempfile

    ff = shutil.which("ffmpeg")
    if not ff:
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:                           # noqa: BLE001
            ff = None
    if not ff:
        return (WARN, "链路实测", "没找到 ffmpeg，跳过（装 imageio-ffmpeg 或把 ffmpeg 放进 PATH）")
    if not MPV.is_file():
        return (WARN, "链路实测", "没 mpv，跳过")

    tmp = Path(tempfile.mkdtemp(prefix="livetest_"))
    try:
        mp4, vpy = tmp / "t.mp4", tmp / "t.vpy"
        subprocess.run(
            [ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10",
             "-t", "0.5", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "25", str(mp4)],
            check=True, timeout=180)
        vpy.write_text(
            "import vapoursynth as vs\n"
            "core = vs.core\n"
            "out = core.std.Levels(video_in, 0, 255, 1.0, 0, 235, [0, 1, 2])\n"
            "out.set_output(0)\n", encoding="utf-8")
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join([str(VS_ROOT), str(VS_PY.parent), env.get("PATH", "")])
        env["PYTHONPATH"] = str(VS_SP)
        r = subprocess.run(
            [str(MPV), "--no-config", "--no-hwdec", "--vo=null", "--ao=null",
             "--vf=vapoursynth=file=t.vpy", "--frames=4", "--untimed", str(mp4)],
            capture_output=True, text=True, errors="ignore", timeout=300,
            env=env, cwd=str(tmp))
        out = (r.stdout or "") + (r.stderr or "")
        if "Could not get script output node" in out:
            return (BAD, "链路实测",
                    "mpv 读不到脚本输出 → 脚本必须 video_in 输入 + VideoNode.set_output() 输出")
        if "could not init VS" in out or "Disabling filter vapoursynth" in out:
            first = next((l for l in out.splitlines() if "vapoursynth]" in l), "")[:120]
            return (BAD, "链路实测", f"VS 初始化失败：{first}")
        if r.returncode != 0:
            return (BAD, "链路实测", f"mpv 返回码 {r.returncode}")
        return (OK, "链路实测", "mpv→VSScript→python→VapourSynth 全通（纯 CPU 探针）")
    except Exception as e:                          # noqa: BLE001
        return (WARN, "链路实测", f"{type(e).__name__}: {str(e)[:100]}")
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)


# ---- 1. 解释器与 VapourSynth ----
if VS_PY.is_file():
    add(OK, "VapourSynth 解释器", str(VS_PY))
else:
    add(BAD, "VapourSynth 解释器", f"找不到 {VS_PY}")

if VS_ROOT.is_dir():
    add(OK, "vapoursynth 包", str(VS_ROOT))
else:
    add(BAD, "vapoursynth 包", f"找不到 {VS_ROOT}")

for dll, why in ((("vsscript.dll"), "mpv 挂 VS 靠它（关键）"),
                 (("vspipe.exe"), "命令行测速用"),
                 (("libvapoursynth.dll"), "VS 核心")):
    p = VS_ROOT / dll
    add(OK if p.is_file() else BAD, dll, why if not p.is_file() else "")

# ---- 2. VS 插件与核心命名空间 ----
if VS_PY.is_file():
    probe = (
        "import vapoursynth as vs\n"
        "c=vs.core\n"
        "print('VERSION', vs.__version__)\n"
        "for ns in ('trt',):\n"
        "    print('NS', ns, hasattr(c, ns))\n"
        "print('ORT', hasattr(c,'ort'))\n"
    )
    try:
        r = subprocess.run([str(VS_PY), "-c", probe], capture_output=True,
                           text=True, errors="ignore", timeout=120)
        out = r.stdout
        ver = next((l.split()[-1] for l in out.splitlines() if l.startswith("VERSION")), "?")
        add(OK, "VapourSynth 版本", ver)
        for ns in ("trt",):
            has = f"NS {ns} True" in out
            note = "" if has else f"core.{ns} 不可用"
            add(OK if has else BAD, f"core.{ns}", note)
        has_ort = "ORT True" in out
        add(WARN if not has_ort else OK, "core.ort",
            "不可用（本项目不走 ORT：补帧固定 TensorRT）" if not has_ort else "")
    except Exception as e:                          # noqa: BLE001
        add(BAD, "VS 运行时探测", f"{type(e).__name__}: {str(e)[:90]}")
else:
    add(BAD, "VS 运行时探测", "跳过（没解释器）")

# ---- 3. trtexec（决定 vsmlrt 的 TRT 路径能否走） ----
trtexec = VS_ROOT / "plugins" / "vsmlrt-cuda" / "trtexec.exe"
nv = (VS_ROOT / "plugins" / "vsmlrt-cuda" / "nvinfer_11.dll").is_file()
add(OK if nv else BAD, "TRT 运行库 nvinfer_11.dll",
    "" if nv else "vsmlrt-cuda 目录里没有它")
add(WARN if not trtexec.is_file() else OK, "trtexec.exe",
    "缺失（由 trtexec_py.py 用 TensorRT Python API 顶替，补帧/超分都照常）"
    if not trtexec.is_file() else "")

# ---- 3b. TRT 版本一致性（2026-10-02 新增，血的教训）----
# python 的 `tensorrt` 包 与 vsmlrt-cuda 的 nvinfer_11.dll **必须是同一版本**。
# 不一致时症状极其隐蔽：
#   · 超分照常（它只"加载"预编引擎，走 C++ 的 core.trt）
#   · **补帧崩**（RIFE 要现场编引擎，走 python 的 tensorrt API）——
#     报 `Assertion validateCaskKLibSize failed`，然后静默跳过补帧
#   · vstrt 只会打一句 "version mismatch ... fingers crossed"，很容易被忽略
# 两个 nvinfer_11.dll 同版本时文件大小相同（11.0=375,759,984 / 11.3=401,707,120），
# 所以比对大小就能发现被换错。
try:
    _pyv = "?"
    try:
        import tensorrt as _t
        _pyv = _t.__version__
    except Exception as _e:                                   # noqa: BLE001
        _pyv = f"import 失败({type(_e).__name__})"
    _nv_a = VS_ROOT / "plugins" / "vsmlrt-cuda" / "nvinfer_11.dll"
    _nv_b = VS_SP / "tensorrt_libs" / "nvinfer_11.dll"
    if _nv_a.is_file() and _nv_b.is_file():
        _sa, _sb = _nv_a.stat().st_size, _nv_b.stat().st_size
        if _sa == _sb:
            add(OK, "TRT 版本一致性", f"python tensorrt={_pyv}，两处 nvinfer 同版本")
        else:
            add(BAD, "TRT 版本一致性",
                f"python 包={_pyv}；vsmlrt-cuda 与 tensorrt_libs 的 nvinfer_11.dll "
                f"大小不同（{_sa} / {_sb}）⇒ **补帧会现场编引擎失败**。"
                f"修法：把两处都换成同一版本（本机锁 11.0.0.114）")
    else:
        add(WARN, "TRT 版本一致性",
            f"python={_pyv}；缺 tensorrt_libs/nvinfer_11.dll 或 vsmlrt-cuda 那份，跳过比对")
except Exception as _e:                                        # noqa: BLE001
    add(WARN, "TRT 版本一致性", f"检查异常：{type(_e).__name__}: {str(_e)[:80]}")

# ---- 4. mpv 与 VS 滤镜支持 ----
if MPV.is_file():
    add(OK, "mpv.exe", str(MPV))
    try:
        env = dict(os.environ)
        # mpv 加载 VSScript.dll 靠 PATH；python3xx.dll 也要能找到
        env["PATH"] = os.pathsep.join([str(VS_ROOT), str(VS_PY.parent),
                                       env.get("PATH", "")])
        r = subprocess.run([str(MPV), "--vf=help"], capture_output=True,
                           text=True, errors="ignore", timeout=60, env=env)
        txt = (r.stdout or "") + (r.stderr or "")
        name = None
        for cand in ("vapourynth", "vapoursynth"):
            if cand in txt:
                name = cand
                break
        if name:
            add(OK, "mpv VS 滤镜", f"滤镜名 = --vf={name}=xxx.vpy")
        else:
            add(BAD, "mpv VS 滤镜",
                "这个 mpv 构建里没有 vapoursynth 滤镜（--vf=help 里搜不到）")
    except Exception as e:                          # noqa: BLE001
        add(WARN, "mpv VS 滤镜", f"探测失败 {type(e).__name__}: {str(e)[:80]}")
else:
    add(BAD, "mpv.exe", f"找不到 {MPV}")

# ---- 5. 超分引擎档位 ----
# ⚠ 模式必须用 `fp16*_`（`*` 兼容 `_fp16_` 与 `_fp16io_`）—— 2026-10-02 修：
#   原模式写死 `_fp16_`，而实际引擎早已改名 `_fp16io_`，于是**一个都匹配不到**、
#   恒报「一个都没有」。
for tag, pat in (("animevideov3（4x）", "realesr-animevideov3_fp16*_*.engine"),
                 ("realesr-animevideov3_re2x（2x 备选）", "realesr-animevideov3_re2x_fp16*_*.engine")):
    files = sorted(ENGINE_DIR.glob(pat)) if ENGINE_DIR.is_dir() else []
    # 大于 100MB 的是「胖引擎」（TRT 序列化退化，载入慢一倍多），单独点出来
    fat = [f for f in files if f.stat().st_size > 100 * 2**20]
    detail = " ".join(f.stem.split("_fp16io_")[-1].split("_fp16_")[-1]
                      for f in files) or "一个都没有"
    if fat:
        detail += f"  ⚠ 胖引擎(>100MB) {[f.name for f in fat]}"
    add(OK if files else BAD, f"超分引擎 {tag}", detail)

# ---- 6. RIFE 模型 ----
rife = sorted((HERE / "models" / "rife").glob("rife_v*.onnx")) \
    if (HERE / "models" / "rife").is_dir() else []
if rife:
    for f in rife:
        mb = f.stat().st_size / 2**20
        add(OK, f"RIFE 模型 {f.name}", f"{mb:.1f} MB")
else:
    add(BAD, "RIFE 模型", "缺 tools/live/models/rife/rife_v4.26.onnx"
                          "（官方源：vs-mlrt release `external-models` 的 rife_v4.26.7z）")
extra = HERE / "models" / "rife_v2"
if extra.is_dir():
    add(WARN, "残留 rife_v2/",
        "7 通道版：本机 TRT 11.3 解析不了（fp16 后 /Mul 出现 Float/Half 混合）→ 只会用 impl1，这 407MB 可以删")

# ---- 7. live.vpy 语法 ----
try:
    py_compile.compile(str(HERE / "live.vpy"), doraise=True,
                       cfile=str(HERE / "__live_syntax_check.pyc"))
    add(OK, "live.vpy 语法", "通过")
    (HERE / "__live_syntax_check.pyc").unlink(missing_ok=True)
except Exception as e:                              # noqa: BLE001
    add(BAD, "live.vpy 语法", f"{type(e).__name__}: {str(e)[:150]}")

# ---- 8. 环境变量提示 ----
env_py = os.environ.get("PYTHONPATH", "")
add(OK if str(VS_SP) in env_py else WARN, "PYTHONPATH",
    "" if str(VS_SP) in env_py else f"建议含 {VS_SP}（VSScript 找 python 模块用）")

# ---- 9. 图形控制台那侧的解释器（2026-09-30 改：.venv 优先）----
def _pick_gui_py() -> Path:
    cands = [HERE / ".venv" / "python.exe"]
    la = os.environ.get("LOCALAPPDATA", "")
    cands += [Path(la) / "Programs" / "Python" / "Python310" / "python.exe",
              Path(la) / "Programs" / "Python" / "Python313" / "python.exe"]
    for c in cands:
        if c.is_file():
            return c
    return cands[0]


GUI_PY = _pick_gui_py()
if not GUI_PY.is_file():
    add(WARN, "图形控制台解释器",
        f"找不到 {GUI_PY} —— 启动控制台.bat 起不来（命令行方式不受影响）")
else:
    try:
        r = subprocess.run(
            [str(GUI_PY), "-c",
             "import PySide6, sys; print(PySide6.__version__, sys.version.split()[0])"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = r.stdout.decode("utf-8", "replace").strip().splitlines()
        last = lines[-1] if lines else ""
        if r.returncode == 0 and " " in last:
            ver, pyv = last.rsplit(" ", 1)
            add(OK, "PySide6（图形控制台）", f"{ver} / Python {pyv}")
        else:
            add(WARN, "PySide6（图形控制台）", f"导入失败：{last[:70]}")
    except Exception as e:
        add(WARN, "PySide6（图形控制台）", f"{type(e).__name__}: {str(e)[:60]}")

for f in ("live_gui.py", "启动控制台.bat"):
    has = (HERE / f).is_file()
    add(OK if has else WARN, f, "" if has else "缺失（图形方式不可用，命令行方式仍可用）")

# ---- 10. 中文界面资源（--config-dir 指过去的 mpv_cn/）----
CN = HERE / "mpv_cn"
CN_FILES = {
    "input.conf": "中文键位/中文 OSD 提示",
    "menu.conf": "中文右键菜单条目",
    "scripts/stats_zh.lua": "中文统计页（按 i）",
    "scripts/context_menu.lua": "右键菜单脚本（本机构建未内置，需自己补）",
}
missing_cn = [k for k in CN_FILES if not (CN / k).is_file()]
if not CN.is_dir():
    add(WARN, "中文界面目录 mpv_cn/",
        "缺失 —— 播放器将是英文界面（统计页/右键菜单/按键提示）")
elif missing_cn:
    add(WARN, "中文界面资源",
        "缺 " + "、".join(f"{k}（{CN_FILES[k]}）" for k in missing_cn))
else:
    add(OK, "中文界面资源",
        f"{len(CN_FILES)} 项齐全（统计页/右键菜单/按键提示）")

# ---- 11. 可选：真跑一次 mpv+VS 链路（--link）----
# 文件在不在只是必要条件；环境变量拼错、脚本接口用错，只有真跑一次才暴露。
if "--link" in sys.argv or "-l" in sys.argv:
    add(*link_test())

# ---------------------------------------------------------------- 输出
width = max(len(n) for _, n, _ in rows) + 2
n_bad = sum(1 for lv, _, _ in rows if lv == BAD)
n_warn = sum(1 for lv, _, _ in rows if lv == WARN)
print("=" * 78)
print("实时播放器前置自检（未调用 GPU）")
print("=" * 78)
for lv, name, detail in rows:
    print(f"{lv} {name:<{width}} {detail}")
print("-" * 78)
print(f"结论：{len(rows) - n_bad - n_warn} 项通过 / {n_warn} 项警告 / {n_bad} 项失败")
if n_bad:
    print("有 FAIL 项 → 现在跑 mpv 不会成功，先按上面提示补齐。")
elif n_warn:
    print("没有阻塞项，但要留意警告（不影响先跑起来）。")
else:
    print("全绿。可以直接跑 live_play.bat。")
sys.exit(1 if n_bad else 0)
