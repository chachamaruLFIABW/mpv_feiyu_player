@echo off
chcp 936 >nul
setlocal enableextensions

rem ============================================================
rem  mpv 实时超分 + 补帧 播放器
rem
rem  用法：把视频文件拖到本 bat 上松开即可。
rem        双击运行会给出提示（本脚本不做文件选择，避免中文路径编码问题）。
rem
rem  参数都在下面的 set 里改，改完直接双击/拖拽生效。
rem ============================================================

set "HERE=%~dp0"
rem 本目录自包含：mpv\ 里是播放器，models\ 里是超分/RIFE 模型，vsmlrt\ 是图构建脚本
set "MPV=%HERE%mpv\mpv.exe"
set "SCRIPT=%HERE%live.vpy"

rem --- VapourSynth 位置：本目录 .venv（自带 Python 3.13 + VS R77 + 全部依赖）---
rem   ★ 2026-09-30 起改为**目录内 venv**，整个文件夹拷到别的电脑就能直接跑。
rem     旧写法指向 D:\ComfyUI-aki-v3.2\python，换台机器就断。
rem   ★ 策略：**自带优先，本机兜底** ——
rem     ① 本目录 .venv 存在 → 用它（永远首选，版本锁定，不受目标机影响）
rem     ② .venv 不在 → 回退到常见的系统 VS 安装位置（有就用）
rem     ③ 都没有 → 下面依赖检查会明确报出来，而不是让 mpv 静默禁掉滤镜
set "VENV=%HERE%.venv"
if exist "%VENV%\python.exe" goto :use_venv

rem —— 回退：找本机已装的 VapourSynth（按可能性排序）——
for %%D in (
    "D:\ComfyUI-aki-v3.2\python"
    "%LOCALAPPDATA%\Programs\Python\Python313"
    "%LOCALAPPDATA%\Programs\Python\Python312"
    "C:\Python313"
) do (
    if exist "%%~D\Lib\site-packages\vapoursynth\vsscript.dll" (
        set "VENV=%%~D"
        goto :use_venv
    )
)

rem —— 都没找到：给出人话提示 ——
echo [X] 找不到 VapourSynth 运行时
echo     期望位置（自带）: %HERE%.venv\
echo     回退位置（本机）: D:\ComfyUI-aki-v3.2\python 等
echo.
echo     本播放器需要 VapourSynth 才能工作。若 .venv 被删了，
echo     请重新解压完整包；若想用本机已装的 Python，请装：
echo       pip install vapoursynth
pause
exit /b 1

:use_venv
set "VSPY=%VENV%"
set "VSSP=%VSPY%\Lib\site-packages"
set "VSROOT=%VSSP%\vapoursynth"

rem --- mpv 靠 PATH 找 VSScript.dll；python313.dll 也必须在 PATH 里 ---
rem   ★ 两个目录都要进 PATH：VSROOT 有 vsscript.dll，VSPY 有 python313.dll
set "PATH=%VSROOT%;%VSPY%;%VSPY%\Scripts;%PATH%"
set "PYTHONPATH=%VSSP%"

rem ============================================================
rem  可调参数
rem ============================================================

