# mpv-live · 边看边超分 / 边看边补帧

给 mpv 挂 VapourSynth，在**播放的同时**做 AI 超分和帧插值 —— 不用先转码、不用等。
带一个中文图形界面，改参数即时生效。

> 界面截图见仓库的 Releases 页，或直接运行看一眼（左侧播放队列 + 右侧参数面板，
> 播放队列下面就是日志区）。

## 能做什么

| 能力 | 走哪条路 |
|---|---|
| **超分** | TensorRT 预编译引擎（`realesr-animevideov3` / `AnimeJaNai` 系）或 **Anime4KCPP** 的 VapourSynth 插件（CNN 系，12 个模型） |
| **补帧** | **RIFE**（vsmlrt + TensorRT，画质最好）/ **mvtools**（纯 CPU 光流，不占显存）/ 帧复制 / 帧混合 |
| **顺序** | 可指定「先补帧再超分」或「先超分再补帧」，也可让程序按分辨率自动选 |
| **尺寸** | 「限定播放分辨率」控制最终输出尺寸；「导入分辨率上限」控制超分推理档位 |
| **中文环境** | `mpv_cn/` 里是中文 OSD、中文右键菜单、中文字幕/统计页 |

GUI 里每个选项都带 tooltip 说明权衡，预估卡会列出当前配置最终会跑成什么样。

## 依赖

### 1. Python 侧

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 手动准备的三样东西（不是 pip 包）

| 放到哪 | 是什么 | 要不要 |
|---|---|---|
| `mpv\mpv.exe` | mpv 播放器本体（选 **vapoursynth 启用**的构建） | 必需 |
| `.venv\Lib\site-packages\vapoursynth\plugins\vstrt.dll` | VapourSynth 的 **TensorRT** 插件，提供 `core.trt` | 用 TRT 超分/补帧就需要 |
| `.venv\Lib\site-packages\vapoursynth\plugins\anime4kcpp.dll` | **Anime4KCPP** 插件，提供 `core.anime4kcpp` | 用 Anime4KCPP 路线才需要 |

装好后跑一次**环境自检**（GUI 里的按钮，或 `python selfcheck.py`），它会逐项检查
mpv / VSScript / 插件 / 引擎 / 模型，缺什么直接告诉你。

### 3. 显卡

TensorRT 路线需要 NVIDIA 显卡 + 匹配的 CUDA / 驱动。
**没有 N 卡也能用**：超分切到 Anime4KCPP（它支持 CPU / OpenCL / CUDA），补帧用 mvtools 或帧复制。

## 快速开始

```bat
:: 方式一：启动图形界面
启动控制台.bat

:: 方式二：直接播放（改 bat 里的路径即可）
live_play.bat  "D:\你的片子.mkv"
```

拖视频或文件夹进窗口 → 选好超分/补帧 → 点「开始播放」。
播放中按 **`n`** 可以随时看输出尺寸 / 实际帧率 / 丢帧数。

## 模型

`models/` 里已经带上了 **ONNX 权重**，开箱可用：

```
models/
├─ sr_onnx/       realesr-animevideov3（4x 原生）与 re2x（自训 2x）
├─ animejanai/    AnimeJaNai HD V3.1 四个档（Performance / Balanced × 是否加锐）
└─ rife/          RIFE 4.26 与 4.25 lite（补帧）
```

### TensorRT 引擎要自己编

`.engine` **不在仓库里** —— 它是本机编译产物，绑死 GPU 架构（sm_xx）和 TensorRT 版本，
换台机器必然要重编。两个脚本会自动扫 `models/` 并编译：

```bat
.venv\Scripts\python.exe build_fp16io_engines.py     :: realesr 系
.venv\Scripts\python.exe build_aj_engines.py         :: AnimeJaNai 系
```

首次编译比较慢（每个分辨率档位都要编一份），编完会缓存，之后秒开。

> 懒得编译的话：超分引擎切到 **Anime4KCPP**，它不需要 `.engine`，开箱即用。

## 目录结构

```
live_gui.py                图形界面主程序（PySide6）
live.vpy                   VapourSynth 滤镜：超分 / 补帧的全部实现
live_play.bat              命令行启动器（直接调 mpv + live.vpy）
启动控制台.bat             双击启动 GUI
live_static.py             相似帧检测（让 RIFE 跳过静态帧）
vsmlrt/                    本地化的 vsmlrt 库（RIFE 的 VS 封装）
trtexec_py.py              TRT 引擎构建辅助
build_*_engines.py         引擎编译脚本
selfcheck.py               环境自检
mpv_cn/                    中文 mpv 配置：OSD / 右键菜单 / 统计页 / 快捷键
models/                    ONNX 权重（.engine 需自行编译）
tests/                     回归测试（见下）
```

## 已知限制

- **1080p 上「超分 + 补帧」同开很可能供不上**（GPU 吞吐不够，表现为画面一跳一跳、
  音画错位）。程序检测到这种情况会**自动让路**（换成先超分再补帧 + 把补帧工作尺寸
  压回源尺寸），但更稳的做法是**二选一**。720p 源同开没有压力。
- 超分模型是**固定倍率**，和导出分辨率无关；`AnimeJaNai` 全系和 Anime4KCPP 都是 **2x**
  （Anime4KCPP 没有原生 4x 模型，选 3x/4x 时它只做 2x、剩下的靠 bicubic）。
- 拖进度条用**拖动**而不是单击：单击走的是精确 seek，会多解码一段（慢），
  而且 OSC 单击/拖动的落点精度不同。

## 测试

```bat
cd tests
..\.venv\Scripts\python.exe _t_gui_smoke.py       :: GUI 配置/联动/布局 回归
..\.venv\Scripts\python.exe _t_portable.py        :: 可移植性（路径/依赖）检查
..\.venv\Scripts\python.exe _t_fpsmatrix.py       :: 补帧倍率矩阵
..\.venv\Scripts\python.exe _t_play_cap.py        :: 限定播放分辨率（需 mpv）
..\.venv\Scripts\python.exe _t_a4k_ui.py          :: 超分引擎切换 + 布局
..\.venv\Scripts\python.exe _t_fs_check.py        :: 全屏开关（需 mpv）
..\.venv\Scripts\python.exe _t_playcap_parity.py  :: VS 与 GUI 的尺寸语义一致性
```

`_t_gui_smoke.py` 是主回归，改完 GUI 先跑它。

## 致谢

[mpv](https://mpv.io/) ·
[VapourSynth](https://www.vapoursynth.com/) ·
[vs-mlrt](https://github.com/AmusementClub/vs-mlrt) ·
[Anime4KCPP](https://github.com/TianZerL/Anime4KCPP) ·
[RIFE](https://github.com/hzwer/ECCV2022-RIFE) ·
[AnimeJaNai](https://github.com/the-database/mpv-upscale-2x_animejanai) ·
[Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN)
