# -*- coding: utf-8 -*-
"""
UpgradeEchoTask 离线自测（不依赖游戏/截图，纯逻辑 + mock）。
运行：D:\Dev\Python311\python.exe docs/echo-upgrade/code/test_offline.py
覆盖：页面探针、8 步固定流程、材料连点、收起详情、强化并调谐、调谐成功、
      副词条过滤、失败即停、安全闸。
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]          # .../fzgj
OKWW = REPO / "ok-ww" / "ok-wuthering-waves-master"
sys.path.insert(0, str(OKWW))

from types import SimpleNamespace

import src.task.UpgradeEchoTask as T
from src.task.UpgradeEchoTask import (UpgradeEchoTask, EchoStepRecorder,
                                      TRAIN_RE, CONFIRM_RE, MATERIAL_RE,
                                      SUCCESS_RE, SUBSTAT_KEYWORDS,
                                      DEBUG_ENTRIES, install_desktop_dc_capture)
from src.desktop_dc_capture import (resolve_capture_rect, capture_desktop,
                                    install_global_patch,
                                    uninstall_global_patch, is_patched)
import src.desktop_dc_capture as DDC

# ---------------------------------------------------------------- 计数
_PASS = 0
_FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


# ---------------------------------------------------------------- mock 基建
class Box:
    def __init__(self, x, y, w, h, name=""):
        self.x, self.y = x, y
        self.width, self.height = w, h
        self.name = name


class MockTask(UpgradeEchoTask):
    def __init__(self, screen_texts=None, substats_box=None):
        # 跳过 BaseWWTask 重逻辑，仅保留本任务需要的属性
        self.name = "mock"
        self.done_count = self.fail_count = self.skip_count = 0
        self.consecutive_fail = self.consecutive_skip = 0
        self.stop_reason = ""
        self.all_substats = []
        self.recorder = EchoStepRecorder(Path("/tmp/echo_upgrade_test"))
        self._finished = False
        self.config = {
            "材料品质": "特级", "材料数量": 8, "最大处理数量": 200,
            "单件超时(秒)": 120, "调试入口": "完整流程", "每步截图": True,
            "截图日志目录": "logs/echo_upgrade", "自动最小化工具窗口": False,
            "修复冻结帧(桌面DC抓帧)": False,   # 离线测试不触碰真实桌面DC
        }
        self.CALIBRATED = True
        self.SUBSTATS_BOX = substats_box  # 通常由用例设置
        # executor/capture method mock：供 run() 里的桌面DC抓帧切换使用
        self._capture_method = SimpleNamespace(
            hwnd_window=SimpleNamespace(x=958, y=33, width=1600, height=900,
                                        real_x_offset=0, real_y_offset=0,
                                        real_width=1600, real_height=900),
            do_get_frame=lambda: "original_frame",
        )
        self._executor = SimpleNamespace(method=self._capture_method)
        # 外部依赖 mock
        self._screen_texts = screen_texts or []
        self._clicks = []      # ("point", x, y) / ("box", x, y)
        self._keys = []
        self._warnings = []
        self._frame = None

    # ---- 覆盖外部调用 ----
    def ocr(self, x, y, w, h, match=None):
        res = []
        for name, bx, by, bw, bh in self._screen_texts:
            if match is not None:
                if isinstance(match, re.Pattern):
                    if not match.search(name):
                        continue
                elif match != name:
                    continue
            # 归一化区域相交判定
            if bx + bw < x or bx > x + w or by + bh < y or by > y + h:
                continue
            res.append(Box(bx, by, bw, bh, name))
        return res

    def wait_ocr(self, x, y, w, h, match=None, time_out=0):
        return self.ocr(x, y, w, h, match=match)

    def click(self, x, y, after_sleep=0):
        self._clicks.append(("point", round(x, 4), round(y, 4)))

    def click_box(self, box, after_sleep=0):
        self._clicks.append(("box", box.x, box.y))

    def send_key(self, k, after_sleep=0):
        self._keys.append(k)

    @property
    def frame(self):
        return self._frame

    @frame.setter
    def frame(self, v):
        self._frame = v

    def next_frame(self):
        # 离线测试无需真实截图：保持 frame=None，recorder 自动跳过存图
        self._frame = None

    def log_info(self, m, notify=False):
        pass

    def log_warning(self, m):
        self._warnings.append(m)

    def log_error(self, m, notify=False):
        self._last_error = m

    def info_set(self, k, v):
        pass


# 常见屏幕文本布局（归一化坐标，基于截图估算）
def bag_screen():
    return [("培养", 0.85, 0.92, 0.06, 0.05)]


def panel_screen(with_material=True):
    txt = [
        ("强化并调谐", 0.10, 0.90, 0.20, 0.05),
    ]
    if with_material:
        txt.append(("特级音箧筒", 0.55, 0.10, 0.08, 0.15))
    return txt


def success_screen():
    return [("调谐成功", 0.45, 0.40, 0.10, 0.05)]


def substat_screen():
    return [
        ("暴击 14.9%", 0.10, 0.30, 0.12, 0.03),
        ("攻击 102", 0.10, 0.34, 0.10, 0.03),
        ("暴击伤害 15.0%", 0.10, 0.38, 0.14, 0.03),
        ("普攻伤害加成 9.4%", 0.10, 0.42, 0.16, 0.03),
        ("生命 470", 0.10, 0.46, 0.10, 0.03),
    ]


SUBBOX = (0.05, 0.18, 0.45, 0.65)
TASK_FILE = OKWW / "src" / "task" / "UpgradeEchoTask.py"


# ================================================================ 用例
def test_regex():
    print("[正则]")
    check("TRAIN_RE 培养", bool(TRAIN_RE.search("培养")))
    check("TRAIN_RE 培養(繁)", bool(TRAIN_RE.search("培養")))
    check("CONFIRM_RE 强化并调谐", bool(CONFIRM_RE.search("强化并调谐")))
    check("CONFIRM_RE 强化", bool(CONFIRM_RE.search("强化")))
    check("CONFIRM_RE 不匹配强化消耗", not CONFIRM_RE.search("强化消耗材料"))
    check("MATERIAL_RE 特级音箧筒", bool(MATERIAL_RE.search("特级音箧筒")))
    check("SUCCESS_RE 调谐成功", bool(SUCCESS_RE.search("调谐成功")))
    check("SUBSTAT 暴击 14.9%", bool(SUBSTAT_KEYWORDS.search("暴击 14.9%")))
    check("SUBSTAT 攻击 102", bool(SUBSTAT_KEYWORDS.search("攻击 102")))
    check("SUBSTAT 暴击伤害 15.0%", bool(SUBSTAT_KEYWORDS.search("暴击伤害 15.0%")))
    check("SUBSTAT 普攻伤害加成 9.4%", bool(SUBSTAT_KEYWORDS.search("普攻伤害加成 9.4%")))
    check("SUBSTAT 生命 470", bool(SUBSTAT_KEYWORDS.search("生命 470")))
    check("SUBSTAT 不匹配 海之女", not SUBSTAT_KEYWORDS.search("海之女"))
    check("SUBSTAT 不匹配 强化消耗材料", not SUBSTAT_KEYWORDS.search("强化消耗材料"))


def test_probes():
    print("[页面探针]")
    t = MockTask(bag_screen())
    check("at_bag_echo_page 有培养=True", t.at_bag_echo_page() is True)
    t2 = MockTask([])
    check("at_bag_echo_page 无培养=False", t2.at_bag_echo_page() is False)
    t3 = MockTask(panel_screen())
    check("confirm_enhance_available 面板=True", t3.confirm_enhance_available() is True)
    t4 = MockTask(bag_screen())
    check("confirm_enhance_available 背包=False", t4.confirm_enhance_available() is False)


def test_select_first():
    print("[选首格]")
    t = MockTask(bag_screen())
    ok = t.select_first_echo()
    check("返回 True", ok is True)
    check("点击了固定点 FIRST_SLOT", ("point",) + t.FIRST_SLOT in t._clicks)


def test_enter_culture():
    print("[进入培养]")
    t = MockTask(panel_screen())
    check("已在面板直接返回 True", t.enter_culture() is True)
    t2 = MockTask(bag_screen())
    # 模拟：点到培养后进入面板（真实游戏会切页）
    def cb(box, after_sleep=0):
        t2._screen_texts = panel_screen()
        t2._clicks.append(("box", box.x, box.y))
    t2.click_box = cb
    ok = t2.enter_culture()
    check("点培养后返回 True", ok is True)
    check("点击了培养文字框", any(c[0] == "box" for c in t2._clicks))


ICON_HIT = (0.5581, 0.1122, 0.95, (858, 52, 70, 98))


def test_open_grid():
    print("[打开材料网格]")
    # 网格已展开：绝不能再点 +号（会把它切换关闭）——实机失败的根因
    t = MockTask([])
    t.find_material_icon = lambda frame=None: ICON_HIT
    t.material_grid_open = lambda frame=None: True
    ok = t.open_material_grid()
    check("已展开时直接返回 True", ok is True)
    check("已展开时不再点 +号", t._clicks == [])

    # 未展开：点一次 +号后展开
    seq = {"n": 0}

    def grid_side(frame=None):
        return seq["n"] > 0

    t2 = MockTask([])
    t2.find_material_icon = lambda frame=None: ICON_HIT if seq["n"] else None
    t2.material_grid_open = grid_side

    def click_side(x, y, after_sleep=0):
        seq["n"] += 1
        t2._clicks.append(("point", round(x, 4), round(y, 4)))
    t2.click = click_side
    t2.next_frame = lambda: None
    check("未展开时点一次即返回 True", t2.open_material_grid() is True)
    check("点了 ADD_SLOT_PLUS 固定点",
          ("point",) + tuple(round(v, 4) for v in t2.ADD_SLOT_PLUS) in t2._clicks)
    check("只点了一次 +号", len(t2._clicks) == 1)

    # 点 +号后仍未展开 -> False（触发 no_popup）
    t3 = MockTask([])
    t3.find_material_icon = lambda frame=None: None
    t3.material_grid_open = lambda frame=None: False
    t3.next_frame = lambda: None
    check("展开失败返回 False", t3.open_material_grid() is False)


def make_count_task(counts, target=8):
    """构造一个按序返回数量的 mock：counts 依次是每次 read_material_count 的返回值。"""
    t = MockTask([])
    t.config["材料数量"] = target
    t.next_frame = lambda: None
    seq = list(counts)
    final = seq[-1] if seq else target

    def read():
        return seq.pop(0) if seq else final
    t.read_material_count = read
    return t


def test_select_material():
    print("[选材料-数量闭环]")
    # 0→1→2→...→8：只点图标 +1，共 8 次，不做模板来回定位
    t = make_count_task(list(range(0, 9)))
    st = t.select_material()
    check("从0补到8返回 ok", st == "ok")
    check("点图标 8 次", len(t._clicks) == 8)
    check("点击位置固定=金色格子中心",
          all(c == ("point",) + tuple(round(v, 4) for v in t.MATERIAL_ICON_POINT)
              for c in t._clicks))
    check("未点减号", not any(
        c == ("point",) + tuple(round(v, 4) for v in t.MATERIAL_MINUS_POINT)
        for c in t._clicks))


def test_select_material_over_target():
    print("[选材料-多了点减号]")
    # 已经放了 10 个，目标 8：应连点 2 次「-」，一个图标都不点
    t = make_count_task([10, 9, 8])
    st = t.select_material()
    minus = ("point",) + tuple(round(v, 4) for v in t.MATERIAL_MINUS_POINT)
    icon = ("point",) + tuple(round(v, 4) for v in t.MATERIAL_ICON_POINT)
    check("返回 ok", st == "ok")
    check("点了 2 次减号", t._clicks.count(minus) == 2)
    check("没有点图标", icon not in t._clicks)


def test_select_material_idempotent():
    print("[选材料-重复运行幂等]")
    t = make_count_task([8])
    st = t.select_material()
    check("已是8返回 ok", st == "ok")
    check("一个点都不点(不会来回切换)", t._clicks == [])


def test_select_material_retry_ocr_miss():
    print("[选材料-OCR偶发漏读]")
    t = make_count_task([None, 3, 4, 5, 6, 7, 8])
    st = t.select_material()
    check("漏读一次后仍能补到8", st == "ok")
    check("点击次数=5(从3补到8)", len(t._clicks) == 5)


def test_read_material_count():
    print("[读数量]")
    t = MockTask([("8/776", 0.567, 0.199, 0.046, 0.032),
                  ("279", 0.669, 0.194, 0.030, 0.034)])
    check("识别 8/776 -> 8", t.read_material_count() == 8)
    t2 = MockTask([("279", 0.567, 0.199, 0.046, 0.032)])
    t2.MATERIAL_SLOT_COUNT_BOX = (0.11, 0.75, 0.20, 0.83)
    check("无斜杠时读不到(不误把279当数量)", t2.read_material_count() is None)
    t3 = MockTask([])
    t3._screen_texts = [("8", 0.13, 0.77, 0.02, 0.03)]
    t3.MATERIAL_COUNT_BOX = (0.55, 0.18, 0.64, 0.25)
    check("退回左下槽位数字 8", t3.read_material_count() == 8)


def test_select_material_no_material():
    print("[选材料-无材料]")
    t = make_count_task([None, None, None])
    st = t.select_material()
    check("连续读不到 -> no_material", st == "no_material")


def test_material_template_matching():
    """用真实帧验证图标模板：能命中、中心正确、且不会误命中网格关闭画面。"""
    print("[图标模板实图校验]")
    import cv2
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[3]
    tpl_path = root / "ok-ww" / "ok-wuthering-waves-master" / "assets" / "echo_material_gold.png"
    check("模板文件存在", tpl_path.exists())
    if not tpl_path.exists():
        return
    tpl = cv2.imread(str(tpl_path))
    check("模板可读且尺寸>0", tpl is not None and tpl.shape[0] > 0)

    t = MockTask([])
    t.MATERIAL_TEMPLATE = str(tpl_path)
    open_frame = cv2.imread(str(root / "logs" / "echo_upgrade" /
                                "20261110_003518" / "00_open_grid.png"))
    check("实机网格帧可读", open_frame is not None)
    if open_frame is not None:
        hit = t.find_material_icon(open_frame)
        check("网格帧命中经验物图标", hit is not None)
        if hit:
            check("命中中心≈(0.558,0.112)",
                  abs(hit[0] - 0.5581) < 0.02 and abs(hit[1] - 0.1122) < 0.02)
            check("匹配度>0.9", hit[2] > 0.9)
    # 网格关闭的画面（用户图6）不应命中
    closed = cv2.imread(str(_P(r"C:\Users\46025\.workbuddy\clipboard-images") /
                            "clipboard-2026-11-09T14-20-47-563Z-c0af1ece.jpg"))
    if closed is not None:
        check("网格关闭帧不误命中", t.find_material_icon(closed) is None)


def test_dismiss_detail():
    print("[收起详情]")
    t = MockTask(panel_screen())
    t.dismiss_detail()
    check("点击了 DISMISS_POPUP_POINT",
          ("point",) + t.DISMISS_POPUP_POINT in t._clicks)


def test_confirm_enhance():
    print("[强化并调谐]")
    t = MockTask(panel_screen())
    st = t.confirm_enhance()
    check("返回 ok", st == "ok")
    check("先收起详情(点固定点)",
          ("point",) + t.DISMISS_POPUP_POINT in t._clicks)
    check("再点强化并调谐(box)",
          any(c[0] == "box" for c in t._clicks))


def test_dismiss_success():
    print("[确认调谐成功]")
    t = MockTask(success_screen())
    t.dismiss_success()
    check("有浮层时点了 SUCCESS_DISMISS_POINT",
          ("point",) + t.SUCCESS_DISMISS_POINT in t._clicks)
    t2 = MockTask([])
    t2.dismiss_success()
    check("无浮层时不点继续",
          ("point",) + t2.SUCCESS_DISMISS_POINT not in t2._clicks)


def test_read_substats():
    print("[读副词条]")
    t = MockTask(substat_screen(), substats_box=SUBBOX)
    subs = t.read_substats()
    check("过滤出 5 条副词条", len(subs) == 5)
    check("含'暴击 14.9%'", any("暴击 14.9%" in s for s in subs))
    t2 = MockTask(substat_screen(), substats_box=None)
    check("未标定返回空", t2.read_substats() == [])


def test_run_steps_stop_on_failure():
    print("[失败即停]")
    # 背包+面板（无特级材料）：走到打开网格时因无特级材料失败，应停在当场并返回 no_popup
    t = MockTask(bag_screen() + panel_screen(with_material=False))
    t.SUBSTATS_BOX = SUBBOX
    status = t._run_steps([
        ("select_first", "点选首格声骸", "_step_select_first"),
        ("enter_culture", "进入培养面板", "_step_enter_culture"),
        ("open_grid", "点+号打开材料网格", "_step_open_grid"),
        ("select_material", "选N个特级材料", "_step_select_material"),
    ], {"deadline": None, "substats": None}, debug=False)
    check("打开网格失败返回 no_popup", status == "no_popup")


def test_unknown_screen_diagnosis():
    """screen=未知 且失败时，日志应给出遮挡/页面定位的诊断提示。"""
    print("[未知界面诊断]")
    t = MockTask([])          # 空屏幕：探针全失效，_screen() 返回「未知」
    t.SUBSTATS_BOX = SUBBOX
    t._run_steps([("select_first", "点选首格声骸", "_step_select_first")],
                 {"deadline": None, "substats": None}, debug=False)
    hit = [w for w in t._warnings if "未识别到任何游戏界面" in w]
    check("失败且未知界面时给出诊断", len(hit) == 1)
    check("诊断含背包页指引", bool(hit) and "背包" in hit[0])
    check("诊断含窗口遮挡指引", bool(hit) and "遮挡" in hit[0])


def test_safety_gate():
    print("[安全闸]")
    t = MockTask(bag_screen(), substats_box=None)
    t.CALIBRATED = True
    t.run()
    check("未标定 SUBSTATS_BOX -> 拒绝执行(记录错误)",
          getattr(t, "_last_error", "").find("未标定") >= 0)
    t2 = MockTask(bag_screen(), substats_box=SUBBOX)
    t2.CALIBRATED = False
    t2.run()
    check("CALIBRATED=False -> 拒绝执行",
          getattr(t2, "_last_error", "").find("未标定") >= 0)


def test_debug_entries():
    """锁住两件事：下拉必须含「仅声骸选择」；调试入口不得强制单步暂停。"""
    print("[调试入口]")
    check("下拉含「仅声骸选择」", "仅声骸选择" in DEBUG_ENTRIES)
    check("下拉含「单步暂停调试」", "单步暂停调试" in DEBUG_ENTRIES)
    check("下拉无重复项", len(DEBUG_ENTRIES) == len(set(DEBUG_ENTRIES)))

    # 每个下拉项都必须在 run() 里有对应分发分支，避免「指引里有、下拉里没有」
    src = TASK_FILE.read_text(encoding="utf-8")
    for e in DEBUG_ENTRIES:
        if e in ("完整流程", "单步暂停调试"):
            continue
        check(f"run() 有分发分支：{e}", f'entry == "{e}"' in src)

    def dispatch(entry):
        t = MockTask(bag_screen(), substats_box=SUBBOX)
        t.config["调试入口"] = entry
        t.config["最大处理数量"] = 1
        t.config["截图日志目录"] = "/tmp/echo_upgrade_dispatch_test"
        calls = []

        def spy(steps, ctx, debug):
            calls.append(([s[0] for s in steps], debug))
            return "ok"

        t._run_steps = spy
        t._finish = lambda: None
        t.run()
        return calls

    c = dispatch("仅声骸选择")
    check("仅声骸选择 -> 只跑 select_first", c and c[0][0] == ["select_first"])
    check("仅声骸选择 -> 不强制暂停", c and c[0][1] is False)

    for entry, expect in [
        ("仅培养(从列表)", ["select_first", "enter_culture"]),
        ("仅材料弹窗(已在面板)", ["open_grid", "select_material"]),
        ("仅升级(已在面板)",
         ["confirm", "dismiss_success", "read_substats", "back_to_list"]),
    ]:
        cc = dispatch(entry)
        check(f"{entry} -> 步骤正确", cc and cc[0][0] == expect)
        check(f"{entry} -> 不强制暂停", cc and cc[0][1] is False)

    c = dispatch("单步暂停调试")
    check("单步暂停调试 -> debug=True", c and c[0][1] is True)


def test_tool_window_guard():
    """运行期自动最小化工具窗口，结束必须恢复（含异常路径），且可用开关关闭。"""
    print("[工具窗口遮挡处理]")
    calls = []
    orig = T.set_tool_window_minimized
    T.set_tool_window_minimized = lambda flag: calls.append(flag) or True
    try:
        def run_with(auto_hide, boom=False):
            calls.clear()
            t = MockTask(bag_screen(), substats_box=SUBBOX)
            t.config["调试入口"] = "仅声骸选择"
            t.config["自动最小化工具窗口"] = auto_hide
            t.config["截图日志目录"] = "/tmp/echo_upgrade_guard_test"
            t._finish = lambda: None
            def spy(steps, ctx, debug):
                if boom:
                    raise RuntimeError("模拟步骤异常")
                return "ok"
            t._run_steps = spy
            try:
                t.run()
            except RuntimeError:
                pass
            return list(calls)

        check("开启时先最小化(True)", run_with(True)[0] is True)
        check("开启时结束恢复(False)", run_with(True) == [True, False])
        check("关闭时不触碰窗口", run_with(False) == [])
        check("异常时仍恢复窗口", run_with(True, boom=True) == [True, False])
    finally:
        T.set_tool_window_minimized = orig

    # 检查真实实现不会因无窗口/异常而崩（返回 bool）
    r = orig(False)
    check("真实实现返回 bool 且不抛异常", isinstance(r, bool))


def test_desktop_dc_capture():
    """抓帧源替换：坐标正确、restore 精确还原、异常兜底、开关生效。"""
    print("[桌面DC抓帧]")
    calls = []

    def fake_capture(ctx, w, h, x, y):
        calls.append((w, h, x, y))
        return "desktop_frame"

    cleaned = []

    def fake_cleanup(ctx):
        cleaned.append(True)

    # 原方法在类上（不在实例 __dict__），替换后应被删除以还原类方法
    class FakeMethod:
        def do_get_frame(self):
            return "window_dc_frame"

    m = FakeMethod()
    m.hwnd_window = SimpleNamespace(x=958, y=33, width=1600, height=900,
                                    real_x_offset=0, real_y_offset=0,
                                    real_width=1600, real_height=900)
    check("初始为窗口DC帧", m.do_get_frame() == "window_dc_frame")

    restore = install_desktop_dc_capture(m, capture_fn=fake_capture,
                                         cleanup_fn=fake_cleanup, ctx=object())
    check("返回 restore 回调", callable(restore))
    check("替换后取桌面DC帧", m.do_get_frame() == "desktop_frame")
    check("桌面DC坐标=客户区屏幕坐标", calls == [(1600, 900, 958, 33)])

    restore()
    check("restore 后还原为窗口DC帧", m.do_get_frame() == "window_dc_frame")
    check("restore 清理了 GDI 资源", len(cleaned) == 1)
    restore()
    check("restore 幂等（不重复清理）", len(cleaned) == 1)

    # 异常兜底：抓帧函数抛错时返回 None，不炸任务
    def boom(ctx, w, h, x, y):
        raise RuntimeError("抓屏失败")

    m2 = FakeMethod()
    m2.hwnd_window = m.hwnd_window
    r2 = install_desktop_dc_capture(m2, capture_fn=boom,
                                    cleanup_fn=fake_cleanup, ctx=object())
    check("抓帧异常时返回 None", m2.do_get_frame() is None)
    r2()

    # 无 hwnd_window / method=None 时安全返回 None
    check("method=None 安全返回 None", install_desktop_dc_capture(None) is None)
    check("无 hwnd_window 安全返回 None",
          install_desktop_dc_capture(SimpleNamespace()) is None)

    # 开关：开启时应调用替换（用 monkeypatch 记录），关闭时不调用
    seen = []
    orig = T.install_desktop_dc_capture
    T.install_desktop_dc_capture = lambda method: (seen.append(1) or None)
    try:
        t = MockTask(bag_screen(), substats_box=SUBBOX)
        t.config["调试入口"] = "仅声骸选择"
        t.config["修复冻结帧(桌面DC抓帧)"] = False
        t._run_steps = lambda steps, ctx, debug: "ok"
        t._finish = lambda: None
        t.run()
        check("开关关闭 -> 不切换抓帧", seen == [])

        t2 = MockTask(bag_screen(), substats_box=SUBBOX)
        t2.config["调试入口"] = "仅声骸选择"
        t2.config["修复冻结帧(桌面DC抓帧)"] = True
        t2._run_steps = lambda steps, ctx, debug: "ok"
        t2._finish = lambda: None
        t2.run()
        check("开关开启 -> 调用抓帧切换", seen == [1])
    finally:
        T.install_desktop_dc_capture = orig


def test_wgc_gate():
    """WGC 门禁放行：引用替换、幂等、可还原；WGC 实例不被桌面 DC 覆盖。"""
    print("[WGC 门禁放行]")
    from src import wgc_enable as W
    from ok.device.capture_methods import update as U
    from ok.util import window as WIN

    check("阈值取 WGC_MIN_BUILD(19041)", W.wgc_min_build() == 19041)
    W.uninstall_wgc_patch()
    orig_update = U.windows_graphics_available
    orig_window = WIN.windows_graphics_available
    check("初始未安装", not W.is_wgc_patch_installed())
    check("替换框架内 4 处门禁引用", W.install_wgc_patch() == 4)
    check("update 模块已替换", U.windows_graphics_available is W.wgc_available)
    check("window 原模块已替换", WIN.windows_graphics_available is W.wgc_available)
    check("重复安装幂等", W.install_wgc_patch() == 0)

    saved_build = WIN.WINDOWS_BUILD_NUMBER
    WIN.WINDOWS_BUILD_NUMBER = 18000
    check("build 低于 19041 -> 不可用", W.wgc_available() is False)
    WIN.WINDOWS_BUILD_NUMBER = saved_build

    W.uninstall_wgc_patch()
    check("还原 update 引用", U.windows_graphics_available is orig_update)
    check("还原 window 引用", WIN.windows_graphics_available is orig_window)
    check("还原标记清除", not W.is_wgc_patch_installed())

    class WindowsGraphicsCaptureMethodStub:
        def __init__(self):
            self.hwnd_window = SimpleNamespace(x=0, y=0, width=100, height=100)

        def do_get_frame(self):
            return "wgc_frame"

    fake = WindowsGraphicsCaptureMethodStub()
    check("识别 WGC 抓帧方式", T._is_wgc_capture_method(fake))
    check("WGC 实例跳过桌面DC覆盖", T.install_desktop_dc_capture(fake) is None)
    check("WGC 的 do_get_frame 保持原样", fake.do_get_frame() == "wgc_frame")


def test_global_desktop_patch():
    """全局抓帧补丁：坐标、幂等、还原、崩溃兜底、不复用GDI对象。"""
    print("[全局桌面DC补丁]")
    hw = SimpleNamespace(x=958, y=33, width=1600, height=900,
                         real_x_offset=0, real_y_offset=0,
                         real_width=1600, real_height=900)
    check("屏幕矩形坐标正确", resolve_capture_rect(hw) == (958, 33, 1600, 900))
    hw2 = SimpleNamespace(x=100, y=50, width=1280, height=720,
                          real_x_offset=8, real_y_offset=31,
                          real_width=0, real_height=0)
    check("含偏移且无real尺寸时回退width",
          resolve_capture_rect(hw2) == (108, 81, 1280, 720))

    class FakeBitBlt:
        def do_get_frame(self):
            return "original_window_dc"

        def __init__(self):
            self.hwnd_window = hw

    FakeBitBlt.do_get_frame = FakeBitBlt.do_get_frame
    check("初始未打补丁", not is_patched(FakeBitBlt))
    check("安装返回 True", install_global_patch(FakeBitBlt) is True)
    check("安装后 is_patched", is_patched(FakeBitBlt))
    check("重复安装幂等(False)", install_global_patch(FakeBitBlt) is False)

    # 注入替身，避免离线测试真去抓屏幕
    seen = []
    orig_capture = DDC.capture_desktop
    DDC.capture_desktop = lambda x, y, w, h: (seen.append((x, y, w, h)) or "live_frame")
    try:
        inst = FakeBitBlt()
        check("全局补丁后取实时帧", inst.do_get_frame() == "live_frame")
        check("传给抓帧的坐标正确", seen == [(958, 33, 1600, 900)])

        # 桌面DC失败时必须兜底回原实现，不能让程序失去抓帧能力
        DDC.capture_desktop = lambda x, y, w, h: None
        check("桌面DC失败时兜底原实现",
              inst.do_get_frame() == "original_window_dc")
    finally:
        DDC.capture_desktop = orig_capture

    check("卸载返回 True", uninstall_global_patch(FakeBitBlt) is True)
    check("卸载后 is_patched=False", not is_patched(FakeBitBlt))
    check("卸载后恢复原方法", FakeBitBlt().do_get_frame() == "original_window_dc")
    check("重复卸载幂等(False)", uninstall_global_patch(FakeBitBlt) is False)

    # 真实实现：非法尺寸直接返回 None，不触碰 GDI
    check("宽=0 直接返回 None", capture_desktop(0, 0, 0, 900) is None)
    check("高为负直接返回 None", capture_desktop(0, 0, 100, -1) is None)


def test_full_flow_mock():
    print("[完整流程 mock]")
    screen = (bag_screen() + panel_screen(with_material=True)
              + success_screen() + substat_screen())
    t = MockTask(screen, substats_box=SUBBOX)
    t.config["材料数量"] = 3  # 缩短
    # 材料网格：离线注入数量序列 0→1→2→3（目标 3）
    t.material_grid_open = lambda frame=None: True
    t.next_frame = lambda: None
    seq = [0, 1, 2, 3]

    def read_count():
        return seq.pop(0) if seq else 3
    t.read_material_count = read_count
    status = t._process_one(deadline=None, debug=False)
    check("完整流程返回 ok/selected", status in ("ok", "selected"))
    icon = ("point",) + tuple(round(v, 4) for v in t.MATERIAL_ICON_POINT)
    check("材料补到3个(图标中心点3次)", t._clicks.count(icon) == 3)
    check("收起详情点被调用",
          ("point",) + t.DISMISS_POPUP_POINT in t._clicks)
    check("强化并调谐 box 被调用",
          any(c[0] == "box" for c in t._clicks))
    check("副词条已汇总", len(t.all_substats) == 1 and len(t.all_substats[0]) == 5)


# ================================================================ 运行
if __name__ == "__main__":
    test_regex()
    test_probes()
    test_select_first()
    test_enter_culture()
    test_open_grid()
    test_select_material()
    test_select_material_over_target()
    test_select_material_idempotent()
    test_select_material_retry_ocr_miss()
    test_read_material_count()
    test_select_material_no_material()
    test_material_template_matching()
    test_dismiss_detail()
    test_confirm_enhance()
    test_dismiss_success()
    test_read_substats()
    test_run_steps_stop_on_failure()
    test_unknown_screen_diagnosis()
    test_safety_gate()
    test_debug_entries()
    test_tool_window_guard()
    test_desktop_dc_capture()
    test_wgc_gate()
    test_global_desktop_patch()
    test_full_flow_mock()
    print(f"\n==== 结果：PASS={_PASS}  FAIL={_FAIL} ====")
    sys.exit(1 if _FAIL else 0)