rem 超分：1 开 / 0 关
set "LIVE_UPSCALE=1"
rem 超分模型，可选：
rem   animev3                              realesr-animevideov3 4x（默认，画质优先）
rem   realesr-animevideov3_re2x            自训 2x（快 2 倍）
rem   AnimeJaNai_HD_V3.1_Performance       AnimeJaNai 2x，最快
rem   AnimeJaNai_HD_V3.1Sharp1_Performance 同上 + 加锐
rem   AnimeJaNai_HD_V3.1_Balanced          AnimeJaNai 2x，质量档（推荐）
rem   AnimeJaNai_HD_V3.1Sharp1_Balanced    质量档 + 加锐
rem   （AnimeJaNai 系要先用 build_aj_engines.py 编引擎，见 models/sr_aj/）
rem
rem   ★★ Anime4KCPP 系列（VS 原生插件，**不是 TRT 引擎**）：写成 anime4kcpp:<模型名>。
rem     实测同输出尺寸比 TRT 快 5.8~9.3 倍（YUV 直通，零 float 往返）；
rem     代价是**没有原生 4x**（全是 2x 网络）。全部 12 个模型（按速度排）：
rem       anime4kcpp:acnet-f8b4      VGG，最省
rem       anime4kcpp:fsrcnnx-f8      经典 FSRCNNX，最省
rem       anime4kcpp:acnet-hdn0/1/2/3  降噪递增（hdn3 最强）
rem       anime4kcpp:acnet-gan      官方默认，细节增强
rem       anime4kcpp:acnet-f8b8/f8b18  VGG，中/重
rem       anime4kcpp:artcnn-c4f16   轻量 CNN
rem       anime4kcpp:arnet-f8b8     ResNet 风格（稍慢）
rem       anime4kcpp:acnet-legacy-gan 旧版权重
rem     插件位置：.venv\Lib\site-packagesapoursynth\pluginsnime4kcpp.dll
set "LIVE_SR=animev3"
rem ★ 限定播放分辨率（最终输出的短边上限）。0 = 不限。
rem   解决的问题：4x 模型会把 720p 源超成 5120x2880，比屏幕还大 ——
rem     多出来的像素全要过补帧和输出搬运，纯属白烧。
rem   限住之后：超分照旧按档位推理 -> 缩到上限尺寸 -> **补帧在这个尺寸上做**
rem     （缩在超分之后、补帧之前，省的正是最贵的那一步）。
rem   ? 只管上限：源比它小就按源的来，**绝不放大**。
rem   [!] 2026-09-30：输出尺寸 = **源 x 模型原生倍率**（animev3=4x，
rem       其余 2x）再受本项压一次。原来还有个 LIVE_OUT_SCALE
rem       “输出倍率”，已删 —— 它改不了推理倍率也改不了选档
rem       （pick_ladder 只看源尺寸），和本项职责重叠。
rem       想“只清洗不放大”（1080p 源 + 2x 模型仍出 1080p）
rem       -> 把本项设成 1080 即可（比原来的倍率 x1 更直观）。
rem   ? 它管**最终输出尺寸**；管超分**推理档位**的是下面的 LIVE_SR_MAX。
rem   常用值：1440（2560x1440 屏）/ 1080 / 720。
set "LIVE_PLAY_MAX_H=0"
rem 判据边：short = 按短边（默认）/ long = 按长边。
rem   “我想限成 1920 宽”用 long：
rem     3840x2160 限 1920 -> short 给 3414x1920，long 给 1920x1080。
set "LIVE_PLAY_MAX_EDGE=short"
rem 超分**导入**分辨率上限（推理档位的短边像素）。0 = 不限（画质优先）。
rem   ★ 降本开关：超分成本 ~正比档位像素数。AnimeJaNai Balanced 隔离实测：
rem     960x720  档  13.1ms   （1080p 源配 LIVE_SR_MAX=960 会落到这档）
rem     1280x720 档  26.6ms
rem     1920x1080 档 26.6ms（出图 3840x2160，1080p 源的默认档）
rem   ? 但必须认清：**降档救不活 1080p 的"超分+补帧"全链路**。
rem     ★★ 这一条是 60 秒长窗真播量出来的（70s 素材，后半段窗）：
rem        只超分（SR_MAX=960 + 输出 x1） → 输出 24.0fps  丢帧   0 帧  OK
rem        只补帧 @48                     → 输出 48.0fps  丢帧   0 帧  OK
rem        超分+补帧同开（同口径 @48）     → 输出 48.0fps  丢帧 503 帧  X 9.0/s
rem     注意第三行的阴险点：estimated-vf-fps **照样报 48.0**（滤镜吞吐达标），
rem     但 VO 一直丢帧保同步 —— 只看帧率会误判成"能跑"。**必须看丢帧计数。**
rem   ★ 1080p 要稳：**超分 / 补帧 二选一**。
rem     只超分：LIVE_INTERP=0    只补帧：LIVE_UPSCALE=0
rem   ! 别用 12~14 秒的短窗下结论：同一临界配置短窗能出 0/0/1/1 看着很稳，
rem     长窗才暴露持续丢帧。要判稳，至少 60 秒。
rem   720p 源两者同开毫无压力（0 丢帧），这个开关设不设都一样。
set "LIVE_SR_MAX=0"

