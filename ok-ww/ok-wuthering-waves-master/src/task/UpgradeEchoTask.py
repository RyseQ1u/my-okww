# -*- coding: utf-8 -*-
"""
声骸升级辅助（UpgradeEchoTask）—— 固定步骤 + 检测即点击 + 单步调试 + 截图日志
============================================================================

真实流程（你已在游戏里把待升级件过滤排序好，脚本从背包声骸列表页开始）：
  1. 选中列表首格声骸（点首格）
  2. 进入培养面板（点「培养」）
  3. 点「+号」槽位，打开右侧强化材料网格
  4. 在网格里点「特级」材料 N 次（每次 +1，会弹出材料详情；网格在右侧不受影响）
  5. 点空白处收起材料详情弹窗，露出「强化并调谐」
  6. 点「强化并调谐」确认升级，等待调谐成功
  7. 确认调谐成功浮层（点掉继续），读取结果界面左侧副词条
  8. 返回背包列表，循环下一件

不做：自动登录 / 自动开背包 / 自动读等级反复投料。
     —— 你负责先把待升级件过滤排序好并停在背包声骸列表页，脚本从列表直接循环。

检测即点击：按钮（培养 / 强化并调谐 / 特级材料名）通过 OCR 检测文字框→点框中心，
            去掉精确坐标依赖。仅以下无文字/稳定锚点用固定归一化点：
            FIRST_SLOT（列表首格）、ADD_SLOT_PLUS（+号槽位）、
            DISMISS_POPUP_POINT（收起详情的空白点）、SUCCESS_DISMISS_POINT（继续点）。

⚠️ 前置标定：SUBSTATS_BOX 必须先用本会话截图估算并填入（详情结果界面左侧副词条区域，
    归一化四元组）；CALIBRATED 改为 True 后 run() 才执行，避免误选/误读。
    其余固定点若实机有偏差，看 logs/echo_upgrade/ 带框截图微调即可。
"""
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from src.task.BaseWWTask import BaseWWTask

try:
    import cv2
    _HAS_CV2 = True
except Exception:  # pragma: no cover - cv2 由 ok 依赖保证存在
    _HAS_CV2 = False

# ---------------------------------------------------------------- 文案正则
# 框架 find_boxes_by_name 的语义：str 精确相等，re.Pattern 子串搜索。统一用正则兼容简繁。
TRAIN_RE = re.compile(r"培[養养]")                          # 培养（详情页进入培养）
CONFIRM_RE = re.compile(r"^[強强]化(?:并[調调][諧谐])?$")   # 强化 / 强化并调谐（确认升级；两端锚定避免误中「强化消耗材料」）
MATERIAL_RE = re.compile(r"特级")                           # 仅用于点击后弹出详情框的校验，网格里无文字
# 已选数量形如「8/776」：带斜杠的只有第一个(金色特级)格子，其它格子是纯数字 279/92/6
MATERIAL_COUNT_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
SUCCESS_RE = re.compile(r"調[諧谐]成功|调[諧谐]成功")         # 调谐成功浮层
SHORTAGE_RE = re.compile(
    r"(?:[材貝贝幣币調调諧谐器][^\s]{0,8}(?:不足|不夠|不够))|(?:不足|不夠|不够)")

# 副词条常见关键词，用于从结果界面 OCR 文本里筛出副词条行（按需增删）。
# 匹配形如「暴击 14.9%」「攻击 102」「暴击伤害 15.0%」「普攻伤害加成 9.4%」「生命 470」。
SUBSTAT_KEYWORDS = re.compile(
    r"(攻击|生命|防御|共鸣效率|暴击|爆击|暴击伤害|伤害加成|充能|化解|抗性|"
    r"普攻|技[能術]伤害|治疗|经验|调谐)\s*[:：]?\s*[\+\-]?\s*\d[\d.,]*\s*[%％]?"
)

# 终止步骤状态：命中即停止本件序列（留在当前界面供调试）
STOP_STATUSES = {"fail", "no_material", "no_popup", "timeout", "exception",
                 "skip", "quit"}

# 调试入口下拉选项（GUI 配置面板读取此列表）。
# 提为模块级常量，便于离线测试直接断言，防止「指引里提到、下拉里却没有」的漏配。
DEBUG_ENTRIES = [
    "完整流程",
    "仅声骸选择",              # 只点首格：验证截图是否干净 / FIRST_SLOT 是否命中
    "仅培养(从列表)",          # 选首格 + 进入培养面板
    "仅材料弹窗(已在面板)",     # 开材料网格 + 选 N 个特级
    "仅升级(已在面板)",         # 强化并调谐 + 确认成功 + 读副词条 + 返回
    "单步暂停调试",            # 完整流程且每步暂停
]


# ============================================================== 抓帧方式判定 / 工具窗口遮挡处理
# WGC 放行后走窗口独立表面，遮挡不会污染帧；只有回落到桌面 DC（屏幕合成图）
# 时才需要让工具窗口在任务运行期间避开游戏区域。
def _is_wgc_capture_method(method):
    """判断当前抓帧方式是否为 WGC；桌面 DC 补丁绝不能覆盖 WGC 的 do_get_frame。"""
    if method is None:
        return False
    try:
        from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod
        if isinstance(method, WindowsGraphicsCaptureMethod):
            return True
    except Exception:
        pass
    try:
        return any("WindowsGraphicsCapture" in cls.__name__
                   for cls in type(method).__mro__)
    except Exception:
        return False


