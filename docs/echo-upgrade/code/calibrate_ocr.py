# -*- coding: utf-8 -*-
"""
只读 OCR 标定辅助 —— 不点击、不投料、不修改任何游戏状态。

用途：确定 UpgradeEchoTask 的 SUBSTATS_BOX（详情页副词条区域）。
其余按钮坐标均用「检测即点击」（OCR 文字定位），无需标定。

运行（在任意目录，用 Python 3.11）：
    D:\\Dev\\Python311\\python.exe docs\\echo-upgrade\\code\\calibrate_ocr.py

步骤：
    1. 先启动游戏，强化一件声骸后停在「调谐成功 / 强化结果」界面（左侧能看到副词条列表）。
    2. 运行本脚本，它会每 2 秒 dump 一帧全屏 OCR，共 60 秒。
    3. 期间保持该结果界面不动，让脚本抓到副词条文本。
    4. 从日志里找副词条文本（副属性行，如「暴击 14.9%」「生命 470」），
       记下其所在区域的 x/y/w/h（已是归一化 0~1）。
    5. 把该区域填入 UpgradeEchoTask 的 SUBSTATS_BOX 常量，再把 CALIBRATED 改为 True。

提示：也可直接用游戏截图让我来目测标定 SUBSTATS_BOX，省去本脚本。

日志同时写到 OK-WW 的 logs/ 目录，可用文本编辑器搜索 "OCR_DUMP"。
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "ok-ww" / "ok-wuthering-waves-master"
if not ROOT.is_dir():
    raise SystemExit(f"找不到 OK-WW 源码目录：{ROOT}")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from config import config  # noqa: E402
from ok import run_task  # noqa: E402
from src.task.BaseWWTask import BaseWWTask  # noqa: E402

DURATION_SECONDS = 60
INTERVAL_SECONDS = 2


class OcrDumpTask(BaseWWTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "OCR_DUMP（只读标定）"
        self.description = "只 dump 全屏 OCR 及归一化坐标，不做任何点击"

    def run(self):
        self.log_info("OCR_DUMP 开始：60 秒只读采样，不会点击任何按钮", notify=True)
        deadline = time.time() + DURATION_SECONDS
        frame_no = 0
        while time.time() < deadline:
            frame_no += 1
            width, height = self.width, self.height
            if not width or not height:
                self.log_warning("OCR_DUMP 画面尺寸未就绪，稍后重试")
                self.sleep(INTERVAL_SECONDS)
                continue
            self.log_info(f"OCR_DUMP ===== frame {frame_no} =====")
            for box in self.ocr(0, 0, 1, 1):
                self.log_info(
                    "OCR_DUMP {!r} x={:.3f} y={:.3f} w={:.3f} h={:.3f} conf={:.2f}".format(
                        box.name,
                        box.x / width, box.y / height,
                        box.width / width, box.height / height,
                        box.confidence,
                    )
                )
            self.sleep(INTERVAL_SECONDS)
        self.log_info("OCR_DUMP 结束", notify=True)


if __name__ == "__main__":
    run_task(config, task=OcrDumpTask, debug=True)