rem 处理顺序：auto / hr（先超分后补帧）/ lr（先补帧后超分）
rem   auto 规则：源宽 >=1280 且两者都开 -> lr，并自动把超分模型切成 realesr-animevideov3_re2x
rem   为什么高分辨率要用 lr：RIFE 在大尺寸上贵得离谱（720p 每对 12ms，1440p 每对 53ms），
rem   而 realesr-animevideov3_re2x 便宜到可以多跑一倍次数。实测 720p 源：hr 29.9ms(33fps) vs lr 18.0ms(56fps)
rem   注意：lr 必须配 realesr-animevideov3_re2x；配 animevideov3 反而更慢（4x 中间步骤要做两倍次数）
set "LIVE_ORDER=auto"

rem 补帧：1 开 / 0 关
set "LIVE_INTERP=1"
rem 补帧档位【源帧率 x n】。默认 auto = 源 >=60fps 取 2x；否则取能塞进 60
rem   的最大整数倍（24->2x=48、30->2x=60、25->2x=50）。
rem   ★ 为什么用「源 x n」而不是绝对 fps：源不一定是 24fps。旧档位（48/60/72…）
rem     碰到 25fps 源会算出 1.92x（非整数，多调一次 RIFE）；碰到 50fps 源算出
rem     0.96x < 1 -> 直接跳过补帧，等于白选。倍率档跟源无关，任何源都不会错。
rem   取值：
rem     auto     自动倍数（同上）
rem     -2/-3/-4 固定 2x/3x/4x（主力档，与源帧率无关）
rem     60/144   绝对目标帧率（对齐显示器刷新率才用）
rem   ★ 为什么优先整数倍：每对输入帧调用 RIFE 的次数 = ceil(倍率) - 1
rem     24->60 是 2.5x 要调 2 次；24->48 是 2x 只调 1 次。
rem     多 100% 成本只多 25% 帧。
set "LIVE_INTERP_FPS=auto"
rem 相似帧跳过：与上一帧的平均绝对差低于它就跳过 RIFE、直接用源帧。
rem   0 = 关闭。实拍/番剧里大量静止帧，收益很大（mpv-lazy 的 turbo=2 就是这个）。
rem   播放时日志会打「相似帧：x/y（z%）跳过推理」，按它调大/调小。
set "LIVE_STATIC_TH=0.003"
rem 想按倍率指定：把上面设成 0，再用这个（0 = 不用倍率）。
set "LIVE_INTERP_MULTI=0"
rem 补帧**引擎**（★ 2026-09-30 加）—— 换的是算法，不是模型版本：
rem   mv     = mvtools 传统光流补帧（**默认**：纯 CPU，零显存、零 TRT 依赖）
rem              720p 约 1.9 ms/帧（pel=1 blk=32）。画质弱于 AI（遮挡处有块状撕裂）。
rem              ★ 为什么默认是它：只需要 vapoursynth 自带的 mvtools.dll，
rem                **不依赖 TensorRT** —— 换台机器（甚至没 N 卡）都能跑。
rem              ★ 价值：1080p「超分 + 补帧」同开时 GPU 算力不够，
rem                把补帧挪到 CPU 就能**两边都要** —— 实测 60 秒窗 0 丢帧
rem                （AnimeJaNai Balanced / Performance，SR_MAX=960，输出 x1）。
rem              ? re2x 超分 + 补帧仍然挂（它是 4x 引擎，1080p 档中间步骤
rem                要做 3840x2160，60 秒窗丢 1684 帧）→ 要全都要就换 2x 模型。
rem   rife   = vsmlrt 的 C++ TensorRT 版 RIFE（最快，画质最好，但要 TRT + 吃显存）
rem              720p 约 3.5 ms/帧，GPU 推理。缺点：占显存，
rem              且**平移镜头下线条会抖/出伪影**（社区已知问题）。
rem              ★ 要求：本机（或 .venv 里）有可用的 TensorRT 运行时。
rem                没有的话会自动降级到 mv，并在日志里说明 —— 不会崩。
rem   dup    = 帧复制（每帧原地重复一次，**不做任何插值**）
rem              720p 实测 0.35 ms/帧，零推理、零显存、零插件（纯 std 滤镜）。
rem              画面 = 24fps 的观感塞进 48fps 容器，运动是**顿挫**的（"假 48fps"）。
rem              用途：排障/对照 —— 拿来验证"丢帧到底是不是补帧算力不够"。
rem              ? 只支持整数倍率；非整数会**跳过补帧**并警告（不报错、不崩）。
rem   blend  = 帧混合（相邻两帧各取 50% 线性混合出一张中间帧）
rem              720p 实测 0.31 ms/帧。比 dup 略顺滑，但运动物体**处处半透明重影**。
rem              ? 目前只实现 2x；其它倍率会跳过补帧并警告。
set "LIVE_VFI=mv"
rem mvtools 档位（1280x720 ×2 整链实测 ms/出帧，2026-09-30 重测）：
rem   fast = pel1 blk32 无重叠 1.59  ← 默认，最省
rem   mid  = pel1 blk16 ov4    5.91  ← 慢 3.7 倍
rem   best = pel2 blk16 ov8   11.73  ← 慢 7.4 倍（画质档）
set "LIVE_MV_PRESET=fast"