def _enum_okww_windows():
    """枚举标题含 OK-WW 的可见顶层窗口句柄。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    hits = []

    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if "OK-WW" in buf.value:
                hits.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return hits


def set_tool_window_minimized(minimized: bool) -> bool:
    """最小化(True)/恢复(False) OK-WW 工具窗口；任何异常都只返回 False，绝不阻断任务。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        code = 6 if minimized else 9          # SW_MINIMIZE / SW_RESTORE
        hwnds = _enum_okww_windows()
        for hwnd in hwnds:
            user32.ShowWindow(hwnd, code)
        return bool(hwnds)
    except Exception:
        return False


# ============================================================== 抓帧源修复（冻结帧）
def install_desktop_dc_capture(method, capture_fn=None, cleanup_fn=None, ctx=None):
    """把 BitBlt 的抓帧源从「游戏窗口 DC」临时换成「桌面 DC」，任务结束可 restore。

    实测（2026-11-09，Win10 19045 + 鸣潮 Unreal 窗口）：
      GetWindowDC(游戏窗口)+BitBlt 返回 DWM 缓存的**冻结旧帧**——多次运行截图 md5
      完全相同，与实时画面平均像素差 101.9；OCR 因此永远识别不到「培养」。
      GetDC(0)+BitBlt（桌面 DC）抓屏幕合成结果，与实时画面像素差仅 1.59。
    不改框架源码，只在 capture method 实例上替换 do_get_frame；restore() 精确还原。
    WGC 走窗口独立表面，用它替换会退化成「遮挡即污染」，故直接跳过。
    """
    if _is_wgc_capture_method(method):
        return None
    hw = getattr(method, "hwnd_window", None) if method is not None else None
    if hw is None or not hasattr(method, "do_get_frame"):
        return None
    try:
        if capture_fn is None:
            from ok.device.capture_methods import bitblt_utils as _bu
            capture_fn = _bu.capture_desktop_by_bitblt
            cleanup_fn = cleanup_fn or _bu.clean_up_desktop_bitblt
            ctx = ctx or _bu.BitBltCtxDummy()
    except Exception:
        return None

    had_own = "do_get_frame" in method.__dict__
    original_own = method.__dict__.get("do_get_frame")

    def desktop_do_get_frame():
        try:
            x = hw.x + getattr(hw, "real_x_offset", 0)
            y = hw.y + getattr(hw, "real_y_offset", 0)
            w = getattr(hw, "real_width", 0) or hw.width
            h = getattr(hw, "real_height", 0) or hw.height
            if w <= 0 or h <= 0:
                return None
            return capture_fn(ctx, w, h, x, y)
        except Exception:
            return None

    method.do_get_frame = desktop_do_get_frame
    state = {"restored": False}

    def restore():
        if state["restored"]:
            return
        state["restored"] = True
        try:
            if had_own:
                method.do_get_frame = original_own
            elif "do_get_frame" in method.__dict__:
                delattr(method, "do_get_frame")
        except Exception:
            pass
        try:
            if cleanup_fn:
                cleanup_fn(ctx)
        except Exception:
            pass

    return restore


