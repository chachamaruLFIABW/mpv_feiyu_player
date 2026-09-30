-- avsync_osd.lua —— 常驻 OSD：画面帧号 / 画面时间 / 音频时间 / 差值
-- Alt+Y 开关。给「点了进度条之后画面和声音对不对得上」提供一条能一直盯着的读数。
--
-- 读数含义：
--   画面帧    estimated-frame-number / estimated-frame-count
--             （mpv 按视频时钟推出来的第几帧；除以帧率就是画面时间）
--   画面时间  time-pos
--   音频时间  audio-pts（mpv 的音频时钟）
--   差值      音频时间 − 画面时间
--             明显为正 = 音频在前（声音比画面靠后=画面落后）
--             明显为负 = 音频在后
--   avsync    mpv 自报的同步误差（它拿自己两个时钟比，自洽时恒 ~0）
--
-- ⚠ 这四个数**全部来自 mpv 自己**。如果声音是「设备侧」晚出来的
--   （蓝牙/无线音箱的渲染缓冲，mpv 的时钟不含这一层），
--   这里会一路显示 0.00，而你耳朵听着还是不同步 —— 那就不是这里能看见的。

local mp = require 'mp'
local opts = require 'mp.options'

-- autostart：加载文件后自动开启（测试/诊断用：
--   --script-opts=avsync_osd-autostart=yes）。默认关，不影响日常观看。
local o = { autostart = false }
opts.read_options(o, "avsync_osd")

local visible = false
local timer = nil

local function n(v, fmt)
    if v == nil then return "—" end
    return string.format(fmt, v)
end

local function update()
    local fn = mp.get_property_number("estimated-frame-number")
    local fc = mp.get_property_number("estimated-frame-count")
    local tp = mp.get_property_number("time-pos")
    local ap = mp.get_property_number("audio-pts")
    local av = mp.get_property_number("avsync")
    local dr = mp.get_property_number("frame-drop-count")
    local vf = mp.get_property_number("estimated-vf-fps")

    local frame = "—"
    if fn ~= nil and fc ~= nil then
        frame = string.format("%d / %d", math.floor(fn + 0.5), math.floor(fc + 0.5))
    elseif fn ~= nil then
        frame = string.format("%d / ?", math.floor(fn + 0.5))
    end

    local diff = "—"
    if tp ~= nil and ap ~= nil then
        diff = string.format("%+.3f s", ap - tp)
    end

    -- ★ 必须把**实际 OSD 尺寸**当 ASS 脚本分辨率传进去。
    --   传 (0,0) 时 libass 按默认 384x288 解释，字和位置都会被放大 3~4 倍
    --   （实测 2560x1440 下 \fs22 渲染成 ~60px，screen 上糊一大片）。
    local dim = mp.get_property_native("osd-dimensions")
    local w = (dim and dim.w) or 1280
    local h = (dim and dim.h) or 720

    local t = "{\\an7\\pos(20,18)\\fs20\\bord1.5\\shad0}"
        .. "── 音画观察（Alt+Y 关） ──\\N"
        .. "画面帧     " .. frame .. "\\N"
        .. "画面时间   " .. n(tp, "%.3f") .. " s\\N"
        .. "音频时间   " .. n(ap, "%.3f") .. " s\\N"
        .. "差值(音−画) " .. diff .. "\\N"
        .. "avsync     " .. n(av, "%+.4f") .. " s    丢帧 " .. n(dr, "%d")
        .. "    滤镜 " .. n(vf, "%.1f") .. " fps"
    mp.set_osd_ass(w, h, t)
    return 0.2
end

local function tick()
    local wait = update()
    if visible and timer ~= nil then
        timer:kill()
        timer = mp.add_timeout(wait, tick)
    end
end

local function toggle()
    visible = not visible
    if visible then
        update()
        if timer ~= nil then timer:kill() end
        timer = mp.add_timeout(0.2, tick)
        mp.osd_message("音画观察：开（Alt+Y 关）", 1)
    else
        if timer ~= nil then timer:kill(); timer = nil end
        mp.set_osd_ass(0, 0, "")
        mp.osd_message("音画观察：关", 1)
    end
end

mp.add_key_binding("Alt+y", "toggle", toggle)

-- ⚠ 不能在 file-loaded 里立刻开：那时 VO 还没初始化完，
--   set_osd_ass 会被随后的 VO 初始化冲掉（实测 OSD 不显示）。延迟 3 秒。
local _auto = false
mp.register_event("file-loaded", function()
    if o.autostart and not _auto then
        _auto = true
        mp.add_timeout(3.0, function()
            if not visible then toggle() end
        end)
    end
end)