rem ★ mv 内部线程数 = mvtools 的  参数。
rem   **这是 mv 补帧唯一有效的加速旋钮** —— 别去碰 LIVE_VS_THREADS，
rem   那是 VS 图级线程，对 mv 无效（mvtools 内部自开线程池，
rem   实测 core.num_threads 1->32 总耗时只差 18%）。
rem   库默认 -1（自动）偏保守：仅 Analyse 一级 2.32ms -> opt=4 时 1.51ms
rem   （快 35%）；整链被串行的 BlockFPS 摊薄到 1.63 -> 1.59ms。
rem   超过 ~12 无收益甚至回退（本机物理核只有 12 个）。
set "LIVE_MV_OPT=4"

rem 4K 自适应 = 源宽 >=2560 时 mv 的 blksize 翻倍（上限 64）。
rem   实测（3840x2160 x2 仅补帧）：43.4fps（不够 48，掉帧）-> 58.5fps。
rem   [!] 曾同时关色度多拿 4%，但那会让整个画面变绿
rem       （mvtools 的 chroma=False = 色度平面填 0），已撤销。
rem   不想要自适应就改成 0。
set "LIVE_MV_HD=1"

rem ===== 精确 seek 音画错位（2026-10-01 修）=====
rem GUI 会给 mpv 加 --hr-seek-framedrop=no。mpv 默认是 yes ——
rem   精确 seek 时靠丢帧加速，但在 vapoursynth 滤镜场景下会让 mpv 的时钟
rem   (out_pts) 与画面真实内容脱节，偏移约 5 秒且恒定；而 frame-drop-count
rem   和 avsync 全程都是 0/0.0000，mpv 自己完全看不见。
rem   症状：单击进度条后画面比声音快好几秒。
rem 想还原 mpv 默认（会重新出现错位）：
rem set "LIVE_HRSEEK_FRAMEDROP=1"

rem ===== 音频侧排查（2026-09-30 加）=====
rem 音频缓冲：mpv 默认 0.2，本脚本历史上用 0.6。mpv 官方文档就该选项写着
rem   "This option should be used for testing only." —— 它会在设备缓冲之外
rem   再加一层软件缓冲；这层若没被完整算进音频时钟，画面就会整体跑到声音
rem   前面（而 avsync 全程显示 0，自己跟自己比看不出来）。
rem   ★ 2026-10-01 默认值已改回 mpv 自己的 0.2（原 0.6 会让声音更晚）。
rem set "LIVE_AUDIO_BUFFER=0.2"

rem 音频延迟微调（秒，可负）：按耳朵校准用。正 = 推迟音频；
rem   "画面快过音频"（声音迟到）就给负值，例如 -0.2。
rem set "LIVE_AUDIO_DELAY=-0.2"

