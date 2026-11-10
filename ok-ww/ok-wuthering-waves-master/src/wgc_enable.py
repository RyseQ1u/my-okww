# -*- coding: utf-8 -*-
"""放行 ok-script 的 Windows.Graphics.Capture 版本门禁。

背景
----
ok-script 2.0.20 的 ``ok.util.window.windows_graphics_available()`` 把
``WGC_NO_BORDER_MIN_BUILD`` (20348, Win11/Server 2022「可关闭黄框」的阈值)
误当成「WGC 是否可用」的阈值，导致 Win10 19045 上 WGC 被剔除、回落 BitBlt。
按 HWND 捕获的实际门槛是同文件已定义的 ``WGC_MIN_BUILD`` (19041)。

本机实测：build 19045.6466，``GraphicsCaptureSession.IsSupported() = True``；
框架自带的 ``WindowsGraphicsCaptureMethod`` 在目标窗口被不透明窗口完全遮挡时
仍能连续给出该窗口自己的实时帧（见 logs/wgc_front.png / logs/wgc_occluded.png）。

做法
----
启动期把门禁函数替换为按 19041 判定的版本，并覆盖框架内所有静态绑定引用；
不改 site-packages（重装即丢），**不改 WINDOWS_BUILD_NUMBER**（改它会误触发
``IsBorderRequired=False`` 分支）。Win10 上黄色提示框仍会显示，但不进入采集帧。

Win10 的 WGC 会话在 STA 主线程建立时，首帧 ``frame_pool.Recreate`` 会抛一次
``0x8001010E``；框架回调已 try/except 吞掉，后续帧正常，不影响功能。
"""
import importlib

from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

DEFAULT_WGC_MIN_BUILD = 19041

# 框架里所有读取该门禁的位置。ok/__init__.py 用 _resolve 动态解析 ok.util.window，
# 覆盖原模块即可；其余三处是 from ... import 的静态绑定，必须逐个替换。
_TARGETS = (
    ("ok.util.window", "windows_graphics_available"),
    ("ok.device.capture_methods.update", "windows_graphics_available"),
    ("ok.device.capture_methods.browser", "windows_graphics_available"),
    ("ok.device.DeviceManager", "windows_graphics_available"),
)

_originals = {}


def wgc_min_build():
    from ok.util import window
    return getattr(window, "WGC_MIN_BUILD", DEFAULT_WGC_MIN_BUILD)


def wgc_available():
    """按 WGC_MIN_BUILD 判定，保留框架原有的 WinRT 互操作校验。"""
    from ok.util import window
    build = getattr(window, "WINDOWS_BUILD_NUMBER", -1)
    threshold = wgc_min_build()
    if build < threshold:
        logger.info(f"WGC gate: build {build} < {threshold} -> unavailable")
        return False
    try:
        from ok.rotypes import idldsl  # noqa: F401  触发 WinRT 类型注册
        from ok.rotypes.roapi import GetActivationFactory
        from ok.rotypes.Windows.Graphics.Capture import IGraphicsCaptureItemInterop
        GetActivationFactory("Windows.Graphics.Capture.GraphicsCaptureItem").astype(
            IGraphicsCaptureItemInterop)
        logger.info(f"WGC gate: build {build} >= {threshold}, CreateForWindow OK -> enabled")
        return True
    except Exception as e:
        logger.error(f"WGC gate: WinRT interop check failed: {e}")
        return False


def install_wgc_patch():
    """幂等安装，返回本次实际替换的引用数。必须在 ``OK(config)`` 之前调用。"""
    if _originals:
        return 0
    count = 0
    for mod_name, attr in _TARGETS:
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:
            logger.warning(f"WGC gate: cannot import {mod_name}: {e}")
            continue
        if not hasattr(mod, attr):
            logger.warning(f"WGC gate: {mod_name}.{attr} not found, skipped")
            continue
        _originals[(mod_name, attr)] = getattr(mod, attr)
        setattr(mod, attr, wgc_available)
        count += 1
    logger.info(f"WGC gate: patched {count}/{len(_TARGETS)} references "
                f"(min build {wgc_min_build()})")
    return count


def uninstall_wgc_patch():
    """还原所有引用（离线自测 / 运行时切换用）。"""
    for (mod_name, attr), original in list(_originals.items()):
        try:
            setattr(importlib.import_module(mod_name), attr, original)
        except Exception:
            pass
    count = len(_originals)
    _originals.clear()
    return count


def is_wgc_patch_installed():
    return bool(_originals)
