"""超分引擎切换（TRT / Anime4KCPP）的离屏 UI 探针。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication           # noqa: E402
from PySide6.QtGui import QFontMetrics                # noqa: E402
import live_gui                                       # noqa: E402

app = QApplication(sys.argv)
w = live_gui.MainWindow()
w.lst_files.clear()
w.media.clear()

print("=== 引擎下拉 ===")
for i in range(w.cb_sreng.count()):
    print(f"  [{i}] {w.cb_sreng.itemText(i)!r}  data={w.cb_sreng.itemData(i)!r}")

print("\n=== 默认（TRT）模型下拉 ===")
n_trt = w.cb_sr.count()
for i in range(n_trt):
    print(f"  [{i}] {w.cb_sr.itemText(i)[:52]!r:56s} {w.cb_sr.itemData(i)!r}")

print("\n=== 切到 Anime4KCPP ===")
w.cb_sreng.setCurrentIndex(1)
app.processEvents()
print(f"  模型下拉项数 {w.cb_sr.count()}（CTRL: 应为 12）")
for i in range(w.cb_sr.count()):
    print(f"  [{i}] {w.cb_sr.itemText(i)!r}  data={w.cb_sr.itemData(i)!r}")
print(f"  当前选中 = {w.cb_sr.currentData()!r}")
print(f"  导入分辨率上限 可用 = {w.cb_srmax.isEnabled()}（CTRL: False）")
print(f"  引擎标签 = {w.lb_engines.text()[:110]}")

print("\n=== 计划卡（A4K）===")
w.chk_up.setChecked(True)
w.chk_ip.setChecked(False)
# ⚠ 「输出倍率」控件 2026-09-30 已删除（与「限定播放分辨率」职责重叠）。
#   A4K 恒原生 2x；这里改用 cap 演示尺寸控制。
w.rb_cap_custom.setChecked(True)
w.sp_cap.setValue(0)               # 先不限，看原生 2x 输出
app.processEvents()
print("   [不限 cap] ", w.lb_plan.text().replace("<br>", " | ")[:200])
w.sp_cap.setValue(1080)            # 再压到 1080p
app.processEvents()
print("   [cap=1080] ", w.lb_plan.text().replace("<br>", " | ")[:200])
w.rb_cap_preset.setChecked(True)
w.cb_play_h.setCurrentIndex(w.cb_play_h.findData(0))

print("\n=== 回到 TRT ===")
w.cb_sreng.setCurrentIndex(0)
app.processEvents()
print(f"  模型下拉项数 {w.cb_sr.count()}（CTRL: {n_trt}）")
print(f"  导入分辨率上限 可用 = {w.cb_srmax.isEnabled()}（CTRL: True）")

print("\n=== launch env（A4K）===")
w.cb_sreng.setCurrentIndex(1)
app.processEvents()
cfg = w._collect_cfg()
env, args = live_gui.build_launch(cfg, [], "dummy")
print(f"  LIVE_SR = {env['LIVE_SR']!r}")
print(f"  LIVE_SR_MAX = {env['LIVE_SR_MAX']!r}")
# ★ 2026-09-30：LIVE_OUT_SCALE 已删除（输出 = 源 × 模型原生倍率）——
#   断言它**不在** env 里，防止有人手滑加回来。
print(f"  LIVE_OUT_SCALE 是否存在 = {'LIVE_OUT_SCALE' in env}"
      f"（期望 False）")
assert "LIVE_OUT_SCALE" not in env, "LIVE_OUT_SCALE 不该再出现在 launch env 里"

print("\n=== estimate() ===")
for sr in ("anime4kcpp:acnet-gan", "anime4kcpp:f8b4", "anime4kcpp:arnet-f8b8"):
    ms, tag = live_gui.estimate("hr", sr, True, False, 1280, 720, 0)
    print(f"  {sr:26s} {ms:7.2f} ms  {tag}")
for sr in ("animev3", "realesr-animevideov3_re2x"):
    ms, tag = live_gui.estimate("hr", sr, True, False, 1280, 720, 0)
    print(f"  {sr:26s} {ms:7.2f} ms  {tag}")

print("\n=== 配置往返（A4K 存盘再读回）===")
w.cb_sr.setCurrentIndex(4)     # 第 5 个 A4K 模型
app.processEvents()
want = w.cb_sr.currentData()
c = w._collect_cfg()
saved = dict(live_gui.DEFAULTS)
saved.update(c)
w.cfg = saved
w._restore_cfg()
got = w.cb_sr.currentData()
print(f"  存 {want!r} → 读回 {got!r}  {'OK' if want == got else 'FAIL'}")
print(f"  引擎读回 = {w.cb_sreng.currentData()!r}  {'OK' if w.cb_sreng.currentData()=='a4k' else 'FAIL'}")

# ══════════════════════════════════════════════════════════════════
# 布局回归：模型名**必须放得下**（用户明确反馈过"看不清模型名"）
# ══════════════════════════════════════════════════════════════════
print("\n=== 布局：下拉文字不被截断 ===")
w.resize(1680, 1148)
w.show()
for _ in range(10):
    app.processEvents()
bad = 0
for eng, name in ((0, "TensorRT"), (1, "Anime4KCPP")):
    w.cb_sreng.setCurrentIndex(eng)
    for _ in range(8):
        app.processEvents()
    fm = QFontMetrics(w.cb_sr.font())
    worst = max(range(w.cb_sr.count()),
                key=lambda i: fm.horizontalAdvance(w.cb_sr.itemText(i)))
    txt = w.cb_sr.itemText(worst)
    need = fm.horizontalAdvance(txt) + 40         # +箭头 +内边距
    ok = w.cb_sr.width() >= need
    bad += 0 if ok else 1
    print(f"  [{'OK' if ok else 'FAIL'}] {name:10s} 下拉 {w.cb_sr.width()}px "
          f"≥ 需 {need}px   「{txt}」")

print("\n=== 布局：参数区不需要滚动 ===")
# ★ 必须 show() + 多轮 processEvents，否则量到的是未布局的中间态。
# ★ 而且要在**刚把引擎切回 TRT 并再切一次**之后量 —— 前面为了验证换表
#   来回切过引擎、还跑过 _restore_cfg，那些中间态会让 wordWrap 标签的
#   sizeHint 停在偏大的值（曾虚报 723 vs 真实 691）。
from PySide6.QtWidgets import QScrollArea              # noqa: E402
w.cb_sreng.setCurrentIndex(0)
w.cb_sreng.setCurrentIndex(1)
w.resize(1680, 1148)
w.show()
for _ in range(20):
    app.processEvents()
w.chk_up.setChecked(True)
w.chk_ip.setChecked(True)          # 两个开关都开 = 内容最多的情况
for _ in range(20):
    app.processEvents()
sa = w.findChild(QScrollArea, "rightScroll")
vp, need_h = sa.viewport().height(), sa.widget().sizeHint().height()
# ★★ 容差必须区分平台：**offscreen 没有真实字体度量**，wordWrap 标签折行结果
#   和真屏不一样（实测同一配置 offscreen 723px / windows 691px，差 32px）。
#   所以 offscreen 下只做"大致装得下"的弱断言，强断言交给真屏；
#   真屏的判据是「≤ 滚动条厚度（~16px）」，即实际上不会滚。
_offscreen = os.environ.get("QT_QPA_PLATFORM") == "offscreen"
_tol = 48 if _offscreen else 20
ok_vp = vp >= need_h - _tol
print(f"  [{'OK' if ok_vp else 'FAIL'}] 1680x1148 开满两开关："
      f"视口 {vp}px vs 内容 {need_h}px"
      f"（容差 {_tol}px{'' if not _offscreen else '，offscreen 字体度量不准'}）")
bad += 0 if ok_vp else 1
# 大屏应完全不用滚
w.resize(2560, 1400)
for _ in range(12):
    app.processEvents()
vp2, nd2 = sa.viewport().height(), sa.widget().sizeHint().height()
ok_big = vp2 >= nd2
print(f"  [{'OK' if ok_big else 'FAIL'}] 2560x1400 全屏："
      f"视口 {vp2}px vs 内容 {nd2}px（应完全不用滚）")
bad += 0 if ok_big else 1

print("\n=== 布局：日志被压扁（用户要求）===")
from PySide6.QtWidgets import QSplitter                 # noqa: E402
sp = w.centralWidget().findChild(QSplitter)
top_h, log_h = sp.sizes()
ratio = log_h / (top_h + log_h)
ok_r = ratio <= 0.20
print(f"  [{'OK' if ok_r else 'FAIL'}] 日志占比 {ratio * 100:.1f}%（目标 ≤20%）"
      f"  上半 {top_h}px / 日志 {log_h}px")
bad += 0 if ok_r else 1

print(f"\n{'✓ 布局全部通过' if not bad else f'✗ {bad} 项布局检查失败'}")
sys.exit(1 if bad else 0)