rem 换音频输出设备（排查蓝牙延迟用）。取值抄 `mpv --audio-device=help`：
rem   wasapi/{6563f293-a289-453e-8d23-098a042b5d87}  扬声器(Realtek 有线)
rem   wasapi/{52212adc-a907-438e-81a6-8c7c17e2f70e}  XV272U(HDMI)
rem   wasapi/{c02d2aec-0eef-4d2f-b359-2e05a62de72e}  扬声器(BT66 蓝牙，当前默认)
rem 若换成有线后错位消失 -> 就是蓝牙渲染延迟，用上面的 LIVE_AUDIO_DELAY 补掉。
rem set "LIVE_AUDIO_DEVICE=wasapi/{6563f293-a289-453e-8d23-098a042b5d87}"

rem [诊断] 把「画面内容自己的时间戳」烧在左上角（单位：秒）。
rem   用法：开它，播放时冠定在一个画面上，看屏幕**右下角的时间**与
rem         画面左上角的秒数是否同步。两者差多少 = 真实音画差。
rem   为什么靠它：time-pos / audio-pts / avsync 全是 mpv 自己累加的数，
rem         内容错位它们看不见。
rem   代价：略增 CPU（每帧画一次字），排查完记得注掉。
rem set "LIVE_SHOW_TC=1"

rem 补帧后端固定 TensorRT（不给选项）：ncnn 跑不了 RIFE —— 它依赖 GridSample，
rem   ncnn 会报 "GridSample not supported yet!"，图能建、fps 也对，但推理是废的。
rem   本机缺的 trtexec.exe 由 trtexec_py.py 用 TensorRT Python API 顶替。
rem   ★ 只在 LIVE_VFI=rife 时有意义。
rem   ★ 2026-09-30 清理后**本目录只保留两个 RIFE 模型**：
rem       4.26（22MB，默认）/ 4.25_lite（22MB）。其余版本（4.0~4.10、rife_v2）
rem       及其 TRT 引擎缓存全部删除（省了 6 GB 磁盘）。
rem     想用别的版本：把 rife_v<VER>.onnx 放回 models/rife/ 即可。
rem   ? 版本之间**性能几乎没差**（都在 3.2~4.4ms @1080p）。
set "LIVE_RIFE_VER=4.26"

rem ★ 曾有重复的「补帧引擎选择」段落（还写着 vsrife 残留说明）—— 2026-09-30
rem   已删除，引擎选择统一在**上面 LIVE_VFI 那一处**（本文件只应定义一次）。
rem   ★ 曾有 vsrife 引擎（PyTorch 版 RIFE）：TRT 路径本机编译必失败，
rem     非 TRT 只是把 RIFE 换个壳重跑（720p 15.5ms vs 3.5ms，慢 4.4 倍）
rem     且画质没变好 —— 没有任何独有能力，已连同 LIVE_VSRIFE_* 变量一并删除。
rem   ★ 曾有 live.vpy 里的 impl=2（7 通道 rife_v2 模型）：本机 TRT 11 解析不了
rem     它的混合精度 /Mul，模型与分支已一并删除。

rem GPU 编号 / 推理并发流
set "LIVE_DEVICE=0"
rem 补帧用 fp16 I/O（★ 实测最大的一项优化：搬运量砍半，1080p @60 从 0.64x 提到 0.99x）
set "LIVE_FP16_IO=1"
rem 输出像素格式：auto = 8bit 源用 yuv420p8、否则 yuv420p16
rem   （1080p @60 实测 yuv420p8 比 yuv420p16 快约 14%，因为写入字节减半）
rem   ? 输出**必须**是 YUV：mpv 的 vapoursynth 滤镜只认 YUV（RGB 一律报
rem     Unsupported output format 并**禁用整条滤镜**，会退化成放源码流）。
rem     曾经的 LIVE_RAW_OUT 开关因此已删除。
set "LIVE_OUT_FMT=auto"

rem 推理并发流。默认 2 —— 实测 1080p 仅补帧 @60：1 流 0.99x → 2 流 1.09x
set "LIVE_STREAMS=2"

