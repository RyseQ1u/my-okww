if __name__ == '__main__':
    from config import config
    from ok import OK

    # 框架把 WGC_NO_BORDER_MIN_BUILD(20348) 误当作「WGC 可用性」阈值，
    # 使 Win10 19045 上 WGC 被剔除。按 WGC_MIN_BUILD(19041) 放行，
    # 必须在 OK(config) 实例化前替换框架内所有门禁引用。
    try:
        from src.wgc_enable import install_wgc_patch
        install_wgc_patch()
        print("[capture] WGC gate enabled (min build 19041)")
    except Exception as e:  # 放行失败不阻断启动，自动回落到桌面 DC
        print(f"[capture] WGC gate patch failed, fallback: {e}")

    # Win10 19045 上 BitBlt(窗口DC) 会返回 DWM 冻结旧帧，且复用 GDI 对象会在
    # 跨线程截图时崩溃。启动时全局改为「桌面 DC + 每次自建自清 + 线程锁」，
    # 让 GUI 截图按钮、OCR、任务截图日志统一拿到实时画面。
    try:
        from src.desktop_dc_capture import install_global_patch
        install_global_patch()
        print("[capture] desktop DC patch installed")
    except Exception as e:  # 打补丁失败不阻断启动，退回框架原方式
        print(f"[capture] desktop DC patch failed, fallback: {e}")

    ok = OK(config)
    ok.start()
