# -*- coding: utf-8 -*-
"""live_gui.py 的离屏冒烟测试（不启动 mpv、不碰 CUDA、不写真配置）。

跑法：
    <Python310>\\python.exe _t_gui_smoke.py
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(ROOT))

FAILS = []
STAT = {"ok": 0}


def ck(cond, name, extra=""):
    print(("  [OK] " if cond else "  [X ] ") + name + (f"   {extra}" if extra else ""))
    if cond:
        STAT["ok"] += 1
    else:
        FAILS.append(name)


import live_gui as G                                    # noqa: E402
from PySide6.QtCore import Qt                           # noqa: E402
from PySide6.QtWidgets import QApplication, QListWidgetItem   # noqa: E402

# ═════════════════════════════════════════════════ 1 行分割器
print("== 1 输出行分割器 ==")
s = G.LineSplitter()
ck(s.feed(b"a\nb\n") == [("a", False), ("b", False)], "普通 \\n 两行")
s = G.LineSplitter()
ck(s.feed(b"x\ry\r") == [("x", True), ("y", True)], "单独 \\r = 覆盖当前行")
s = G.LineSplitter()
ck(s.feed(b"p\r\nq\r\n") == [("p", False), ("q", False)], "\\r\\n = 新行")
s = G.LineSplitter()
ck(s.feed(b"he") == [], "不完整片段先暂存")
ck(s.feed(b"llo\n") == [("hello", False)], "跨块拼接正确")
s = G.LineSplitter()
ck(s.feed(b"a\r") == [("a", True)], "块尾 \\r 立刻生效")
ck(s.feed(b"b\n") == [("b", False)], "下一块的 b 是新行（没被并进 a）")
s = G.LineSplitter()
ck(s.feed("中文\n".encode()) == [("中文", False)], "UTF-8 中文")

# ═════════════════════════════════════════════════ 2 命令行组装
print("== 2 命令行与环境变量组装 ==")
cfg = dict(G.DEFAULTS)
env, args = G.build_launch(cfg, ["X:/a.mkv"], r"\\.\pipe\test")
ck(env["LIVE_ORDER"] == "auto" and env["LIVE_SR"] == "animev3"
   and env["LIVE_INTERP"] == "1" and env["LIVE_UPSCALE"] == "1",
   "环境变量按配置生成", f"{env['LIVE_ORDER']}/{env['LIVE_SR']}")
ck("--vf=vapoursynth=file=live.vpy:concurrent-frames=6" in args,
   "--vf 用相对路径 + file= 参数名", )
ck(args[-1] == "X:/a.mkv" and args.count("X:/a.mkv") == 1, "文件排在最后")
ck(any(a.startswith("--config-dir=") for a in args)
   and "--framedrop=vo" in args, "关键 mpv 参数在位（用 config-dir 载中文界面）")
ck(not any(a == "--no-config" for a in args),
   "没有 --no-config（否则会禁掉 config-dir 里的中文资源）")
ck(not any(a.startswith("--msg-level") for a in args), "默认不加 msg-level")
ck(env["LIVE_INTERP_FPS"] == "auto", "补帧默认 auto（整数倍率）",
   env["LIVE_INTERP_FPS"])
ck(env["LIVE_STATIC_TH"] == "0.003", "相似帧阈值默认 0.003",
   env["LIVE_STATIC_TH"])
# ★★ LIVE_RAW_OUT 已被删除（2026-09-30 重大更正）—— 这里断言它**不能**复活。
# 原因：mpv 的 vapoursynth 滤镜只接受 YUV 输出（vf_vapoursynth.c 的 mp_from_vs
# 第一句就是 `if (colorFamily == cfYUV) {...} return 0;`）。RGB 输出会报
# `Unsupported output format.` 并**禁用整条滤镜** —— 外部表现是「丢帧 0」，
# 实际是在播放未处理的源码流（VO 回落成源分辨率）。所以它当年"有效"是假象。
ck("LIVE_RAW_OUT" not in env,
   "RAW_OUT 已删除，不再传（传了会让整条滤镜被禁用）")
ck(not any("raw" in k.lower() for k in env), "环境变量里没有任何 raw* 残留")
ck(not any("vf-add" in a for a in args), "不再追加 format 滤镜（它只在 RAW_OUT 下有用途）")
# ★ GUI 的默认值会**覆盖** live.vpy 的默认值，两边必须一致：
#   曾经这里是 1，把「2 流 +10%」的优化又关掉了（截图才发现）
ck(env["LIVE_STREAMS"] == "2", "并发流默认 2（与 live.vpy 一致）",
   env["LIVE_STREAMS"])
cfg2 = dict(cfg, mpv_verbose=True)
_, args2 = G.build_launch(cfg2, ["X:/a.mkv"], r"\\.\pipe\t")
ck("--msg-level=all=info" in args2, "勾了详细日志才加 msg-level")
cfg3 = dict(cfg, upscale=False, interp=False)
env3, _ = G.build_launch(cfg3, ["X:/a.mkv"], r"\\.\pipe\t")
ck(env3["LIVE_UPSCALE"] == "0" and env3["LIVE_INTERP"] == "0", "两个开关能关掉")

# ═════════════════════════════════════════════════ 3 预估函数
print("== 3 性能预估 ==")
ms, tag = G.estimate("hr", "animev3", True, True, 854, 480)
ck(tag == "实测" and abs(ms - 15.59) < 0.02, "480p HR+av3 = 15.59ms（实测点）",
   f"{ms:.2f}ms {tag}")
ms, tag = G.estimate("lr", "realesr-animevideov3_re2x", True, True, 1280, 720)
ck(tag == "实测" and abs(ms - 18.08) < 0.02, "720p LR+re2x = 18.08ms（实测点）",
   f"{ms:.2f}ms {tag}")
ms, tag = G.estimate("hr", "animev3", True, True, 1280, 544)
ck(0 < ms < 34.93, "非标分辨率走插值", f"{ms:.2f}ms {tag}")
ms, tag = G.estimate("hr", "animev3", True, True, 1920, 1080)
ck(ms > 34.93 and tag in ("外推", "插值"), "1080p 是外推且更慢", f"{ms:.2f}ms {tag}")
# ⚠ 这张表**只认实机播放「丢帧 0」的边界**。我曾用 `mpv --untimed --vo=null`
# 量「处理速度倍率」来重测，结果 720p LR+re2x 全链路只有 1.086×（判"跑不动"），
# 而实播是 vf-fps 48 / 丢帧 0 —— 离线全速跑比真实播放悲观 2~3 倍，不许拿它改表。
ms, tag = G.estimate("hr", "animev3", True, False, 1280, 720)
ck(tag == "实测" and abs(ms - 14.04) < 0.02,
   "720p 只超分 = 14.04ms（实机表；别用离线倍率去改它）", f"{ms:.2f}ms {tag}")
# 比最小实测点还小的源 → 取端点。往小外推会虚高。
ms, tag = G.estimate("hr", "realesr-animevideov3_re2x", True, False, 854, 480)
ck(tag.startswith("端点") and abs(ms - 6.50) < 0.02,
   "比最小实测点还小 → 取端点（不外推出虚高的 fps）", f"{ms:.2f}ms {tag}")
ms, _ = G.estimate("hr", "x", False, False, 1280, 720)
ck(ms is None, "两个都关 → 不做预估")

# ═════════════════════════════════════════════════ 4 窗口与配置
print("== 4 窗口构造与配置载入 ==")
G.CFG_PATH = ROOT / "_t_ui_config.json"          # ★ 别碰用户真实配置
if G.CFG_PATH.exists():
    G.CFG_PATH.unlink()
app = QApplication(sys.argv)
app.setStyleSheet(G.QSS)
w = G.MainWindow()
ck(w.chk_up.isChecked() and w.chk_ip.isChecked(), "默认超分/补帧都开")
ck(w.cb_order.currentData() == "auto", "默认顺序 auto")
ck(w.cb_sr.currentData() == "animev3", "默认模型 animev3")
# ★ 2026-09-30 加：超分下拉只列**真的编过引擎**的模型（扫盘），且 AnimeJaNai
#   一旦编好就该出现。这条防的是「下拉里列了没引擎的模型 → 用户选完播放才发现
#   跳过超分」——加 AnimeJaNai 时差点就这么写。
_sr_keys = [w.cb_sr.itemData(i) for i in range(w.cb_sr.count())]
ck(all(G.sr_ladder(k) for k in _sr_keys if k != "animev3"),
   "下拉里每个模型都至少有一档引擎", str(_sr_keys))
ck(len(_sr_keys) == len(set(_sr_keys)), "模型不重复")
_aj = [k for k in _sr_keys if str(k).startswith("AnimeJaNai")]
ck(len(_aj) in (0, 4),
   "AnimeJaNai 要么全没编（0 个）要么 4 个都在", f"{len(_aj)} 个：{_aj}")
# 补帧模型下拉：只列 models/rife/ 里真的存在的 onnx
_rv = [w.cb_rifev.itemData(i) for i in range(w.cb_rifev.count())]
ck("4.26" in _rv, "补帧模型里有 4.26（默认）", str(_rv))
ck(all((G.RIFE_DIR / f"rife_v{k}.onnx").is_file() for k in _rv),
   "下拉里每个补帧模型都有对应 onnx")
ck(not any(k in ("4.25", "4.24") for k in _rv),
   "没把 rife_v2 里的 7 通道版列进来（TRT 11 跑不了）")
ck(w.cb_rifev.currentData() == "4.26", "补帧模型默认 4.26")
# 档位上限下拉：跑不动时的降本开关（LIVE_SR_MAX）
_sm = [w.cb_srmax.itemData(i) for i in range(w.cb_srmax.count())]
ck(0 in _sm, "档位上限含「不限」(0)", str(_sm))
# ★ 2026-10-01：**删掉「960p 档」**、标签改成档位本身的 WxH。
#   依据（实测档位表 640x360/854x480/960x720/1280x720/1920x1080）：
#   SR_MAX=960 与 SR_MAX=720 挑到的**都是 1280x720 档**（960x720 是 4:3，被宽高比筛掉），
#   960 那一档纯属误导 —— 所以预设只留 1080 / 720 两档。
ck(1080 in _sm and 720 in _sm, "档位上限含 1080 / 720", str(_sm))
ck(960 not in _sm, "档位上限**不再有** 960（它和 720 挑到同一档）", str(_sm))
_sm_txt = [w.cb_srmax.itemText(i) for i in range(w.cb_srmax.count())]
ck(any("1920*1080" in t for t in _sm_txt), "标签写成档位 WxH：1920*1080", str(_sm_txt))
ck(any("1280*720" in t for t in _sm_txt), "标签写成档位 WxH：1280*720", str(_sm_txt))
ck(w.cb_srmax.currentData() == 0, "档位上限默认「不限」")
# ★ 预设 / 自定义 二选一（与「限定播放分辨率」同款）
ck(hasattr(w, "rb_srmax_preset") and hasattr(w, "rb_srmax_custom"),
   "导入分辨率上限有「预设 / 自定义」单选")
ck(w.rb_srmax_preset.isChecked() and not w.rb_srmax_custom.isChecked(),
   "默认在「预设」")
ck(w.srmax_stack.currentIndex() == 0, "默认 stack 停在预设页")
w.rb_srmax_custom.setChecked(True)
ck(w.srmax_stack.currentIndex() == 1, "切自定义 → stack 翻到第 1 页",
   str(w.srmax_stack.currentIndex()))
w.sp_srmax.setValue(1000)
ck(w._srmax_value() == 1000, "_srmax_value() 取自定义值", str(w._srmax_value()))
_smc = w._collect_cfg()
ck(_smc["sr_max"] == 1000 and _smc["sr_max_custom"] is True,
   "自定义值 + 标记进配置", f'{_smc["sr_max"]}/{_smc["sr_max_custom"]}')
_es, _as = G.build_launch(_smc, ["X:/x.mkv"], r"\.\pipe\s")
ck(_es["LIVE_SR_MAX"] == "1000", "自定义值进 launch env", _es["LIVE_SR_MAX"])
# 自定义值不能被「切预设再切回来」冲掉（cap 那边踩过的同一个坑）
w.rb_srmax_preset.setChecked(True)
w.rb_srmax_custom.setChecked(True)
ck(w.sp_srmax.value() == 1000, "来回切 radio 不冲掉自定义值", str(w.sp_srmax.value()))
w.rb_srmax_preset.setChecked(True)
ck(w._srmax_value() == 0, "切回预设 → 取下拉值", str(w._srmax_value()))
# pick_ladder 的 srmax 语义（短边上限）：1080p 源限制到 960 → 从 1920x1080 掉到 1280x720
_ld_full = G.pick_ladder(1920, 1080, "AnimeJaNai_HD_V3.1_Balanced", 0)
_ld_1080 = G.pick_ladder(1920, 1080, "AnimeJaNai_HD_V3.1_Balanced", 1080)
_ld_cap = G.pick_ladder(1920, 1080, "AnimeJaNai_HD_V3.1_Balanced", 960)
_ld_720 = G.pick_ladder(1920, 1080, "AnimeJaNai_HD_V3.1_Balanced", 720)
ck(_ld_full == (1920, 1080), "不限档位时 1080p 源走 1920x1080 档", str(_ld_full))
ck(_ld_1080 == (1920, 1080), "上限 1080 不砍 1080p 档（短边判据）", str(_ld_1080))
ck(_ld_cap == (1280, 720), "上限 960 时 1080p 源掉到 1280x720 档", str(_ld_cap))
ck(_ld_720 == (1280, 720), "上限 720 时保留 1280x720（短边正好 720）", str(_ld_720))
_ld_480 = G.pick_ladder(1920, 1080, "AnimeJaNai_HD_V3.1_Balanced", 480)
ck(_ld_480 == (854, 480), "上限 480 时才掉到 854x480", str(_ld_480))
_ld_same = G.pick_ladder(1280, 720, "AnimeJaNai_HD_V3.1_Balanced", 960)
ck(_ld_same == (1280, 720), "720p 源在 960 上限下不动", str(_ld_same))
_ld_tiny = G.pick_ladder(1920, 1080, "AnimeJaNai_HD_V3.1_Balanced", 100)
ck(_ld_tiny == (1920, 1080), "上限小到没档可选 → 忽略上限，不崩", str(_ld_tiny))
# estimate 要**感知 srmax**：降档后预估必须跟着降，否则界面一直报「跑不动」
_ms_big, _ = G.estimate("lr", "AnimeJaNai_HD_V3.1_Balanced", True, True,
                        1920, 1080, 0)
_ms_cap, _ = G.estimate("lr", "AnimeJaNai_HD_V3.1_Balanced", True, True,
                        1920, 1080, 960)
ck(_ms_big > 30, "不限档时 1080p 全链路预估 >30ms（跑不动）", f"{_ms_big}")
ck(_ms_cap < 22, "降到 960 档后预估 <22ms（48fps 能跑）", f"{_ms_cap}")
ck(_ms_cap < _ms_big, "降档确实让预估变快", f"{_ms_big} → {_ms_cap}")
# ★ 2026-09-30：「输出倍率」控件**已删除**（冗余，详见 live.vpy 的删除说明）。
#   输出尺寸现在 = 源 × 模型原生倍率，再受「限定播放分辨率」压。
#   断言两件事：①控件确实没了 ②launch env 里也不再出现 LIVE_OUT_SCALE。
ck(not hasattr(w, "cb_scale"), "「输出倍率」控件已删除（与限定播放分辨率职责重叠）")
_rq = w._collect_cfg()
ck("out_scale" not in _rq, "配置字典里不再有 out_scale", str(sorted(_rq)))
_eq, _aa = G.build_launch(_rq, ["X:/x.mkv"], r"\.\pipe\s")
ck("LIVE_OUT_SCALE" not in _eq, "launch env 里不再传 LIVE_OUT_SCALE",
   str([k for k in _eq if "SCALE" in k]))
# ★★ 2026-10-01：`--hr-seek-framedrop=no` 是「单击进度条 → 画面快过音频」的修复。
#   删掉它 → 精确 seek 后画面内容会比音频**超前 4.7~5.3 秒**（实测，mpv 指标全看不见）。
#   反向验证过：去掉这个参数，真 GUI 真片源点一次进度条就能复现。
ck("--hr-seek-framedrop=no" in _aa,
   "launch args 含 --hr-seek-framedrop=no（防「跳进度条→音画错位」复发）",
   str([x for x in _aa if "hr-seek" in x]))
ck("LIVE_HRSEEK_FRAMEDROP" not in _eq,
   "该参数不经 env 传递（默认硬开）", str([k for k in _eq if "HRSEEK" in k]))

# ★★ 2026-10-01 加：播放组的「全屏」小勾（用户要求：勾上全屏、不勾按视频分辨率）。
ck(hasattr(w, "chk_fs"), "播放组有「全屏」勾选框")
ck(w.chk_fs.isChecked() is False, "全屏默认**不勾**（= 老行为：按视频分辨率开窗）",
   str(w.chk_fs.isChecked()))
_fs_save = w.chk_fs.isChecked()
w.chk_fs.setChecked(True)
_rc = w._collect_cfg()
ck(_rc.get("fullscreen") is True, "勾上全屏 → 进配置", str(_rc.get("fullscreen")))
_, _af = G.build_launch(_rc, ["X:/x.mkv"], r"\.\pipe\s")
ck("--fullscreen" in _af, "勾上全屏 → launch args 带 --fullscreen")
w.chk_fs.setChecked(False)
_, _af2 = G.build_launch(w._collect_cfg(), ["X:/x.mkv"], r"\.\pipe\s")
ck("--fullscreen" not in _af2, "不勾 → 不带 --fullscreen（按视频分辨率开窗）",
   str([x for x in _af2 if "fullscreen" in x]))
w.chk_fs.setChecked(_fs_save)          # 还原，别污染后面
# 原生倍率：animev3 = 4x，其余 = 2x，超分关 = 1
# ⚠ 必须**保存并恢复** cb_sr —— 这段会把模型切来切去，不还原就会污染后面
#   所有依赖"当前模型"的断言（ORDER auto 规则、超分档位、预估卡…）。
#   2026-09-30 第一次写时就漏了恢复，一次弄挂 3 个无关断言。
_sr_save = w.cb_sr.currentIndex()
_up_save = w.chk_up.isChecked()
_ns_cases = []
for _k, _want in (("animev3", 4), ("realesr-animevideov3_re2x", 2),
                  ("AnimeJaNai_HD_V3.1_Balanced", 2)):
    _i = w.cb_sr.findData(_k)
    if _i >= 0 and w.chk_up.isChecked():
        w.cb_sr.setCurrentIndex(_i)
        _ns_cases.append((_k, w._native_scale(), _want))
for _k, _got, _want in _ns_cases:
    ck(_got == _want, f"原生倍率 {_k} → {_want}x", f"{_got}")
w.chk_up.setChecked(False)
ck(w._native_scale() == 1, "超分关 → 原生倍率 1（尺寸不变）", str(w._native_scale()))
w.chk_up.setChecked(_up_save)
w.cb_sr.setCurrentIndex(_sr_save)          # ★ 还原，别污染后面的断言
# 像素级对照：输出 = 源 × 原生倍率（cap 不限时）
_ns_now = max(1, 4 if w.cb_sr.currentData() == "animev3" else 2)
ck(G.capped_play_size(1280, 720, _ns_now, 0, "short") == (1280 * _ns_now, 720 * _ns_now),
   "输出 = 源 × 原生倍率（cap 不限时）",
   f"{G.capped_play_size(1280, 720, _ns_now, 0, 'short')}")
# ★ 6 不是随便给的：有音轨（真音频时钟）下 cf=3 时 720p LR+re2x@48 每秒丢 8 帧
#   —— 画面追不上声音就是「音画不同步」；cf=6 起丢帧归零。
ck(w.sp_conc.value() == 6, "并发帧默认 6（3 在有音轨时会持续丢帧，实测）")
ck(w.sp_conc.maximum() >= 12, "并发帧上限给到 12（重档位需要更深的预取）")
ck(w.cb_multi.currentData() == 0, "目标帧率默认 auto", w.cb_multi.currentData())
# ★ 2026-09-30 改 semantics：档位从「绝对 fps」改成「源 ×n」。
#   auto + ×2/×3/×4 + 60/144（绝对） = 7 项。
ck(w.cb_multi.count() == 6, "档位 auto + ×2/×3/×4 + 60/144")
_datas = [w.cb_multi.itemData(i) for i in range(w.cb_multi.count())]
ck(_datas == [0, -2, -3, -4, 60, 144], "档位 data 编码 = [0,-2,-3,-4,60,144]",
   str(_datas))
ck(abs(w.sp_static.value() - 0.003) < 1e-9, "相似帧阈值默认 0.003")
# auto 解析：24fps 源 → 整数 2x = 48fps（而不是 60）
ck(G.resolve_target_fps(0, 24.0) == 48.0, "auto: 24fps → 48fps",
   G.resolve_target_fps(0, 24.0))
ck(G.resolve_target_fps(0, 30.0) == 60.0, "auto: 30fps → 60fps")
v = G.resolve_target_fps(0, 24.0 / 1.001)
ck(abs(v / (24.0 / 1.001) - 2.0) < 1e-9, "auto: 23.976fps → 整数 2x（47.95fps）",
   f"{v:.3f}")
# ★ 新版：auto 对 ≥60fps 源给 2x（旧版给 1x = 完全不插帧，等于白选）
ck(G.resolve_target_fps(0, 60.0) == 120.0, "auto: 60fps 源 → ×2 = 120fps",
   G.resolve_target_fps(0, 60.0))
# ★ 核心新语义：倍数档与源帧率无关，任何源都是那个倍数
ck(G.resolve_multi(-2, 24.0) == 2.0 and G.resolve_multi(-2, 29.97) == 2.0,
   "×2 档：24fps 与 29.97fps 源都给 2x")
ck(G.resolve_multi(-4, 25.0) == 4.0, "×4 档：25fps 源给 4x（=100fps）",
   G.resolve_multi(-4, 25.0))
ck(G.resolve_multi(-3, 23.976) == 3.0, "×3 档：23.976fps 源给 3x（=71.9fps）")
# 旧档位最致命的坑：50fps 源配「48fps 目标」会算出 0.96x < 1 → 跳过补帧
ck(G.resolve_multi(-2, 50.0) == 2.0, "×2 档修掉旧版『50fps 源 → 0.96x 白选』",
   G.resolve_multi(-2, 50.0))
ck(G.resolve_target_fps(60, 24.0) == 60.0, "指定 60 就照给 60（不自动降）")
ck(G.resolve_target_fps(0, 0.0) == 48.0, "源帧率未知 → 兜底 24fps×2",
   G.resolve_target_fps(0, 0.0))
ck(G.resolve_multi(None, 24.0) == 2.0, "sel=None（组合框未就绪）→ 当 auto")
# ⚠ 阈值不能写死大数：档位数会随 LIVE_SR_BUILD 现编（按源尺寸落盘）与引擎重编而
#   增减 —— 原来写 `>= 12` 在 models/sr 只有 7 个档时必然误报。这里只验"扫盘没坏"。
ck(len(w.engines) >= 4, "扫到超分引擎", f"{len(w.engines)} 个")

# ═════════════════════════════════════════════════ 5 日志覆盖语义
print("== 5 日志面板覆盖语义 ==")
w.log.clear()
w._last_was_replace = False
w._append_log("普通行", False)
w._append_log("进度A", True)
w._append_log("进度B", True)
w._append_log("普通行2", False)
lines = w.log.toPlainText().split("\n")
ck(lines == ["普通行", "进度B", "普通行2"], "普通→覆盖A→覆盖B→普通 = 3 行",
   repr(lines))
ck(w.log.toPlainText() != "\n" + w.log.toPlainText(), "开头没有多余空行")
ck(w.log.maximumBlockCount() == 4000, "日志限行 4000（防长播变卡）")

# ═════════════════════════════════════════════════ 6 auto 复刻
print("== 6 auto 规则复刻 ==")
w.lst_files.clear()
it = QListWidgetItem("x.mkv")
it.setData(Qt.UserRole, "X:/x.mkv")
w.lst_files.addItem(it)
w.media["X:/x.mkv"] = (1920, 1080, 24.0)
o, sr, *_ = w._current_plan()
ck(o == "lr" and sr == "realesr-animevideov3_re2x",
   "1920 宽 → lr + re2x（宽 ≥1280 就强制）", f"{o}/{sr}")
w.media["X:/x.mkv"] = (854, 480, 24.0)
w.cb_multi.setCurrentIndex(w.cb_multi.findData(0))   # ★ 显式回到 auto，别靠前面测试的残留
o, sr, *_ = w._current_plan()
ck(o == "hr" and sr == "animev3",
   "854 宽 + 默认 auto（→48fps）→ hr + animev3", f"{o}/{sr}")
w.cb_multi.setCurrentIndex(w.cb_multi.findData(-4))   # 源 ×4 = 24→96fps ≥60
o, sr, *_ = w._current_plan()
ck(o == "lr" and sr == "realesr-animevideov3_re2x",
   "854 宽 + ×4（→96fps ≥60）→ 转 lr + re2x", f"{o}/{sr}")
w.cb_multi.setCurrentIndex(w.cb_multi.findData(0))
# ★ 新增：倍数档不会再出现「倍率 <1 白选」的旧坑（50fps 源 ×2）
w.media["X:/x.mkv"] = (854, 480, 50.0)
w.cb_multi.setCurrentIndex(w.cb_multi.findData(-2))
o2, _, ms2, _, _, _, _, bf2 = w._current_plan()
ck(bf2 == 50.0, "×2 档识别到 50fps 源", str(bf2))
w.media["X:/x.mkv"] = (854, 480, 24.0)
w.cb_multi.setCurrentIndex(w.cb_multi.findData(60))
w.cb_order.setCurrentIndex(w.cb_order.findData("lr"))
*_, note, _ = w._current_plan()
ck("⚠" in note, "手动 lr + animev3 会出警告", note[:28])
w.cb_order.setCurrentIndex(w.cb_order.findData("auto"))
w.chk_ip.setChecked(False)
o, *_ = w._current_plan()
ck(o == "hr", "关掉补帧后 auto 回退 hr", o)
w.chk_ip.setChecked(True)

# ═════════════════════════════════════════════════ 7 联动与启用态
print("== 7 启用态联动 ==")
w.chk_up.setChecked(False)
ck(not w.cb_sr.isEnabled() and not w.lb_sr.isEnabled(), "关超分 → 模型下拉和标签一起灰")
w.chk_up.setChecked(True)
ck(w.cb_sr.isEnabled(), "开超分 → 恢复")
w.chk_ip.setChecked(False)
ck(not w.cb_multi.isEnabled() and not w.sp_static.isEnabled(),
   "关补帧 → 目标帧率/相似帧阈值都变灰")
w.chk_ip.setChecked(True)

# ═════════════════════════════════════════════════ 8 窄窗布局
print("== 8 窄窗布局（控件不许被压扁）==")
w.resize(980, 560)
w.show()
for _ in range(3):
    app.processEvents()
# ⚠ 判据用 **minimumSizeHint**（而不是 sizeHint）：offscreen 平台没有真实字体度量，
#   sizeHint 会比真实平台虚高几 px（实测 GPU 输入框 sizeHint 32~35 之间跳），
#   拿它当门槛会得到"时绿时红"的假失败。minimumSizeHint 才是"不被压扁"的本质线。
for name, ctl in (("超分模型下拉", w.cb_sr), ("补帧倍率下拉", w.cb_multi),
                  ("补帧模型下拉", w.cb_rifev), ("档位上限下拉", w.cb_srmax),
                  ("顺序下拉", w.cb_order), ("GPU 输入框", w.sp_dev)):
    need = ctl.minimumSizeHint().height()
    # ★ 容差必须区分平台（照抄 _t_a4k_ui.py 的手法）：offscreen **没有真实字体度量**，
    #   同一控件的 minimumSizeHint 会在 21~35 之间跳（实测 GPU 输入框单独跑 21、
    #   在 smoke 里跑到第 8 节时是 35，而实际高度一直是 32）。
    #   拿它做严格门槛 = 制造"时绿时红"的假失败。真屏判据不放松。
    _tol = 4 if os.environ.get("QT_QPA_PLATFORM") == "offscreen" else 0
    ck(ctl.height() >= need - _tol,
       f"{name} 高 {ctl.height()} ≥ 最小需要 {need}（容差 {_tol}）")
# 预估卡：2026-09-30 起只留 3 行客观数据（判定/建议文本已按用户要求删除），
#   高度自然变矮。判据改成"至少装得下 2 行"（≥40），不再要求 ≥60。
ck(w.lb_plan.height() >= 40, "预估卡没被压到显示不全（≥40px）",
   f"{w.lb_plan.height()}")

# ★★ 2026-09-30 补：**队列非空**的渲染路径必须也能跑通。
#   踩过的坑：删「输出倍率」控件时漏了一处 `self.cb_scale...`（在 _update_plan
#   的后半段），而 smoke 里队列一直是空的 → `_update_plan` 提前 return →
#   **测试全绿但真跑必崩**（用户一拖文件进来就 AttributeError）。
#   教训：删控件后必须专门测"用到它的那条完整路径"，空队列的早返回会掩盖问题。
try:
    # 伪造 _current_plan 的返回值，直接走预估卡渲染分支
    _orig = w._current_plan
    w._current_plan = lambda: ("hr", "animev3", 12.5, "实测", 1920, 1080, "", 24.0)
    try:
        w._update_plan()
        ck(True, "队列有文件时 _update_plan 能跑通（删控件后最容易漏的路径）")
    except AttributeError as _e:
        ck(False, "队列有文件时 _update_plan 能跑通", f"AttributeError: {_e}")
    finally:
        w._current_plan = _orig
except Exception as _e:
    ck(False, "队列有文件时 _update_plan 能跑通", f"{type(_e).__name__}: {_e}")
ck(w.btn_run.isEnabled() == (w.lst_files.count() > 0), "有文件时播放键可用")
print(f"  预估卡文字：{w.lb_plan.text()[:120].replace(chr(10), ' | ')}")

# ═════════════════════════════════════════════════ 9 配置往返
print("== 9 配置记忆往返 ==")
w.cb_multi.setCurrentIndex(w.cb_multi.findData(-3))
w.chk_mpvlog.setChecked(True)
_i_rv = w.cb_rifev.findData("4.25_lite")
if _i_rv >= 0:
    w.cb_rifev.setCurrentIndex(_i_rv)
_i_sm = w.cb_srmax.findData(1080)
if _i_sm >= 0:
    w.cb_srmax.setCurrentIndex(_i_sm)
w.sp_dev.setValue(1)
w._save_cfg()
saved = G.load_cfg()
ck(saved["interp_fps"] == -3 and saved["mpv_verbose"] is True
   and saved["device"] == 1,
   "写盘再读回一致（×3 档存 -3）", f"fps={saved['interp_fps']} dev={saved['device']}")
ck(saved.get("sr_max") == 1080, "档位上限也记住了", str(saved.get("sr_max")))
ck(saved["files"] == ["X:/x.mkv"], "文件队列也记住了", str(saved["files"]))
G.CFG_PATH.write_text("{ 坏掉的 json", encoding="utf-8")
ck(G.load_cfg() == dict(G.DEFAULTS), "配置损坏 → 回落默认值，不抛异常")

# ═════════════════════════════════════════════════ 10 运行时状态面板
print("== 10 运行时状态渲染 ==")
d = {
    "media-title": "涼宮ハルヒの憂鬱 - 03 [1280x720].mkv",
    "time-pos": 83.4, "duration": 1445.0,
    "playlist-pos": 1, "playlist-count": 3,
    "estimated-vf-fps": 43.2, "estimated-frame-rate": 47.95,
    "frame-drop-count": 0, "mistimed-frame-count": 0,
    "decoder-frame-drop-count": 0, "vo-delayed-frame-count": 0,
    "video-params/w": 1280, "video-params/h": 720,            # 解码器输出（源）
    "video-out-params/w": 2560, "video-out-params/h": 1440,   # 滤镜之后（超分后）
    "display-fps": 143.999,
    "pause": False,
}
h1 = G.MainWindow.render_live(d, 43.8, 48.0, 0.0)
ck("输出 <b>43.8 fps" in h1, "输出帧率进面板")
ck("实际显示 <b>43.8 fps" in h1, "零丢帧时实际显示 = 输出")
ck("01:23" in h1 and "24:05" in h1, "进度格式化 mm:ss")
ck("2/3" in h1, "播放列表位置")
# ★ 分辨率必须用 video-out-params（滤镜链之后 = 超分后的真实输出）。
#   用 video-params 会一直显示**源**尺寸：实测挂超分播 1280x720 源时，
#   面板显示"→ 1280×720"，而真输出是 2560×1440。
ck("2560×1440" in h1, "分辨率取 video-out-params（超分后的输出）")
ck("1280×720" not in h1, "不显示源分辨率（video-params）")
ck("屏 144Hz" in h1, "显示显示器刷新率")
ck("实时" not in h1, "没传实测帧率 → 不出现「实时」（排版与旧版一致）")
ck("实时 <b>41.2 fps" in G.MainWindow.render_live(d, 43.8, 48.0, 0.0, 41.2),
   "传了实测帧率 → 显示「实时」")
ck("● 流畅" in h1 and "7fd6a0" in h1, "零丢帧 → 绿色「流畅」")
ck("达成 91%" in h1, "达成率按实际显示算")
h2 = G.MainWindow.render_live(dict(d, **{"frame-drop-count": 218}), 48.0, 48.0, 20.0)
ck("实际显示 <b>28.0 fps" in h2, "丢 20/s 时实际显示 28fps（而非 48）")
ck("● 明显掉帧" in h2 and "e08a8a" in h2, "明显掉帧变红")
ck("丢帧 20.0/s（累计 218）" in h2, "丢帧速率 + 累计都在")
h3 = G.MainWindow.render_live(d, 48.0, 48.0, 2.0)
ck("● 轻微掉帧" in h3 and "e0c06a" in h3, "轻微掉帧变黄")
ck("测量中" in G.MainWindow.render_live(d, None, 48.0), "首次无数据 → 测量中")
h4 = G.MainWindow.render_live(dict(d, **{"media-title": "a<b>&c.mkv",
                                         "pause": True}), 40.0, 48.0)
ck("a&lt;b&gt;&amp;c.mkv" in h4, "文件名做 HTML 转义")
ck("⏸" in h4, "暂停状态有标记")
ck(G.MainWindow._fmt_time(None) == "--:--"
   and G.MainWindow._fmt_time(-1) == "--:--", "非法时长兜底")
ck(G.MainWindow._fmt_time(3725) == "1:02:05", "超 1 小时带小时位")

# ═════════════════════════════════════════════════ 11 采样与丢帧速率
print("== 11 采样与丢帧速率 ==")
w._fps_hist = []
w._drop_hist = []
w._sample({"estimated-vf-fps": 40.0, "frame-drop-count": 100})
w._sample({"estimated-vf-fps": 0.2})              # 太低 → 丢
w._sample({"estimated-vf-fps": float("nan")})     # 非数 → 丢
w._sample({"estimated-vf-fps": float("inf")})     # 无穷 → 丢
w._sample({"estimated-vf-fps": True})             # 布尔 → 丢
w._sample({"frame-drop-count": 130})
w._sample({"media-title": "只有标题"})             # 都不是数字 → 不采
ck(len(w._fps_hist) == 1, "只收有效 fps 采样", str(len(w._fps_hist)))
ck(len(w._drop_hist) == 2, "只收整数丢帧计数", str(len(w._drop_hist)))
ck(abs(w._avg_fps(10) - 40.0) < 0.01, "fps 均值计算")
now = time.monotonic()
w._drop_hist = [(now - 4.0, 100), (now - 2.0, 140), (now, 180)]
ck(abs(w._drop_rate(5.0) - 20.0) < 0.5, "丢帧速率 = 20/s",
   f"{w._drop_rate(5.0):.2f}")
w._drop_hist = [(now, 5)]
ck(w._drop_rate(5.0) == 0.0, "样本不足 → 速率 0（不抛异常）")
w._drop_hist = []
# ★ 实测帧率（帧号差分）—— 全项目**唯一真·实测**的帧率来源：
#   estimated-vf-fps 是滤镜链声明值（恒定），帧号差分才是真跑出来的速率。
w._fno_hist = []
w._sample({"estimated-frame-number": 100})
w._sample({"estimated-frame-number": True})        # 布尔 → 丢
w._sample({"estimated-frame-number": "100"})       # 字符串 → 丢
w._sample({"media-title": "只有标题"})              # 无关字段 → 不采
ck(len(w._fno_hist) == 1, "只收整数帧号", str(len(w._fno_hist)))
w._fno_hist = [(now - 2.0, 100), (now, 148)]
ck(abs(w._real_fps(5.0) - 24.0) < 0.5, "实测帧率 = Δ帧号 / Δt = 24/s",
   f"{w._real_fps(5.0):.2f}")
w._fno_hist = [(now, 5)]
ck(w._real_fps(5.0) is None, "样本不足 → None（不抛异常）")
w._fno_hist = []

st = G.MpvStat(r"\\.\pipe\nonexistent")
ck(st.snapshot == {} and st.opened is False, "IPC 快照初始为空、未连接")
old_ref = st.snapshot
st.snapshot = {"x": 1}
ck(old_ref == {} and st.snapshot == {"x": 1},
   "写时复制：换引用不动旧快照（读方永远自洽）")
ck(st.last_error == "", "未启动时错误字段为空字符串")
st.stop()

# ═════════════════════════════════════════════════ 11.5 限定播放分辨率
print("== 11.5 限定播放分辨率（4x 模型超尺寸时按它补帧）==")
# ★ 与 live.vpy 的 `_play_cap_size()` 同源：短边判据、等比缩放、取偶（YUV420）
#   只当上限用 —— 源比它还小就**绝不放大**（"限定 ≠ 锁定"）
ck(G.capped_play_size(1280, 720, 4.0, 0) == (5120, 2880),
   "不限（0）→ 4x 直接放出来 5120×2880，不缩")
ck(G.capped_play_size(1280, 720, 4.0, 2160) == (3840, 2160),
   "4x@720p 超 4K 上限 → 缩到 3840×2160")
ck(G.capped_play_size(1280, 720, 4.0, 1440) == (2560, 1440),
   "缩到 2K（1440）")
ck(G.capped_play_size(1280, 720, 4.0, 1080) == (1920, 1080),
   "缩到 1080p")
ck(G.capped_play_size(1280, 720, 1.0, 2160) == (1280, 720),
   "源本身没超上限 → 原样（绝不放大）")
ck(G.capped_play_size(1920, 1080, 2.0, 2160) == (3840, 2160),
   "刚好压在 4K 上限上 → 不缩")
ck(G.capped_play_size(3840, 2160, 2.0, 1080) == (1920, 1080),
   "8K → 1080p 上限，短边判据成立")
# 取偶：YUV420 要求宽高都是偶数（否则 VS 直接报错、整条链被禁用）
for _w, _h, _s, _m in ((1279, 719, 1.0, 0), (1277, 715, 1.0, 719),
                       (1001, 999, 1.0, 1440)):
    _ow, _oh = G.capped_play_size(_w, _h, _s, _m)
    ck(_ow % 2 == 0 and _oh % 2 == 0, f"输出尺寸必为偶数（{_w}x{_h}→{_ow}x{_oh}）")

# 界面控件：默认"不限"，五档齐全
_ph_items = [w.cb_play_h.itemData(i) for i in range(w.cb_play_h.count())]
ck(_ph_items == [0, 2160, 1440, 1080, 720], "限定播放分辨率五档齐全",
   str(_ph_items))
ck(w.cb_play_h.currentData() == 0, "默认不限（0）—— 不改变既有行为",
   str(w.cb_play_h.currentData()))
# 关联的 label 文案
ck(w.lb_play_h.text() == "限定播放分辨率", "控件说明文字", w.lb_play_h.text())
# 重命名：档位上限 → 导入分辨率上限
ck(w.lb_srmax.text() == "导入分辨率上限", "「档位上限」已改名为「导入分辨率上限」",
   w.lb_srmax.text())

# 进配置字典 & launch env
w.cb_play_h.setCurrentIndex(w.cb_play_h.findData(1440))
_rcp = w._collect_cfg()
ck(_rcp.get("play_max_h") == 1440, "限定播放分辨率写进配置字典",
   str(_rcp.get("play_max_h")))
_cep, _ = G.build_launch(_rcp, ["X:/x.mkv"], r"\\.\pipe\s")
ck(_cep.get("LIVE_PLAY_MAX_H") == "1440", "限定播放分辨率进 launch env",
   str(_cep.get("LIVE_PLAY_MAX_H")))
# 关掉超分+补帧后它该置灰（没有任何处理 → 谈不上限定输出尺寸）
w.chk_up.setChecked(False)
w.chk_ip.setChecked(False)
w._refresh_enabled()
ck(w.cb_play_h.isEnabled() is False, "超分/补帧全关 → 限定播放分辨率置灰")
# ★ 判据是 ip or up（不像 cb_multi 那样只跟补帧）—— 只开超分也该可用，
#   因为它限的就是超分输出的尺寸
w.chk_up.setChecked(True)
w._refresh_enabled()
ck(w.cb_play_h.isEnabled() is True, "只开超分 → 限定播放分辨率可用（判据 ip or up）")
w.chk_up.setChecked(False)
w.chk_ip.setChecked(True)
w._refresh_enabled()
ck(w.cb_play_h.isEnabled() is True, "只开补帧 → 限定播放分辨率也可用")
w.chk_ip.setChecked(False)
w.chk_up.setChecked(True)
w._refresh_enabled()

# ★★ 2026-09-30 新增：预设 / 自定义 二选一（radio + stack）
print("-- 限定播放分辨率：预设 / 自定义 二选一 --")
ck(w.rb_cap_preset.isChecked() and not w.rb_cap_custom.isChecked(),
   "默认选「预设」")
ck(w.cap_stack.currentIndex() == 0, "默认停在 stack 第 0 页（预设下拉）",
   str(w.cap_stack.currentIndex()))
# 切到自定义：翻页 + **只在输入框为 0 时**才把预设值当初值填进去
w.cb_play_h.setCurrentIndex(w.cb_play_h.findData(1080))
w.sp_cap.setValue(0)                       # 显式归零，测"初值同步"分支
w.rb_cap_custom.setChecked(True)
ck(w.cap_stack.currentIndex() == 1, "切自定义 → stack 翻到第 1 页",
   str(w.cap_stack.currentIndex()))
ck(int(w.sp_cap.value()) == 1080, "输入框为 0 时切自定义 → 用预设值当初值",
   str(w.sp_cap.value()))
# 自定义值 + 判据边进配置与 env
w.sp_cap.setValue(1920)
w.cb_cap_edge.setCurrentIndex(w.cb_cap_edge.findData("long"))
_cpc = w._collect_cfg()
ck(_cpc.get("play_max_custom") is True, "自定义标记进配置",
   str(_cpc.get("play_max_custom")))
ck(_cpc.get("play_max_h") == 1920, "自定义数值进配置（覆盖预设值）",
   str(_cpc.get("play_max_h")))
ck(_cpc.get("play_max_edge") == "long", "判据边进配置", str(_cpc.get("play_max_edge")))
_cpe, _ = G.build_launch(_cpc, ["X:/x.mkv"], r"\\.\pipe\s")
ck(_cpe.get("LIVE_PLAY_MAX_H") == "1920", "自定义值进 launch env",
   str(_cpe.get("LIVE_PLAY_MAX_H")))
ck(_cpe.get("LIVE_PLAY_MAX_EDGE") == "long", "判据边进 launch env",
   str(_cpe.get("LIVE_PLAY_MAX_EDGE")))
# 切回预设：判据边必须**强制回落 short**（预设档位说的是短边）
w.rb_cap_preset.setChecked(True)
ck(w.cap_stack.currentIndex() == 0, "切回预设 → stack 回到第 0 页",
   str(w.cap_stack.currentIndex()))
_cpc2 = w._collect_cfg()
ck(_cpc2.get("play_max_edge") == "short",
   "预设模式判据边强制 short（预设档位语义就是短边）",
   str(_cpc2.get("play_max_edge")))
# 算法层：长边/短边判据必须给出不同结果（否则 edge 没接上）
_short = G.capped_play_size(3840, 2160, 1.0, 1920, "short")
_long = G.capped_play_size(3840, 2160, 1.0, 1920, "long")
ck(_long == (1920, 1080), "3840x2160 限 1920 按长边 → 1920x1080", str(_long))
ck(_short == (3414, 1920), "3840x2160 限 1920 按短边 → 3414x1920", str(_short))
ck(_short != _long, "两条判据边确实走不同分支")
# 自定义模式下 radio 互斥（选一个自动取消另一个）
w.rb_cap_custom.setChecked(True)
ck(not w.rb_cap_preset.isChecked(), "radio 互斥：选自定义后预设自动取消")
# ★ 切走再切回，用户填的自定义值**不得被预设值覆盖**
#   （2026-09-30 写这段测试时抓到的真实 UX 缺陷：无条件同步会冲掉 1920）
w.rb_cap_preset.setChecked(True)
w.rb_cap_custom.setChecked(True)
ck(int(w.sp_cap.value()) == 1920, "切走再切回，自定义值不被预设覆盖",
   str(w.sp_cap.value()))
ck(w._cap_value() == (1920, "long"), "_cap_value() 取自定义组的值", str(w._cap_value()))
w.rb_cap_preset.setChecked(True)
ck(w._cap_value() == (1080, "short"), "_cap_value() 切回预设后取下拉值",
   str(w._cap_value()))
# 复位，别污染后面的检查
w.cb_play_h.setCurrentIndex(w.cb_play_h.findData(0))

# ═════════════════════════════════════════════════ 12 能力上限表
print("== 12 实测能力上限表 ==")
RE2X = "realesr-animevideov3_re2x"
ck(G.max_stable_fps(854, 480, "lr", RE2X, True, True) == 72,
   "480p LR+re2x 上限 72 fps（实播：72 丢 0、96 丢 776）")
ck(G.max_stable_fps(1280, 720, "lr", RE2X, True, True) == 48,
   "720p LR+re2x 上限 48 fps（实播：48 丢 0~4、60 丢 373）")
ck(G.max_stable_fps(854, 480, "hr", "animev3", True, True) == 72,
   "480p HR+av3 上限 72 fps（实播：48 丢 0~1、60 丢 313）")
ck(G.max_stable_fps(854, 480, "hr", "animev3", False, True) == 120,
   "只补帧 480p 上限 120 fps")
ck(G.max_stable_fps(1280, 720, "hr", "animev3", False, True) == 120,
   "只补帧 720p 上限 120 fps（实播 @60 有 2.01x 余量）")
ck(G.max_stable_fps(1920, 1080, "hr", "animev3", False, True) == 48,
   "只补帧 1080p 上限 48 fps —— @60 实播只有 62%，会稳定丢帧 27/s")
ck(G.max_stable_fps(320, 240, "hr", "animev3", False, True) == 120,
   "比 480p 更小 → 只补帧取端点 120")
ck(G.max_stable_fps(3840, 2160, "hr", "animev3", False, True) == 48,
   "4K 源 → 只补帧取端点 48（表里只到 1080p）")
# 每对输入的 RIFE 调用次数 = ⌈倍率⌉−1 —— 这是「仅补帧也掉帧」的机制根因
ck(G.interp_calls(2.0) == 1, "2x → 1 次/对")
ck(G.interp_calls(2.5) == 2, "2.5x（=60fps）→ 2 次/对 ← 性价比最差")
ck(G.interp_calls(3.0) == 2, "3x → 2 次/对")
ck(G.interp_calls(4.0) == 3, "4x → 3 次/对")
ck(G.interp_calls(1.5) == 1, "1.5x → 1 次/对")
ck(G.max_stable_fps(854, 480, "hr", "animev3", True, False) is None,
   "不补帧 → 没有「帧率上限」这个概念")
ck(G.max_stable_fps(640, 360, "lr", RE2X, True, True) == 72,
   "比 480p 更小的源 → 取端点值（+超分 72）")
ck(G.max_stable_fps(1920, 1080, "lr", RE2X, True, True) == 24,
   "1080p + 超分 → 24（实播 @48 只有 0.41x，超分那步 fp32 I/O 太贵）")
mid = G.max_stable_fps(1024, 576, "lr", RE2X, True, True)
ck(48 < mid < 72, "中间分辨率按像素插值", f"{mid}")

# ═════════════════════════════════════════════════ 收尾

# ── 旧配置里的 concurrent=3 必须被抬上来 ─────────────────────
# 3 是坏值：带音轨的片子、真实音频时钟下会持续丢帧（音画不同步）。
# 配置里存着 3 的话会盖掉新默认值，等于改了白改 —— 所以必须有迁移。
_old = ROOT / "_t_ui_cfg_old.json"
_old.write_text('{"concurrent": 3, "files": [], "sr": "re2x"}', encoding="utf-8")
_keep_cfg = G.CFG_PATH
G.CFG_PATH = _old
_w2 = G.MainWindow()
ck(_w2.sp_conc.value() == 6, "旧配置的并发帧 3 → 迁移到 6（否则音画不同步照旧）",
   str(_w2.sp_conc.value()))
ck(_w2.cb_sr.currentData() == "realesr-animevideov3_re2x"
   or _w2.cb_sr.currentData() is not None, "旧模型名不会让下拉崩掉")
G.CFG_PATH = _keep_cfg
_w2.close()
for _p in (ROOT / "_t_ui_cfg_old.json", ROOT / "_t_ui_config.json"):
    _p.unlink(missing_ok=True)

# ══════════════════════════════════════════════════════════════════
# 13 补帧引擎选择（★ 也放最后：同样改界面状态）
# ══════════════════════════════════════════════════════════════════
print("== 13 补帧引擎（rife / mv / dup / blend）==")
_vfi_items = [w.cb_vfi.itemData(i) for i in range(w.cb_vfi.count())]
ck(_vfi_items == ["rife", "mv", "dup", "blend"],
   "引擎下拉四项齐全（vsrife 已删）", str(_vfi_items))
ck(w.cb_vfi.currentData() == "rife", "引擎默认 rife（最快）",
   str(w.cb_vfi.currentData()))

w.chk_ip.setChecked(True)
w.cb_vfi.setCurrentIndex(w.cb_vfi.findData("mv"))
_cenv, _ = G.build_launch(w._collect_cfg(), ["X:/x.mkv"], r"\\.\pipe\s")
ck(_cenv["LIVE_VFI"] == "mv", "选 mvtools → env LIVE_VFI=mv",
   _cenv["LIVE_VFI"])
ck(w.cb_rifev.isEnabled() is False,
   "选 mvtools 时补帧模型下拉置灰（它不用 RIFE 权重）")
ck("mvtools" in w.lb_backend.text(),
   "选 mvtools 时后端说明改成'跑 CPU'", w.lb_backend.text())

w.cb_vfi.setCurrentIndex(w.cb_vfi.findData("rife"))
ck(w.cb_rifev.isEnabled() is True, "切回 rife 时补帧模型下拉恢复可用")

# mv 档位（只在引擎=mv 时可编辑）
ck(w.cb_mv.count() == 3, "mv 档位三项齐全", str(w.cb_mv.count()))
wk = [w.cb_mv.itemData(i) for i in range(w.cb_mv.count())]
ck(wk == ["fast", "mid", "best"], "mv 档位键正确", str(wk))
w.cb_vfi.setCurrentIndex(w.cb_vfi.findData("mv"))
ck(w.cb_mv.isEnabled() is True, "选 mv 时档位下拉可用")
w.cb_mv.setCurrentIndex(w.cb_mv.findData("mid"))
_rcm = w._collect_cfg()
ck(_rcm.get("mv_preset") == "mid", "mv 档位写进配置字典", str(_rcm.get("mv_preset")))
_cem, _ = G.build_launch(_rcm, ["X:/x.mkv"], r"\\.\pipe\s")
ck(_cem.get("LIVE_MV_PRESET") == "mid", "mv 档位进 launch env",
   str(_cem.get("LIVE_MV_PRESET")))
w.cb_vfi.setCurrentIndex(w.cb_vfi.findData("rife"))
ck(w.cb_mv.isEnabled() is False, "切回 rife 时 mv 档位置灰")
w.cb_vfi.setCurrentIndex(w.cb_vfi.findData("mv"))


# dup / blend 分支（零插值引擎：没有任何专属控件，所有档位全置灰）
for _eng in ("dup", "blend"):
    w.cb_vfi.setCurrentIndex(w.cb_vfi.findData(_eng))
    ck(w.cb_vfi.currentData() == _eng, f"能选中 {_eng} 引擎")
    for _wname in ("cb_rifev", "cb_mv"):
        ck(getattr(w, _wname).isEnabled() is False,
           f"选 {_eng} 时 {_wname} 置灰（它不做推理，没有权重/档位）")
    ck("不" in w.lb_backend.text(),
       f"选 {_eng} 时后端说明写明'不做推理'", w.lb_backend.text())
    _ce, _ = G.build_launch(w._collect_cfg(), ["X:/x.mkv"], r"\\.\pipe\s")
    ck(_ce["LIVE_VFI"] == _eng, f"{_eng} 进 launch env LIVE_VFI",
       _ce.get("LIVE_VFI"))
# dup/blend 也必须能被存进配置（重启不丢）
w.cb_vfi.setCurrentIndex(w.cb_vfi.findData("blend"))
ck(w._collect_cfg().get("vfi") == "blend", "blend 写进配置字典",
   str(w._collect_cfg().get("vfi")))
w.cb_vfi.setCurrentIndex(w.cb_vfi.findData("rife"))

# 引擎选择要能被 _collect_cfg 存下来（否则重启就丢）
w.cb_vfi.setCurrentIndex(w.cb_vfi.findData("mv"))
_rc = w._collect_cfg()
ck(_rc.get("vfi") == "mv", "引擎写进配置字典", str(_rc.get("vfi")))
ck("LIVE_VFI" in G.build_launch(_rc, ["X:/x.mkv"], r"\\.\pipe\s")[0],
   "引擎出现在 launch env 里")
w.cb_vfi.setCurrentIndex(w.cb_vfi.findData("rife"))

# ══════════════════════════════════════════════════════════════════
# 14 live.vpy：模块级变量必须"处处有定义"（★ 2026-09-30 血的教训）
# ══════════════════════════════════════════════════════════════════
# 真实事故：`_CPU_VFI_FORCED_HR` 只在 `if ORDER == "auto":` 分支里赋值，
#   却在分支外的日志段被无条件读 → 用户**手选 HR**（LIVE_ORDER=hr）时：
#       NameError: name '_CPU_VFI_FORCED_HR' is not defined
#   → VS 图构建失败 → 整个 vapoursynth 滤镜被 mpv 禁用 →
#   `VO: 1280x720`（在放未处理的源码流），用户看到的现象就是"超分完全没生效"。
#
# 为什么之前的测试没抓到：我自己的测试恰好都跑在会让它被赋值的路径上。
#   → 所以这里做**静态检查**：凡是在 `if` 块里赋值的模块级名字，
#     必须也在 if 之外出现过（赋值或有默认值）。静态查最便宜、覆盖最全，
#     不用真去跑几十个组合（那个由 _t_build_matrix.py 负责）。
print("== 14 live.vpy 模块级变量处处有定义 ==")
import ast as _ast
_LV = (ROOT / "live.vpy").read_text(encoding="utf-8")
_tree = _ast.parse(_LV)

# 收集：模块级 if 块内赋值的名字 ← 这些是"可能没被定义"的嫌疑对象
# ★ 判据要精确，否则误报（第一版就误报了 4 个）：
#   ① `if/else` 两路都赋值 → 安全，不算嫌疑（如 rgb）
#   ② 只在同一块内读 → 安全，不算嫌疑（如 cf / fr / _need_lr）
#   ③ **只赋值不带 else，且块外有读取** → 真嫌疑（才是 _CPU_VFI_FORCED_HR 的形态）
_reads_outside: dict[str, list[int]] = {}


def _collect_reads(nodes, top_level=True):
    """收集模块级的名字读取；top_level=False 时是某个 if 块内部。"""
    for _n in nodes:
        for _sub in _ast.walk(_n):
            if isinstance(_sub, _ast.Name) and isinstance(_sub.ctx, _ast.Load):
                _reads_outside.setdefault(_sub.id, []).append(_sub.lineno)


_risky: dict[str, list[int]] = {}
for _node in _tree.body:
    if isinstance(_node, _ast.If):
        # 有 else 分支 → 两条路都会赋值，安全
        if _node.orelse:
            continue
        _names_here = set()
        for _sub in _ast.walk(_node):
            if isinstance(_sub, _ast.Assign):
                for _t in _sub.targets:
                    if isinstance(_t, _ast.Name):
                        _names_here.add(_t.id)
        for _nm in _names_here:
            _risky.setdefault(_nm, []).append(_node.lineno)

# 收集：所有"if 之外"的模块级赋值行号
_safe: set[str] = set()
for _node in _tree.body:
    if not isinstance(_node, _ast.If):
        for _sub in _ast.walk(_node):
            if isinstance(_sub, _ast.Assign):
                for _t in _sub.targets:
                    if isinstance(_t, _ast.Name):
                        _safe.add(_t.id)

# 只收集 if 块**之外**的读取行号（块内自产自销不算问题）
for _node in _tree.body:
    if not isinstance(_node, _ast.If):
        _collect_reads([_node])
_outside_reads = {k: v for k, v in _reads_outside.items()}
# 块内读取全部剔除
for _node in _tree.body:
    if isinstance(_node, _ast.If):
        for _sub in _ast.walk(_node):
            if isinstance(_sub, _ast.Name) and isinstance(_sub.ctx, _ast.Load):
                _outside_reads.pop(_sub.id, None)

_bad = {k: v for k, v in _risky.items()
        if k not in _safe and k in _outside_reads}
ck(not _bad,
   "live.vpy 里没有「只在无 else 的 if 块内赋值、却在块外被读」的模块级变量"
   "（这正是 _CPU_VFI_FORCED_HR 那个 bug 的形态）",
   str({k: f"if 在第 {v} 行，块外读在第 {_outside_reads.get(k)} 行"
        for k, v in _bad.items()}))

# 再点名确认那个具体的变量确实有默认值（防止有人把它挪回 if 里）
_has_default = any(
    isinstance(n, _ast.Assign)
    for n in _tree.body
    if not isinstance(n, _ast.If)
    for t in getattr(n, "targets", [])
    if isinstance(t, _ast.Name) and t.id == "_CPU_VFI_FORCED_HR"
)
ck(_has_default,
   "`_CPU_VFI_FORCED_HR` 在模块级有默认赋值（不再依赖 auto 分支）")

# ORDER 的三个取值都必须能在源码里找到对应处理（hr/lr/auto）
for _o in ("auto", "hr", "lr"):
    ck(f'"{_o}"' in _LV, f"live.vpy 里有对 ORDER={_o} 的处理")

# ★★ 2026-09-30 加：参数区（warn/log 定义之前）**不得调用它们**
#   踩过的坑：给 PLAY_MAX_EDGE 加合法值校验时写在了常量区（~226 行），
#   而 `warn` 定义在 ~397 行 → `NameError: name 'warn' is not defined`
#   → **整条滤镜被 mpv 禁用**（用户看到"滤镜没生效"，且错误信息藏在
#   `[vapoursynth] NameError...` 里，不在 [live] 前缀下）。
#   正解是照 `_MATRIX_BAD` 的模式：常量区只**记录**，warn 定义后再报。
_early_defs = {n.name: n.lineno for n in _tree.body
               if isinstance(n, _ast.FunctionDef) and n.name in ("log", "warn")}
if _early_defs:
    _guard_line = min(_early_defs.values())
    _early_calls = []
    for _n in _ast.walk(_tree):
        if isinstance(_n, _ast.Call) and isinstance(_n.func, _ast.Name):
            if _n.func.id in ("log", "warn") and _n.lineno < _guard_line:
                _early_calls.append((_n.func.id, _n.lineno))
    ck(not _early_calls,
       "参数区（log/warn 定义之前）没有调用它们（否则 NameError 会禁用整条滤镜）",
       str(_early_calls))
    # 顺带确认延迟报警的机制真的在（_MATRIX_BAD 那个模式）
    ck("_PLAY_EDGE_BAD" in _LV and "_MATRIX_BAD" in _LV,
       "非法参数走『常量区记录 → warn 定义后统一报』的延迟报警模式")
else:
    ck(False, "live.vpy 里找不到 log/warn 定义（结构变了？）")

print()
print("== 15 VFR 源（rmvb 这类「容器帧率造假」的）判定与传参 ==")
# 背景：这类源的容器帧率是假的（D.Gray-man：写 30fps，真实平均 18.71fps），
# live.vpy 开头的 AssumeFPS(container_fps) 会把逐帧时长**拍平** → 时间轴按假帧率
# 走 → 与音频持续漂移（实测 400 帧：不拍平 17.0s / 拍平 13.0s）。
# 修法：GUI 判到 VFR 就置 LIVE_VFR=1，让 live.vpy 跳过 AssumeFPS。详见 build_launch。
_cfg_v = w._collect_cfg()
_env_v, _arg_v = G.build_launch(_cfg_v, ["X:/a.rmvb"], r"\\.\pipe\t", vfr=True)
ck(_env_v.get("LIVE_VFR") == "1", "vfr=True → env LIVE_VFR=1", _env_v.get("LIVE_VFR"))
ck(not any("container-fps-override" in a for a in _arg_v),
   "vfr=True 但没给实测帧率 → 不覆盖容器帧率")
_arg_r = G.build_launch(_cfg_v, ["X:/a.rmvb"], r"\\.\pipe\t",
                        vfr=True, real_fps=22.8)[1]
ck(any("--container-fps-override=22.8" in a for a in _arg_r),
   "vfr=True + real_fps → 覆盖容器帧率（只修统计页显示，不碰时间轴）",
   str([a for a in _arg_r if "override" in a]))
ck(not any("container-fps-override" in a for a in
           G.build_launch(_cfg_v, ["X:/a.mkv"], r"\\.\pipe\t")[1]),
   "默认（不传 vfr）也不传 --container-fps-override")
_env_c, _ = G.build_launch(_cfg_v, ["X:/a.mkv"], r"\\.\pipe\t", vfr=False)
ck(_env_c.get("LIVE_VFR") == "0", "vfr=False → env LIVE_VFR=0，正常 CFR 行为不变",
   _env_c.get("LIVE_VFR"))
ck("LIVE_VFR" in _LV, "live.vpy 里读 LIVE_VFR")
# ⚠ 不能拿 `_LV.split("AssumeFPS")` 判 —— 文件头的 env 说明里也写着 "跳过 AssumeFPS"。
#   要按**实际调用** `src.std.AssumeFPS(` 的位置比。
_pos_branch = _LV.find("_VFR_SRC = str(")
_pos_assume = _LV.find("src.std.AssumeFPS(")
ck(-1 < _pos_branch < _pos_assume,
   "live.vpy 的 _VFR_SRC 分支排在 src.std.AssumeFPS() 调用之前（否则等于没修）",
   f"_VFR_SRC@{_pos_branch} / AssumeFPS@{_pos_assume}")
ck("elif src.fps_num == 0 or src.fps_den == 0:" in _LV,
   "AssumeFPS 兜底仍保留（给正常 CFR 源用）")
ck(hasattr(G.ProbeWorker, "_probe_vfr") and not hasattr(G.ProbeWorker, "_probe_real_fps"),
   "ProbeWorker 用 _probe_vfr（判 VFR），旧的 _probe_real_fps 已撤")
# ═════════════════════════════════════════════════ 16 按源尺寸现编引擎（LIVE_SR_BUILD）
print("== 16 按源尺寸现编引擎（LIVE_SR_BUILD）==")
_LG = (ROOT / "live_gui.py").read_text(encoding="utf-8")
ck('SR_BUILD = _flag("LIVE_SR_BUILD", True)' in _LV, "LIVE_SR_BUILD 默认开")
ck("def _may_build_src_engine(" in _LV and "def _ensure_src_engine(" in _LV,
   "现编的门禁与入口都已定义")
_bld = _LV.split("def _may_build_src_engine(")[1].split("def _ensure_src_engine(")[0]
ck("SR_NATIVE_SCALE != 2" in _bld, "4x 模型不现编（源×4 输出太大）")
ck("_SR_BUILD_MIN_EDGE <= e <= _SR_BUILD_MAX_EDGE" in _bld,
   "有短边上下限（防 4K 级源编出 8K）")
ck("SR_MAX > 0 and" in _bld, "设了 LIVE_SR_MAX 且源超限 → 不现编（尊重限档意图）")
ck("IS_A4K" in _bld, "Anime4KCPP 路线不走引擎现编")
# ★ 现编的档位会进 LADDER，兜底若按"面积最大"挑就会被它抢走（2242x1080 面积 >
#   1920x1080，而两者比例不同 → 4K 源被横向拉伸）：必须"比例接近里取面积最大"。
#   两处实现必须一致，否则 GUI 预估卡和真跑出来的档位不一样。
ck("_near = [t for t in pool if abs(t[0] / t[1] - ar) <= _tol]" in _LV,
   "vpy 侧选档兜底：比例接近的档里取面积最大")
ck("_near = [t for t in lad if abs(t[0] / t[1] - ar) <= _tol]" in _LG,
   "GUI 侧选档兜底与 vpy 一致")
ck("max(pool, key=lambda t: t[0] * t[1])" not in _LV.split("def pick_ladder(")[1][:3000],
   "vpy 侧「按面积挑最大档」的旧兜底已撤")
ck((ROOT / "sr_engine.py").is_file(), "sr_engine.py 存在（现编实现）")
# 行为面：4K 源（16:9）不能被"面积更大但比例不同"的档位抢走
ck(G.pick_ladder(3840, 2160, "realesr-animevideov3_re2x", 0) == (1920, 1080),
   "4K 源选 1920x1080（不被非同比例的现存档位抢走）")
# 统计面板的属性选择（用错会显示源尺寸/恒定帧率，2026-10-02 修）
ck('9: "video-out-params/w"' in _LG, "统计面板取滤镜链之后的尺寸（不是源尺寸）")
ck("def _real_fps(" in _LG and "estimated-frame-number" in _LG,
   "有实测帧率（帧号差分）—— mpv 没有 estimated-display-fps 这个属性可用")

# ═════════════════════════════════════════════════ 17 把统计推到 mpv 画面（OSD）
print("== 17 OSD 统计（全屏时也能看到）==")
_args_osd_on = G.build_launch(dict(G.DEFAULTS, osd_stat=True),
                              ["a.mkv"], r"\\.\pipe\x")[1]
_args_osd_off = G.build_launch(dict(G.DEFAULTS, osd_stat=False),
                               ["a.mkv"], r"\\.\pipe\x")[1]
ck("--osd-level=3" in _args_osd_on,
   "开了「画面上显示统计」→ 加 --osd-level=3（否则 level 3 的 OSD 不显示）")
ck("--osd-level=3" not in _args_osd_off, "关掉 → 不加该参数")
ck(hasattr(G.MpvStat, "push"),
   "MpvStat 有 push()（读线程独占管道，写必须来自另一个线程）")
# OSD 文本必须是**纯文本**（OSD 不认 HTML），且含关键指标
_ol = w._osd_line({"video-out-params/w": 2560, "video-out-params/h": 1440,
                   "display-fps": 144.0, "frame-drop-count": 137},
                  23.9, 12.0, 23.4)
ck("<" not in _ol and ">" not in _ol, "OSD 行是纯文本（不带 HTML 标签）", _ol)
ck("实时 23.4 fps" in _ol and "2560×1440" in _ol and "屏 144Hz" in _ol,
   "OSD 行含实测帧率 / 输出分辨率 / 刷新率")
ck("丢帧 12.0/s" in _ol and "累计 137" in _ol, "有丢帧时 OSD 带速率与累计")
_ol2 = w._osd_line({"video-out-params/w": 2560, "video-out-params/h": 1440},
                   23.9, 0.0, None)
ck("丢帧" not in _ol2 and "实时" not in _ol2,
   "没丢帧/没实测帧率时不硬塞这两段")
# ★ 第二行：GPU 利用率/显存 + CPU 利用率 + 硬件型号（SysStat 后台采集；
#   采集线程不在时整行不出现，不能硬塞空行）
class _FakeSys:
    snapshot = {"gpu_util": 21, "gpu_mem_used": 1005, "gpu_mem_total": 16376,
                "cpu_util": 12}
w._sys = _FakeSys()
_ol3 = w._osd_line({"video-out-params/w": 2560, "video-out-params/h": 1440,
                    "display-fps": 144.0}, 23.9, 0.0, 23.4)
ck("GPU 21%" in _ol3 and "1.0/16.0G" in _ol3 and "CPU 12%" in _ol3,
   "OSD 第二行含 GPU 利用率/显存/CPU 利用率", _ol3)
ck("RTX 4080" in _ol3 and "i7-12700KF" in _ol3,
   "OSD 第二行含硬件短名（NVIDIA/GeForce 前缀已剥）")
ck("\n" in _ol3, "GPU/CPU 在第二行（show-text 的 \\n 会渲染成换行）")
w._sys = None
_ol4 = w._osd_line({"video-out-params/w": 2560, "video-out-params/h": 1440},
                   23.9, 0.0, 23.4)
ck("\n" not in _ol4, "采集线程不在 → 不出第二行（不硬塞）")
ck(hasattr(G, "SysStat") and hasattr(G.SysStat, "run"),
   "SysStat 采集线程存在（GPU 用 nvidia-smi 子进程、CPU 用 psutil）")

if G.CFG_PATH.exists():
    G.CFG_PATH.unlink()
# 后面几段又 record 过 → 落盘文件会重新出现，收尾一并清掉（别在用户目录里留垃圾）
for _p in (ROOT / "_t_bench_local.json", ROOT / "_t_bench_report.txt"):
    _p.unlink(missing_ok=True)
print()
if FAILS:
    print("✗ 失败 " + str(len(FAILS)) + " 项：" + str(FAILS))
    app.quit()
    sys.exit(1)
print("✓ 全部通过　共 " + str(STAT["ok"]) + " 项")
app.quit()
sys.exit(0)