rem 色彩矩阵（动漫基本是 709）/ 是否打印链路日志
set "LIVE_MATRIX=709"
set "LIVE_VERBOSE=1"

rem mpv 里 VapourSynth 滤镜的名字（本机实测是 vapoursynth，由 selfcheck 确认）
set "VFILTER=vapoursynth"
rem 滤镜参数：脚本一律用相对路径 —— 绝对路径里的盘符 ":" 会被 --vf 当成参数分隔符
set "VFLIVE=live.vpy"
rem concurrent-frames: 流水线深度（VS 能提前算几帧）。★ 实测必须是 3：
rem   给 2 时输出帧率显示 48fps 但每秒丢 20 帧（实际只显示 27fps）——
rem   不是算得慢，是单帧延迟超过 20.83ms 的帧间隔、mpv 判定"迟到"就丢了。
rem   给 3 缓冲就够，实测稳态丢帧 0.0/s。显存吃紧时可以试 2。
set "VFOPTS=concurrent-frames=6"

rem ============================================================
rem  依赖检查
rem ============================================================

if not exist "%MPV%" (
    echo [X] 找不到 mpv.exe
    echo     期望位置: %MPV%
    pause
    exit /b 1
)

if not exist "%VSROOT%\vsscript.dll" (
    echo [X] 找不到 vsscript.dll
    echo     期望位置: %VSROOT%\vsscript.dll
    echo     mpv 靠它加载 VapourSynth，缺这个一定跑不起来。
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo [X] 找不到 live.vpy
    echo     期望位置: %SCRIPT%
    pause
    exit /b 1
)

if "%~1"=="" goto :no_input

set "TARGET=%~1"
goto :run

:no_input
echo.
echo ============================================================
echo  请把视频文件拖到本 bat 上再松开。
echo.
echo  想先体检环境就把下面这行复制到 cmd 里跑：
echo    %VSPY%\python.exe "%HERE%selfcheck.py"
echo ============================================================
echo.
pause
exit /b 0

:run
rem 必须切到本目录：--vf 里传的是相对路径 live.vpy
cd /d "%HERE%"

echo.
echo ============================================================
echo  mpv 实时超分 + 补帧
echo    超分      : %LIVE_SR%  倍率 x%LIVE_OUT_SCALE%  播放上限 %LIVE_PLAY_MAX_H%
echo    补帧      : %LIVE_INTERP%  目标 %LIVE_INTERP_FPS%  相似帧阈值 %LIVE_STATIC_TH%  后端 trt
echo    输入      : %TARGET%
echo ============================================================
echo  首次加载要建 VapourSynth 图并载入模型，可能黑屏几秒，正常。
echo    开头几帧可能是黑的（mpv 建图期间的占位帧），随引擎不同：
echo      mv 最重（Super+2次Analyse）会黑前 3~4 帧，blend 黑 1 帧，dup 不黑。
echo    这是 mpv 的行为，不是补帧出错；正片从第几帧起就完全正常。
echo  处理跟不上时 mpv 会自动丢帧保音画同步。
echo  链路日志以 [live] 开头，会打印在这个窗口里。
echo  seek 之后要重建 VapourSynth 图并重载模型，会卡几百毫秒到几秒，正常。
echo.

rem ★ 用 --config-dir 加载中文界面资源（input.conf / menu.conf / scripts）
rem   注意：不能再用 --no-config —— 它会连 config-dir 里的配置和脚本一起禁掉
"%MPV%" --config-dir="%HERE%mpv_cn" ^
        --vf=%VFILTER%=file=%VFLIVE%:%VFOPTS% ^
        --cache=yes ^
        --demuxer-max-bytes=512MiB ^
        --demuxer-readahead-secs=30 ^
        --audio-buffer=0.6 ^
        --framedrop=vo ^
        --force-window=yes ^
        --title="live - %~nx1" ^
        "%TARGET%"

set "RC=%ERRORLEVEL%"
echo.
echo mpv 退出，返回码 %RC%
if not "%RC%"=="0" echo 返回码非 0 时，请把窗口里的 [live] 日志贴出来。
pause
exit /b %RC%
