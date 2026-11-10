import os

os.environ["PYAPPIFY_PYTHON_TEST"] = "1"
if __name__ == '__main__':
    from config import config
    from ok import OK

    try:
        from src.wgc_enable import install_wgc_patch
        install_wgc_patch()
    except Exception as e:
        print(f"[capture] WGC gate patch failed, fallback: {e}")

    config = config
    config['debug'] = True
    # config['click_screenshots_folder'] = "click_screenshots"  # debug用 点击后截图文件夹]
    ok = OK(config)
    ok.start()
