# -*- coding: utf-8 -*-
"""全局桌面 DC 抓帧补丁（Win10 19045 + 鸣潮 Unreal 窗口专用）。

背景（2026-11-09 实机实证）
----------------------------
框架默认的 ``BitBltCaptureMethod`` 通过 ``GetWindowDC(游戏窗口) + BitBlt`` 抓帧。
在这台机器上，游戏窗口处于后台时 DWM 会返回**缓存的旧窗口内容**：
  * 多次运行保存的截图 md5 完全相同，与实时画面平均像素差 101.9；
  * ``GetDC(0)``（桌面 DC）抓的屏幕合成结果与实时画面像素差仅 1.59。
结果就是 OCR 永远看到旧画面，页面探针恒失败。

另外，框架的 ``capture_by_bitblt`` 在 BitBlt context 上复用 GDI 对象
（bitmap / compatible DC），跨线程（GUI 截图按钮 vs 窗口刷新线程）并发时会抛
``Select bitmap object failed``，甚至触发 native access violation 导致进程退出。

本模块的对策
------------
1. 在**类级别**替换 ``BitBltCaptureMethod.do_get_frame``，全局生效——
   GUI 的「截图」按钮、OCR、任务截图日志都走同一条实时路径。
2. 抓帧用「每次自建、用完即释放」的桌面 DC，并加线程锁：
   不复用任何 GDI 对象，从根本上避开 SelectObject 崩溃与句柄泄漏。
3. 可逆：``uninstall_global_patch()`` 精确还原；重复调用幂等。

不修改 ok 框架源码，只在进程启动时打补丁。
"""
import threading

# 保护 GDI 创建/释放过程，避免与窗口刷新线程并发
_LOCK = threading.Lock()
_PATCHED_ATTR = "_ww_desktop_dc_patched"
_ORIGINAL_ATTR = "_ww_desktop_dc_original"


def resolve_capture_rect(hwnd_window):
    """算出游戏客户区在屏幕上的绝对矩形 (x, y, w, h)。

    与框架 ``ForegroundBitBltCaptureMethod`` 的坐标口径一致；实机验证
    x=958, y=33, 1600x900 可抓到正确的游戏画面。
    """
    x = hwnd_window.x + getattr(hwnd_window, "real_x_offset", 0)
    y = hwnd_window.y + getattr(hwnd_window, "real_y_offset", 0)
    w = getattr(hwnd_window, "real_width", 0) or hwnd_window.width
    h = getattr(hwnd_window, "real_height", 0) or hwnd_window.height
    return int(x), int(y), int(w), int(h)


def capture_desktop(x, y, w, h):
    """抓屏幕矩形，返回 BGRA ``numpy.uint8`` 数组 (h, w, 4)；失败返回 None。

    公开为模块级函数，便于离线测试注入替身而不触碰真实 GDI。
    """
    return _capture_desktop_impl(x, y, w, h)


def _capture_desktop_impl(x, y, w, h):
    if w <= 0 or h <= 0:
        return None
    with _LOCK:
        hdc = dc = cdc = bmp = None
        try:
            import numpy as np
            import win32con
            import win32gui
            import win32ui

            hdc = win32gui.GetDC(0)                      # 桌面 DC
            dc = win32ui.CreateDCFromHandle(hdc)
            cdc = dc.CreateCompatibleDC()
            bmp = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(dc, w, h)
            cdc.SelectObject(bmp)
            cdc.BitBlt((0, 0), (w, h), dc, (x, y), win32con.SRCCOPY)
            arr = np.frombuffer(bmp.GetBitmapBits(True), dtype=np.uint8)
            return arr.reshape((h, w, 4)).copy()
        except Exception:
            return None
        finally:
            # 逆序释放；bitmap 仍被 cdc 选中时不能删，所以先删 DC。
            # 每步单独 try，保证任一步失败都不影响其余资源回收。
            _g = _win32gui()
            for cleanup in (
                lambda: cdc.DeleteDC() if cdc is not None else None,
                lambda: dc.DeleteDC() if dc is not None else None,
                lambda: _g.DeleteObject(bmp.GetHandle())
                if (bmp is not None and _g is not None) else None,
                lambda: _g.ReleaseDC(0, hdc)
                if (hdc and _g is not None) else None,
            ):
                try:
                    cleanup()
                except Exception:
                    pass


def _win32gui():
    try:
        import win32gui
        return win32gui
    except Exception:
        return None


def install_global_patch(method_class=None):
    """把 BitBlt 的抓帧全局改为桌面 DC。返回 True 表示本次生效，False 表示已装过。

    ``method_class`` 可注入，供离线测试使用。
    """
    if method_class is None:
        from ok.device.capture_methods.bitblt import BitBltCaptureMethod as method_class
    if getattr(method_class, _PATCHED_ATTR, False):
        return False
    original = method_class.do_get_frame

    def do_get_frame(self):
        try:
            x, y, w, h = resolve_capture_rect(self.hwnd_window)
            frame = capture_desktop(x, y, w, h)
            if frame is not None:
                return frame
        except Exception:
            pass
        # 桌面 DC 不可用时兜底回原实现，避免整个程序失去抓帧能力
        return original(self)

    method_class.do_get_frame = do_get_frame
    setattr(method_class, _ORIGINAL_ATTR, original)
    setattr(method_class, _PATCHED_ATTR, True)
    return True


def uninstall_global_patch(method_class=None):
    """还原类方法（幂等）。返回 True 表示确实还原了。"""
    if method_class is None:
        from ok.device.capture_methods.bitblt import BitBltCaptureMethod as method_class
    if not getattr(method_class, _PATCHED_ATTR, False):
        return False
    original = getattr(method_class, _ORIGINAL_ATTR, None)
    if original is not None:
        method_class.do_get_frame = original
    try:
        delattr(method_class, _ORIGINAL_ATTR)
    except Exception:
        pass
    try:
        delattr(method_class, _PATCHED_ATTR)
    except Exception:
        pass
    return True


def is_patched(method_class=None):
    if method_class is None:
        from ok.device.capture_methods.bitblt import BitBltCaptureMethod as method_class
    return bool(getattr(method_class, _PATCHED_ATTR, False))
