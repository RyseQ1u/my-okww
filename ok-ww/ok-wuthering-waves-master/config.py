import os
import re
from pathlib import Path

from ok import Box, ConfigOption, Icon
from ok.util.GlobalConfig import create_basic_options
from src.game_launcher import get_game_launch_arguments
from src.task.process_feature import process_feature

version = "dev"


def _find_most_recently_run_pc_exe():
    try:
        import codecs
        import struct
        import winreg
    except ImportError:
        return None

    user_assist_key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
    candidates = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, user_assist_key) as root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                guid = winreg.EnumKey(root, index)
                try:
                    with winreg.OpenKey(root, fr"{guid}\Count") as count_key:
                        for value_index in range(winreg.QueryInfoKey(count_key)[1]):
                            encoded_path, data, _ = winreg.EnumValue(count_key, value_index)
                            path = os.path.expandvars(codecs.decode(encoded_path, "rot_13"))
                            if not path.casefold().endswith(r"\wuthering waves.exe"):
                                continue
                            if not Path(path).is_file():
                                continue
                            last_run = struct.unpack_from("<Q", data, 60)[0] if len(data) >= 68 else 0
                            candidates.append((last_run, path))
                except OSError:
                    continue
    except OSError:
        return None

    return max(candidates, default=(0, None))[1]