# ============================================================== 步骤执行日志
class EchoStepRecorder:
    """把每一步的截图（带框标注 + 原始帧）与结构化记录落盘到一次运行的专属目录。

    日志与截图只存本地（logs/ 已被 .gitignore 忽略），按运行永久保留，
    供日后回看并重新标定估算坐标。单次运行目录内容：
      NN_key_SS.png      带检测框标注的截图
      NN_key_SS_raw.png  同一步的未标注原始帧（可重新跑 OCR 标定）
      steps.jsonl        每步结构化记录（追加写，不覆盖）
      report.json        汇总
      meta.json          本次运行使用的坐标常量与开关快照
    """

    def __init__(self, base_dir: Path):
        self.base = Path(base_dir)
        # 精确到毫秒，避免同一秒内多次运行互相覆盖
        self.run_dir = self.base / datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.steps: list[dict] = []
        self.lock = threading.Lock()
        self._shot_seq = 0

    def write_meta(self, meta: dict):
        """合并写入本次运行的坐标/开关快照，供日后对照截图优化。"""
        path = self.run_dir / "meta.json"
        current = {}
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                current = {}
        current.update(meta)
        current["updated"] = datetime.now().isoformat(timespec="milliseconds")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)

    def capture(self, frame, index, key, status, screen, detections, action,
                substats=None):
        """保存一步的带框截图、原始帧与记录，返回记录字典。"""
        path = raw_path = None
        if frame is not None:
            path, raw_path = self._save_shot(index, key, frame, detections)
        rec = {
            "step": index,
            "key": key,
            "status": status,
            "screen": screen,
            "time": datetime.now().isoformat(timespec="milliseconds"),
            "detections": detections,
            "action": action,
            "substats": substats,
            "screenshot": str(path) if path else None,
            "screenshot_raw": str(raw_path) if raw_path else None,
        }
        with self.lock:
            self.steps.append(rec)
            with open(self.run_dir / "steps.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def _save_shot(self, index, key, frame, detections):
        # 递增序号：重试或循环多件时同一步不会互相覆盖截图
        if not _HAS_CV2 or frame is None:
            return None, None
        with self.lock:
            self._shot_seq += 1
            seq = self._shot_seq
        stem = f"{index:02d}_{key}_{seq:02d}"
        target = self.run_dir / f"{stem}.png"
        raw_target = self.run_dir / f"{stem}_raw.png"
        cv2.imwrite(str(raw_target), frame)
        img = frame.copy()
        for det in (detections or {}).values():
            if isinstance(det, dict) and "x" in det:
                x, y, w, h = det["x"], det["y"], det["w"], det["h"]
                cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
                label = det.get("label", "")
                if label:
                    cv2.putText(img, label, (x, max(0, y - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        cv2.imwrite(str(target), img)
        return target, raw_target

    def finalize(self, summary: dict) -> Path:
        with open(self.run_dir / "report.json", "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "steps": self.steps},
                      f, ensure_ascii=False, indent=2)
        return self.run_dir


# ============================================================== 主任务
class UpgradeEchoTask(BaseWWTask):

    # ================= 标定开关（必须先标定） =================
    # ⚠️ 以下为按截图目测的估算值（非精确标定），实机若读不到副词条/点偏，按 logs/echo_upgrade/ 截图微调。
    CALIBRATED = True
    SUBSTATS_BOX = (0.05, 0.18, 0.45, 0.65)    # 结果界面左侧副词条区域（归一化四元组），估算值

    # ================= 固定点击点（无文字/稳定锚点，可微调） =================
    # 列表首格（图标类，无稳定文字标签，保留归一化点击）。来自背包截图：首卡约 x0.09 y0.12。
    FIRST_SLOT = (0.135, 0.20)
    # 「+号」槽位：强化面板左下「强化消耗材料」区域第一个加号。来自截图 ≈ (0.14, 0.75)。
    ADD_SLOT_PLUS = (0.14, 0.75)
    # 收起材料详情弹窗的空白点（详情弹窗遮挡强化并调谐，点其外空白收起）。来自截图顶栏空白 ≈ (0.30, 0.07)。
    DISMISS_POPUP_POINT = (0.30, 0.07)
    # 「调谐成功」浮层继续点（点掉浮层进入结果详情）。实机若自动消失可不点。
    SUCCESS_DISMISS_POINT = (0.50, 0.90)

    # ============ 材料图标模板（网格里只有图标+数量，没有品质文字） ============
    # 用户实机截图确认：右侧材料网格不显示「特级」等品质名，只有 4 个彩色经验物
    # 图标与数量（金/紫/蓝/绿）。点击材料后才在详情框里显示「特级密音筒」。
    # 故改用图标模板匹配定位，而非 OCR 找文字。模板从实时帧裁剪，避开会变化的
    # 数量数字与选中边框。命中中心实测 ≈ (0.558, 0.112)。
    MATERIAL_TEMPLATE = "assets/echo_material_gold.png"
    MATERIAL_MATCH_THRESHOLD = 0.72
    MATERIAL_SEARCH_BOX = (0.50, 0.03, 0.99, 0.95)   # 右侧材料网格搜索区
    GRID_OPEN_LAPLACIAN_MIN = 1200                   # 网格开≈4030，关≈193

    # ---- 数量闭环（用户实机图 OCR 实测，1600x900）----
    # 右上第一个格子「8/776」文字区域，置信度 1.00
    MATERIAL_COUNT_BOX = (0.55, 0.18, 0.64, 0.25)
    # 左下已选槽位数量「8」区域（备用判据）
    MATERIAL_SLOT_COUNT_BOX = (0.11, 0.75, 0.20, 0.83)
    # 第一个金色经验物格子中心（固定点，不再反复模板定位，避免来回切换选中态）
    MATERIAL_ICON_POINT = (0.5581, 0.1122)
    # 该格子右上角「-」减号按钮（选中数量>0 时出现）
    MATERIAL_MINUS_POINT = (0.6040, 0.0745)
    # 左下槽位「+」加号按钮（也可用于补数量）
    MATERIAL_PLUS_POINT = (0.1910, 0.7410)

    # ================= 按钮搜索区域（只需覆盖按钮大致位置） =================
    # 检测即点击：OCR 在区域内找到按钮文字后点其框中心，不需要精确点击坐标。
    TRAIN_BTN_BOX = (0.72, 0.84, 1.00, 0.99)     # 培养（背包右下）
    MATERIAL_GRID_BOX = (0.50, 0.04, 0.99, 0.94)  # 右侧材料网格（OCR 限定特级材料）
    CONFIRM_BTN_BOX = (0.05, 0.84, 0.42, 0.99)    # 强化并调谐（面板底部左）

    MAX_RETRY = 3               # 连续失败上限
    MAX_CONSECUTIVE_SKIP = 5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "⬆️ 声骸升级辅助(固定步骤)"
        self.description = (
            "用户过滤排序好待升级件后，走固定步骤：选→培养→点+号开网格→"
            "点N个特级→强化并调谐→确认副词条，循环处理多件并统计副词条。"
        )
        self.supported_languages = ["zh_CN", "zh_TW"]
        self.default_config.update({
            "材料品质": "特级",        # 仅作日志标注；实机靠图标模板定位，不靠文字
            "材料数量": 8,            # 每件点几次经验物（每次 +1）
            "最大处理数量": 200,
            "单件超时(秒)": 120,
            "调试入口": "完整流程",
            "每步截图": True,
            "截图日志目录": "logs/echo_upgrade",
            "自动最小化工具窗口": True,
            "修复冻结帧(桌面DC抓帧)": True,
        })
        # 材料品质是短字符串：OK 框架无 "text" 类型，用 line_edit。
        self.config_type["材料品质"] = {"type": "line_edit"}
        # 材料数量(int) 不声明 type，由框架按默认值自动渲染为 SpinBox。
        self.config_type["调试入口"] = {
            "type": "drop_down",
            "options": list(DEBUG_ENTRIES),
        }
        # 每步截图(bool) 不声明 type，由框架按默认值自动渲染为开关(Switch)。

        self.config_description = {
            "材料品质": "仅用于日志标注；材料靠 assets/echo_material_gold.png 图标模板定位（网格无品质文字）",
            "材料数量": "每件点几次经验物图标，每点一次放入 1 个（实机 8 次 = 8/784）",
            "最大处理数量": "本次最多处理多少件，防止误触后长时间运行",
            "单件超时(秒)": "单件处理超过该秒数即放弃并继续下一件",
            "调试入口": ("完整流程=循环多件；仅声骸选择=只点首格(查截图是否干净/坐标是否准)；"
                        "仅培养/仅材料弹窗/仅升级=单独跑对应段落、失败停在当场；"
                        "单步暂停调试=完整流程且每步暂停"),
            "每步截图": "每步保存带检测框标注图 + 未标注原始帧，便于复盘与日后重新 OCR 标定",
            "截图日志目录": ("每次运行一个独立目录（毫秒时间戳，不覆盖历史）；含截图、原始帧、"
                            "steps.jsonl、report.json 与坐标快照 meta.json。本地永久保留，不进版本控制"),
            "自动最小化工具窗口": ("仅在抓帧源回落为桌面 DC 时生效，避免工具窗口被拍进屏幕合成图；"
                                  "WGC 抓的是窗口独立表面，不受遮挡影响，会自动跳过本项"),
            "修复冻结帧(桌面DC抓帧)": ("仅 WGC 不可用时生效：Win10 游戏窗口 DC 会返回 DWM 缓存旧帧，"
                                     "导致 OCR 永远失败；开启后改用桌面 DC 抓实时画面，结束自动还原"),
        }
        self.done_count = 0
        self.fail_count = 0
        self.skip_count = 0
        self.consecutive_fail = 0
        self.consecutive_skip = 0
        self.stop_reason = ""
        self.all_substats = []    # 跨件副词条汇总
        self.recorder = None
        self._finished = False

    # ===================== 通用工具 =====================

    @staticmethod
    def _expired(deadline):
        return deadline is not None and time.time() > deadline

    @staticmethod
    def _box_record(box, label=None):
        if box is None:
            return None
        return {"x": int(box.x), "y": int(box.y), "w": int(box.width),
                "h": int(box.height), "label": label or (box.name or "")}

    def _detect(self, region, regex):
        """在区域内 OCR 检测首个匹配文字框，返回 Box 或 None。"""
        boxes = self.ocr(*region, match=regex)
        return boxes[0] if boxes else None

    def _click_text(self, region, regex, after_sleep=0.5, fallback_point=None):
        """检测文字框并点其框中心；未命中且有兜底点时退化为固定点击。返回命中 Box。"""
        box = self._detect(region, regex)
        if box is not None:
            self.click_box(box, after_sleep=after_sleep)
            return box
        if fallback_point:
            self.click(*fallback_point, after_sleep=after_sleep)
        return None

    # ===================== 材料图标模板匹配 =====================

    def _material_template(self):
        """延迟加载模板（相对路径基于项目根，与截图日志目录同口径）。"""
        cached = getattr(self, "_material_tpl_cache", None)
        if cached is not None:
            return cached
        if not _HAS_CV2:
            return None
        path = Path(self.MATERIAL_TEMPLATE)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        tpl = cv2.imread(str(path)) if path.exists() else None
        self._material_tpl_cache = tpl
        return tpl

    def find_material_icon(self, frame=None):
        """在右侧网格里找金色经验物图标，返回 (归一化中心x, 归一化中心y, 匹配度, 像素框)，失败 None。"""
        if not _HAS_CV2:
            return None
        tpl = self._material_template()
        img = frame if frame is not None else self.frame
        if tpl is None or img is None:
            return None
        h, w = img.shape[:2]
        if h < 10 or w < 10:
            return None
        x1, y1, x2, y2 = self.MATERIAL_SEARCH_BOX
        rx1, ry1 = int(x1 * w), int(y1 * h)
        roi = img[ry1:int(y2 * h), rx1:int(x2 * w)]
        th, tw = tpl.shape[:2]
        if roi.shape[0] < th or roi.shape[1] < tw:
            return None
        res = cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score < self.MATERIAL_MATCH_THRESHOLD:
            return None
        px, py = rx1 + loc[0], ry1 + loc[1]
        cx, cy = (px + tw / 2) / w, (py + th / 2) / h
        return (round(cx, 4), round(cy, 4), round(float(score), 4),
                (px, py, tw, th))

    def material_grid_open(self, frame=None):
        """判断材料网格是否已展开。

        优先用图标模板匹配（最贴合实机）；模板不可用时退回右侧区域的
        Laplacian 方差（网格开≈4030，关≈193）。
        """
        hit = self.find_material_icon(frame)
        if hit is not None:
            return True
        if not _HAS_CV2:
            return False
        img = frame if frame is not None else self.frame
        if img is None:
            return False
        h, w = img.shape[:2]
        x1, y1, x2, y2 = self.MATERIAL_SEARCH_BOX
        roi = img[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)]
        if roi.size == 0:
            return False
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
        return cv2.Laplacian(gray, cv2.CV_64F).var() >= self.GRID_OPEN_LAPLACIAN_MIN

    def read_material_count(self):
        """读当前已选经验物数量。

        实机 OCR：右上第一个格子显示「8/776」(conf 1.00)，其它格子是纯数字
        279/92/6，所以带斜杠的比值能唯一定位金色特级材料。
        备选：左下已选槽位下方的纯数字「8」。
        读不到返回 None。
        """
        for box in self.ocr(*self.MATERIAL_COUNT_BOX):
            m = MATERIAL_COUNT_RE.search(box.name or "")
            if m:
                return int(m.group(1))
        for box in self.ocr(*self.MATERIAL_SLOT_COUNT_BOX):
            s = (box.name or "").strip()
            if s.isdigit() and 0 <= int(s) <= 999:
                return int(s)
        return None

    # ===================== 页面锚点与导航 =====================

    def at_bag_echo_page(self):
        """「培养」按钮出现 = 在背包声骸列表/详情页。"""
        return bool(self.wait_ocr(*self.TRAIN_BTN_BOX, match=TRAIN_RE, time_out=0.6))

    def confirm_enhance_available(self):
        """强化面板探针：出现「强化并调谐」即为面板。"""
        return bool(self._detect(self.CONFIRM_BTN_BOX, CONFIRM_RE))

    # ===================== 步骤方法 =====================

    def select_first_echo(self, det=None):
        # 首格为图标类 UI，无稳定文字标签，保留归一化点击；这是唯一的非文字点击点
        self.click(self.FIRST_SLOT[0], self.FIRST_SLOT[1], after_sleep=0.6)
        ok = self.at_bag_echo_page()
        if det is not None:
            det["select_first"] = self._box_record(None, "首格(固定点击)")
        return ok

    def enter_culture(self, deadline=None, det=None):
        """列表/详情页 -> 培养面板：检测「培养」并点其框中心。"""
        for _ in range(5):
            if self._expired(deadline):
                return False
            if self.confirm_enhance_available():
                return True
            box = self._click_text(self.TRAIN_BTN_BOX, TRAIN_RE, after_sleep=0.6)
            if det is not None:
                det["train_btn"] = self._box_record(box, "培养")
            if self.confirm_enhance_available():
                return True
        return False

    def open_material_grid(self, deadline=None, det=None):
        """面板 -> 材料网格：确保右侧网格处于展开状态。

        关键：网格常常**已经展开**（用户可能手动点过，或上一件残留）。此时绝不能
        再盲点 +号——那会把它切换关闭。先判断，只有确实没展开时才点一次。
        """
        if self._expired(deadline):
            return False
        if self.material_grid_open():
            if det is not None:
                hit = self.find_material_icon()
                if hit:
                    px, py, tw, th = hit[3]
                    det["material_icon"] = {"x": px, "y": py, "w": tw, "h": th,
                                            "label": "经验物图标(网格已展开)"}
            return True

        self.click(*self.ADD_SLOT_PLUS, after_sleep=0.6)
        if det is not None:
            det["add_slot"] = self._box_record(None, "+号槽位(固定点击)")
        self.next_frame()
        if self.material_grid_open():
            return True
        self.log_warning("点+号后材料网格仍未展开")
        return False

    def select_material(self, deadline=None, det=None):
        """把已选经验物数量精确调到目标值（默认 8）。

        用户明确要求：不要来回重复点图标凑数，而是**读实际数量 → 用 +/- 补差**。
        实机里数量有两处可读：右上格子「8/776」、左下已选槽位「8」；
        多了点右上格子「-」，少了点图标(或左下「+」)。

        每轮都重新读数量，所以重复运行幂等：已经 8 个就一个点都不点。
        """
        prefix = self.config.get("材料品质", "特级")
        target = int(self.config.get("材料数量"))
        if target < 0:
            return "no_material"
        last = None
        miss = 0
        max_rounds = target + 12
        for _ in range(max_rounds):
            if self._expired(deadline):
                return "timeout"
            cur = self.read_material_count()
            if cur is None:
                # OCR 偶发漏读：刷新帧重试几轮，别一次读不到就整个放弃
                miss += 1
                if miss >= 3:
                    self.log_warning("连续 3 轮未读到已选材料数量（检查 MATERIAL_COUNT_BOX 标定）")
                    return "no_material"
                self.next_frame()
                continue
            miss = 0
            if cur == target:
                if det is not None and last is not None and last != cur:
                    det.setdefault("material", []).append(
                        {"x": 0, "y": 0, "w": 0, "h": 0,
                         "label": f"数量已达标 {cur}/{target}"})
                return "ok"
            last = cur
            if cur > target:
                point, label = self.MATERIAL_MINUS_POINT, f"{cur}→{cur-1}(减)"
            else:
                point, label = self.MATERIAL_ICON_POINT, f"{cur}→{cur+1}(加)"
            self.click(*point, after_sleep=0.35)
            if det is not None:
                det.setdefault("material", []).append(
                    {"x": 0, "y": 0, "w": 0, "h": 0,
                     "label": f"{prefix} {label} 目标{target}"})
            self.next_frame()
        self.log_warning(f"数量闭环未在 {max_rounds} 轮内达到 {target}，最后读到 {last}")
        return "fail"

    def dismiss_detail(self, det=None):
        """材料详情弹窗遮挡强化并调谐，点空白处收起。固定点，可微调。"""
        self.click(*self.DISMISS_POPUP_POINT, after_sleep=0.4)
        if det is not None:
            det["dismiss"] = self._box_record(None, "收起详情(固定点击)")

    def confirm_enhance(self, deadline=None, det=None):
        """收起材料详情弹窗 -> 点「强化并调谐」确认升级，等待调谐动画。"""
        self.dismiss_detail(det)
        box = self._click_text(self.CONFIRM_BTN_BOX, CONFIRM_RE, after_sleep=2.5)
        if det is not None:
            det["confirm"] = self._box_record(box, "强化并调谐")
        return "ok" if box else "fail"

    def dismiss_success(self, deadline=None, det=None):
        """调谐成功浮层：等待动画后点击继续点进入结果详情。浮层可能自动消失。"""
        time.sleep(1.5)
        for _ in range(3):
            if self._expired(deadline):
                break
            if self._detect((0.0, 0.0, 1.0, 1.0), SUCCESS_RE):
                self.click(*self.SUCCESS_DISMISS_POINT, after_sleep=1.0)
                if det is not None:
                    det["success"] = self._box_record(None, "调谐成功(已继续)")
            else:
                break
        return "ok"

    def read_substats(self, det=None):
        """OCR 结果界面左侧副词条区域，筛选副属性行。需先标定 SUBSTATS_BOX。"""
        if not self.SUBSTATS_BOX:
            if det is not None:
                det["substats"] = []
            return []
        boxes = self.ocr(*self.SUBSTATS_BOX)
        lines = [b.name.strip() for b in boxes if b.name]
        substats = [ln for ln in lines if SUBSTAT_KEYWORDS.search(ln)]
        if det is not None:
            det["substats"] = substats
        return substats

    def back_to_bag(self, deadline=None):
        """任意层 -> 背包声骸列表页。"""
        start = time.time()
        while time.time() - start < 12:
            if self._expired(deadline):
                return False
            if self.at_bag_echo_page():
                return True
            self.send_key("esc", after_sleep=0.4)
        return False

    # ===================== 步骤化单件处理 =====================

    def _screen(self):
        """尽力判断当前界面，用于日志标注。"""
        if self.confirm_enhance_available():
            return "强化面板"
        if self.at_bag_echo_page():
            return "背包声骸页"
        return "未知"

    def _pause(self, idx, key, desc):
        """单步暂停：等待用户指令。无 stdin（GUI 模式）时直接继续。"""
        msg = (f"\n[单步调试] 步骤 {idx:02d} {key} —— {desc}\n"
               f"  回车=继续   s=跳过此步   q=退出\n> ")
        self.log_info(msg)
        try:
            ans = input(msg).strip().lower()
        except Exception:
            return "continue"
        if ans == "q":
            return "quit"
        if ans == "s":
            return "skip"
        return "continue"

    def _step_select_first(self, ctx, det):
        return "selected" if self.select_first_echo(det) else "fail"

    def _step_enter_culture(self, ctx, det):
        return "ok" if self.enter_culture(ctx["deadline"], det) else "fail"

    def _step_open_grid(self, ctx, det):
        return "ok" if self.open_material_grid(ctx["deadline"], det) else "no_popup"

    def _step_select_material(self, ctx, det):
        return self.select_material(ctx["deadline"], det)

    def _step_confirm(self, ctx, det):
        return self.confirm_enhance(ctx["deadline"], det)

    def _step_dismiss_success(self, ctx, det):
        return self.dismiss_success(ctx["deadline"], det)

    def _step_read_substats(self, ctx, det):
        subs = self.read_substats(det)
        ctx["substats"] = subs
        self.log_info(f"副词条：{subs}")
        return "ok"

    def _step_back_to_list(self, ctx, det):
        return "ok" if self.back_to_bag(ctx["deadline"]) else "fail"

    # 完整单件步骤表（固定 8 步）
    FULL_STEPS = [
        ("select_first", "点选首格声骸", "_step_select_first"),
        ("enter_culture", "进入培养面板", "_step_enter_culture"),
        ("open_grid", "点+号打开材料网格", "_step_open_grid"),
        ("select_material", "选N个特级材料", "_step_select_material"),
        ("confirm", "点强化并调谐", "_step_confirm"),
        ("dismiss_success", "确认调谐成功", "_step_dismiss_success"),
        ("read_substats", "确认副词条", "_step_read_substats"),
        ("back_to_list", "返回列表", "_step_back_to_list"),
    ]

    def _run_steps(self, steps, ctx, debug):
        """按步骤表执行，遇 STOP_STATUSES 即停（留在当前界面），返回最后状态。"""
        last = "ok"
        for i, (key, desc, method_name) in enumerate(steps):
            if debug:
                ans = self._pause(i, key, desc)
                if ans == "quit":
                    self.log_info("用户中断单步调试")
                    return "quit"
                if ans == "skip":
                    self.log_info(f"跳过步骤 {key}")
                    last = "skip"
                    continue
            det = {}
            screen = self._screen()
            try:
                status = getattr(self, method_name)(ctx, det)
            except Exception as e:  # 步骤内异常也记为失败，停在当场
                self.log_error(f"步骤 {key} 异常：{e}")
                status = "exception"
            self.next_frame()  # 取一帧最新画面用于截图
            rec = self.recorder.capture(self.frame, i, key, status, screen, det,
                                       desc, substats=ctx.get("substats"))
            self.log_info(f"步骤 {key}: {status}  (截图 {Path(rec['screenshot']).name if rec['screenshot'] else '-'})")
            if status in STOP_STATUSES and screen == "未知":
                self.log_warning(
                    "当前帧未识别到任何游戏界面（「培养」「强化并调谐」均未检测到）。"
                    "请确认：① 游戏停在「背包→声骸」列表页；"
                    "② 抓帧源正常（WGC / 桌面DC），画面不是黑屏或冻结旧帧；"
                    "③ 若抓帧源仍是桌面DC，检查是否有窗口遮挡游戏区域。"
                    f"可直接打开本次截图 {Path(rec['screenshot']).name if rec['screenshot'] else '-'} 核对帧内容。")
            if status in STOP_STATUSES:
                return status
            last = status
        return last

    @staticmethod
    def _aggregate(status):
        """把步骤状态映射为外层循环的计数类别。"""
        if status in ("selected", "ok", "reached", "done"):
            return "done"
        if status == "skip":
            return "skip"
        if status == "no_material":
            return "no_material"
        return "fail"  # fail / timeout / no_popup / exception / quit

    def _process_one(self, deadline, debug):
        ctx = {"deadline": deadline, "substats": None}
        status = self._run_steps(self.FULL_STEPS, ctx, debug)
        if ctx.get("substats"):
            self.all_substats.append(ctx["substats"])
        return status

    # ===================== 调试入口分发 =====================

    def _debug_select_only(self, debug):
        """只点首格声骸：验证截图是否被工具窗口污染、FIRST_SLOT 是否命中。

        不进入培养、不投料、不循环，跑完停在当场。这是实机排错的第一站。
        """
        ctx = {"deadline": None, "substats": None}
        self._run_steps([("select_first", "点选首格声骸", "_step_select_first")],
                        ctx, debug)

    def _debug_from_list(self, debug):
        ctx = {"deadline": None, "substats": None}
        self._run_steps(self.FULL_STEPS, ctx, debug)

    def _debug_popup_only(self, debug):
        ctx = {"deadline": None, "substats": None}
        # 假设游戏正停在强化面板：直接开网格→选材料
        self._run_steps([("open_grid", "点+号打开材料网格", "_step_open_grid"),
                         ("select_material", "选N个特级材料", "_step_select_material")],
                        ctx, debug)

    def _debug_upgrade_only(self, debug):
        ctx = {"deadline": None, "substats": None}
        # 假设游戏正停在已选好材料、待强化的面板：直接强化→确认→返回
        self._run_steps([("confirm", "点强化并调谐", "_step_confirm"),
                         ("dismiss_success", "确认调谐成功", "_step_dismiss_success"),
                         ("read_substats", "确认副词条", "_step_read_substats"),
                         ("back_to_list", "返回列表", "_step_back_to_list")], ctx, debug)

    # ===================== 主流程 =====================

    def run(self):
        if (not self.CALIBRATED or not self.SUBSTATS_BOX):
            self.log_error(
                "未标定副词条区域：先用本会话截图估算并填入 SUBSTATS_BOX"
                "（结果界面左侧副词条区域，归一化四元组），再把 CALIBRATED 设为 True。"
                "已拒绝执行以避免误选材料。", notify=True)
            return

        # 初始化步骤日志
        raw = self.config.get("截图日志目录", "logs/echo_upgrade")
        base = Path(raw) if Path(raw).is_absolute() else \
            Path(__file__).resolve().parents[4] / raw
        self.recorder = EchoStepRecorder(base)
        self.log_info(f"步骤日志目录：{self.recorder.run_dir}")
        # 把本次运行实际使用的坐标常量写入 meta.json：日后回看截图时
        # 能确切知道当时用的是哪一组估算值，便于逐步标定优化。
        box_consts = {}
        for cls in type(self).__mro__:
            for attr, value in vars(cls).items():
                if attr.endswith("_BOX") and isinstance(value, tuple):
                    box_consts.setdefault(attr, value)
        self.recorder.write_meta({
            "calibrated": self.CALIBRATED,
            "coordinate_note": ("坐标为按实机截图目测的估算值，尚未精确标定；"
                                "回看 *_raw.png 原始帧可重新 OCR 标定"),
            "substats_box": self.SUBSTATS_BOX,
            "search_boxes": box_consts,
            "fixed_points": {
                "FIRST_SLOT": self.FIRST_SLOT,
                "ADD_SLOT_PLUS": self.ADD_SLOT_PLUS,
                "DISMISS_POPUP_POINT": self.DISMISS_POPUP_POINT,
                "SUCCESS_DISMISS_POINT": self.SUCCESS_DISMISS_POINT,
            },
            "material_template": self.MATERIAL_TEMPLATE,
            "material_threshold": self.MATERIAL_MATCH_THRESHOLD,
        })

        entry = self.config.get("调试入口", "完整流程")
        debug = entry == "单步暂停调试"

        # 先判定实际抓帧方式：WGC 走窗口独立表面，既不需要桌面 DC 兜底，
        # 也不需要最小化工具窗口（遮挡不会污染帧）。
        method = None
        try:
            method = self.executor.method
        except Exception:
            pass
        if method is None:
            try:
                method = self.executor.device_manager.capture_method
            except Exception:
                pass
        is_wgc = _is_wgc_capture_method(method)

        auto_hide = (not is_wgc) and bool(self.config.get("自动最小化工具窗口", True))
        hidden = set_tool_window_minimized(True) if auto_hide else False
        if auto_hide:
            self.log_info(f"工具窗口遮挡处理：{'已最小化' if hidden else '未找到窗口(跳过)'}")
        elif is_wgc:
            self.log_info("工具窗口遮挡处理：WGC 抓帧不受遮挡影响，无需最小化")

        # 抓帧源：WGC 优先；只有回落到 BitBlt 时才用桌面 DC
        # （main.py 启动期全局补丁，或单跑本任务文件时的运行期临时兜底）。
        restore_capture = None
        if is_wgc:
            self.log_info("抓帧源：WGC(窗口独立表面，遮挡不影响)")
        else:
            try:
                from src.desktop_dc_capture import is_patched
                global_patched = is_patched()
            except Exception:
                global_patched = False
            if global_patched:
                self.log_info("抓帧源：桌面DC(全局实时)")
            elif self.config.get("修复冻结帧(桌面DC抓帧)", True):
                try:
                    restore_capture = install_desktop_dc_capture(method)
                except Exception as e:
                    self.log_warning(f"切换桌面DC抓帧失败，沿用原方式：{e}")
                self.log_info("抓帧源：桌面DC(运行期兜底)" if restore_capture
                              else "抓帧源：原方式(切换未生效，可能出现冻结帧)")

        try:
            # 调试入口：假设用户已手动导航到对应界面。
            # 只有「单步暂停调试」才逐步暂停，其余入口直接跑完对应段落。
            if entry == "仅声骸选择":
                self._debug_select_only(debug)
                self._finish()
                return
            if entry == "仅培养(从列表)":
                ctx = {"deadline": None, "substats": None}
                self._run_steps([("select_first", "点选首格声骸", "_step_select_first"),
                                 ("enter_culture", "进入培养面板", "_step_enter_culture")],
                                ctx, debug)
                self._finish()
                return
            if entry == "仅材料弹窗(已在面板)":
                self._debug_popup_only(debug)
                self._finish()
                return
            if entry == "仅升级(已在面板)":
                self._debug_upgrade_only(debug)
                self._finish()
                return
            self._run_full_flow(debug)
        finally:
            if restore_capture:
                restore_capture()
            if auto_hide:
                set_tool_window_minimized(False)

    def _run_full_flow(self, debug):
        """完整流程：假设已在背包声骸列表页（用户已过滤排序），直接循环。"""
        limit = int(self.config.get("最大处理数量"))
        timeout = int(self.config.get("单件超时(秒)"))
        self.info_set("已完成", 0)
        self.info_set("已失败", 0)
        self.info_set("已处理", 0)

        processed = 0
        while processed < limit:
            deadline = time.time() + timeout
            status = self._process_one(deadline, debug)
            if status == "quit":
                self.stop_reason = "用户中断"
                break
            processed += 1
            self.info_set("已处理", processed)

            if time.time() > deadline:
                self.log_warning("单件处理超时")

            cat = self._aggregate(status)
            if cat == "done":
                self.done_count += 1
                self.consecutive_fail = 0
                self.consecutive_skip = 0
                self.info_set("已完成", self.done_count)
            elif cat == "fail":
                self.fail_count += 1
                self.consecutive_fail += 1
                self.consecutive_skip = 0
                self.info_set("已失败", self.fail_count)
            elif cat == "skip":
                self.skip_count += 1
                self.consecutive_skip += 1
                self.consecutive_fail = 0
            else:  # no_material
                self.stop_reason = "材料或列表耗尽"
                break

            if self.consecutive_fail >= self.MAX_RETRY:
                self.stop_reason = "连续失败过多"
                break
            if self.consecutive_skip >= self.MAX_CONSECUTIVE_SKIP:
                self.stop_reason = "连续跳过过多，疑似探针失效"
                break

        self._finish()

    def _finish(self):
        if getattr(self, "_finished", False):
            return
        self._finished = True
        reason = self.stop_reason or "达到处理上限"
        self.info_set("结束原因", reason)
        summary = {
            "reason": reason,
            "done": self.done_count,
            "fail": self.fail_count,
            "skip": self.skip_count,
            "substats": self.all_substats,
        }
        if self.recorder is not None:
            run_dir = self.recorder.finalize(summary)
            self.log_info(f"执行日志已保存：{run_dir}", notify=not self.stop_reason)
        self.log_info(
            f"声骸升级结束（{reason}）：完成 {self.done_count}，"
            f"失败 {self.fail_count}，跳过 {self.skip_count}", notify=True)

    def summary(self):
        # 兼容框架约定；实际汇报逻辑已并入 _finish()
        self._finish()


if __name__ == "__main__":
    from config import config
    from ok import run_task

    run_task(config, task=UpgradeEchoTask, debug=True)