def _find_pc_exe_from_registry():
    try:
        import winreg
    except ImportError:
        return None

    uninstall_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    registry_views = (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in registry_views:
            try:
                with winreg.OpenKey(root, uninstall_key, 0, winreg.KEY_READ | view) as key:
                    subkey_count = winreg.QueryInfoKey(key)[0]
                    for index in range(subkey_count):
                        try:
                            subkey_name = winreg.EnumKey(key, index)
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                display_name = _read_registry_value(subkey, "DisplayName", winreg)
                                if not _is_wuthering_waves_registry_entry(subkey_name, display_name):
                                    continue
                                for value_name in (
                                        "InstallPath", "InstallLocation", "LauncherPath",
                                        "DisplayIcon", "UninstallString"):
                                    registered_path = _read_registry_value(subkey, value_name, winreg)
                                    if game_exe := _find_pc_exe_near_registered_path(registered_path):
                                        return game_exe
                        except OSError:
                            continue
            except OSError:
                continue
    return None


def _read_registry_value(key, name, winreg):
    try:
        return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def _is_wuthering_waves_registry_entry(subkey_name, display_name):
    names = f"{subkey_name} {display_name}".casefold()
    return "wuthering waves" in names or "鸣潮" in names or "鳴潮" in names


def _find_pc_exe_near_registered_path(registered_path):
    if not registered_path:
        return None

    path_text = os.path.expandvars(registered_path.strip().strip('"'))
    exe_end = path_text.casefold().find(".exe")
    if exe_end >= 0:
        path_text = path_text[:exe_end + 4]

    registered = Path(path_text)
    install_folder = registered.parent if registered.suffix.casefold() == ".exe" else registered
    candidates = (
        install_folder / "Wuthering Waves.exe",
        install_folder / "Wuthering Waves Game" / "Wuthering Waves.exe",
        install_folder.parent / "Wuthering Waves Game" / "Wuthering Waves.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def calculate_pc_exe_path(running_path):
    if running_path is None:
        return _find_most_recently_run_pc_exe() or _find_pc_exe_from_registry()
    game_exe_folder = Path(running_path).parents[3]
    return str(game_exe_folder / "Wuthering Waves.exe")


def blur_area(width, height):
    blur_width = int(0.12 * width)
    blur_height = int(0.024 * height)
    return Box(width * 0.879, height * 0.976, blur_width * 0.973, blur_height * 0.994)


basic_config_option = create_basic_options(enable_blur=True)

key_config_option = ConfigOption('Game Hotkey', {
    'Echo Key': 'q',
    'Liberation Key': 'r',
    'Resonance Key': 'e',
    'Tool Key': 't',
    'Jump Key': 'space',
    'Dodge Key': 'lshift',
    'Wheel Key': 'tab',
    'Guidebook Key': 'f2',
    'Bag Key': 'b',
}, description='In Game Hotkey for Skills', config_description={
    'Bag Key': 'In-game hotkey used to open the Bag.',
}, show_at_tab=True, icon=Icon.GAME)

# BaseWWTask.__init__ 会读取 'Character Config'，属于基类运行期依赖，
# 即使分支不含任何角色战斗功能也必须保留。
char_config_option = ConfigOption('Character Config', {
    'Iuno C6': False,
    'Roccia C6': False,
    'Chisa DPS': False,
    'Suoming DPS': False,
}, description='Character Config', show_at_tab=True, icon=Icon.PEOPLE)

monthly_card_config_option = ConfigOption('Monthly Card Config', {
    'Check Monthly Card': True,
    'Monthly Card Time': 4
}, description='Turn on to avoid interruption by monthly card when executing tasks', config_description={
    'Check Monthly Card': 'Check for monthly card to avoid interruption of tasks',
    'Monthly Card Time': 'Your computer\'s local time when the monthly card will popup, hour in (1-24)'
})

config = {
    'debug': False,  # Optional, default: False
    'custom_tasks': True,
    "gui": {
        "type": "qt",
    },
    'config_folder': 'configs',
    'blur_area': blur_area,
    'gui_icon': 'icons/icon.png',
    'global_configs': [basic_config_option, key_config_option, char_config_option, monthly_card_config_option],
    'custom_tabs': [],
    'ocr': {
        'lib': 'onnxocr',
        'auto_simplify': True,
        'params': {
            'use_openvino': True,
            'use_npu': True,
        }
    },
    'plugin':True,
    'my_app': ['src.globals', 'Globals'],
    'start_timeout': 120,  # default 60
    'wait_until_settle_time': 0,
    # required if using feature detection
    'template_matching': {
        'template_folder': os.path.join('assets', 'templates'),
        'default_horizontal_variance': 0.002,
        'default_vertical_variance': 0.002,
        'default_threshold': 0.8,
        'feature_processor': process_feature,
        'vcenter_features': ['monthly_card', 'skip_dialog_check'],
        'hcenter_features': ['monthly_card', 'suisui_forte3', 'message_dialog', 'claim_stamina_sign',
                             'skip_dialog_check', 'skip_quest_confirm', 'skip_quest_confirm_new',
                             'login_close', 'garden_confirm', 'garden_continue_game',
                             'garden_unpause', 'garden_get_gold', 'garden_get_purple', 'garden_get_skip',
                             'garden_not_interested_confirm', 'garden_not_interested', 'a_garden_back',
                             'garden_get_confirm_gray', 'the_garden_max', 'garden_shop_close', 'garden_new_stage',
                             'a_garden_restart', 'suisui_forte2', 'e_forte', 'f_break_full']
    },
    'windows': {  # required  when supporting windows game
        'top_hwnd_class': [re.compile('CAgreementDlg'), re.compile('CLoginDlg_P_'),
                           'CefBrowserWindow', 'Chrome_RenderWidgetHostHWND', '#32770',
                           re.compile('CNativeLoginDlg'), 'Static', 'ComboBox', 'ComboLBox', 'Button'
                           ],
        'calculate_pc_exe_path': calculate_pc_exe_path,
        'launch_arguments': get_game_launch_arguments,
        'exe': 'Client-Win64-Shipping.exe',
        'hwnd_class': 'UnrealWindow',
        'interaction': 'PostMessage',
        'capture_method': ['WGC', 'WGC_HDR','BitBlt'],  # Windows版本支持的话, 优先使用WGC, 否则使用普通BitBlt(屏幕合成图, 对D3D游戏可用; RenderFull/PrintWindow会出黑屏)
        'check_hdr': False,
        'force_no_hdr': False,
        'check_auto_hdr': True,
        'check_night_light': True,
        'force_no_night_light': False,
    },
    'window_size': {
        'width': 1200,
        'height': 800,
        'min_width': 1200,
        'min_height': 800,
    },
    'supported_resolution': {
        'ratio': '16:9',
        'resize_to': [(2560, 1440), (1920, 1080), (1600, 900), (1280, 720)],
        'min_size': (1280, 720)
    },
    'links': {
        'default': {
            'github': 'https://github.com/ok-oldking/ok-wuthering-waves',
            'discord': 'https://discord.gg/vVyCatEBgA',
            'download': 'https://ok-script.com/ok-ww',
            'sponsor': 'https://patreon.com/ok_oldking',
            'share': 'Download OK-WW from https://ok-script.com/ok-ww',
            'faq': 'https://ok-script.com/ok-ww/'
        },
        'zh_CN': {
            'github': 'https://github.com/ok-oldking/ok-wuthering-waves',
            'discord': 'https://discord.gg/vVyCatEBgA',
            'sponsor': 'https://afdian.com/a/ok-oldking',
            'share': '下载okww https://ok-script.com/ok-ww',
            'faq': 'https://ok-script.com/ok-ww',
            'qq_group': 'https://qm.qq.com/q/jw2NltIEWA',
            'qq_channel': 'https://pd.qq.com/s/djmm6l44y',
        },
    },
    'about': """
    <p><strong>echo-only 分支</strong>：仅保留「声骸升级辅助」功能，其余 OK-WW 功能已移除。</p>
    <p style="color:red;">
        <strong>本软件仅供个人学习使用；第三方工具存在封号风险。</strong>
    </p>
""",
    'screenshots_folder': "screenshots",
    'gui_title': 'OK-WW Echo Upgrade',
    # 'coco_feature_folder': get_path(__file__, 'assets/coco_feature'),  # required if using feature detection
    'log_file': 'logs/ok-ww.log',  # Optional, auto rotating every day
    'error_log_file': 'logs/ok-ww_error.log',
    'launcher_log_file': 'logs/launcher.log',
    'launcher_error_log_file': 'logs/launcher_error.log',
    'version': version,
    # 定制版：只注册升级声骸任务，其余上游功能全部移除。
    'onetime_tasks': [
        ["src.task.UpgradeEchoTask", "UpgradeEchoTask"],
    ],
    'trigger_tasks': [],
    'scene': ["src.scene.WWScene", "WWScene"],

}
