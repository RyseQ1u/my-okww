# ok-ww · echo-only 分支

本目录是 `echo-only` 分支上的**最小可运行子集**：只保留「声骸升级辅助（UpgradeEchoTask）」功能及其运行必需的框架代码，其余 OK-WW 功能已全部移除。

上游项目：<https://github.com/ok-oldking/ok-wuthering-waves>（本分支仅作个人学习用途）。

## 保留内容

| 路径 | 用途 |
|---|---|
| `config.py` | 只注册 `UpgradeEchoTask`；`trigger_tasks` 为空；无自定义 Tab |
| `main.py` / `main_debug.py` | GUI 启动入口 |
| `src/task/UpgradeEchoTask.py` | 升级声骸任务（唯一业务任务） |
| `src/task/BaseWWTask.py` | 任务基类，提供 OCR / 点击 / 热键 / 月卡弹窗等能力 |
| `src/task/process_feature.py` | 模板特征预处理器（被 `config.py` 引用） |
| `src/scene/WWScene.py` | 场景基类，`config.py` 的 `scene` 项需要 |
| `src/Labels.py`、`src/globals.py`、`src/game_launcher.py`、`src/OnnxYolo8Detect.py`、`src/OpenVinoYolo8Detect.py` | 基类与 `my_app` 的运行期依赖 |
| `assets/templates/`、`assets/echo_model/`、`icons/`、`i18n/` | OCR / 模板匹配 / GUI 资源 |
| `requirements.txt`、`pyproject.toml`、`LICENSE.txt` | 依赖与许可 |

> 注：`config.py` 中仍保留 `Character Config` 这个全局配置项 —— `BaseWWTask.__init__`
> 会调用 `get_global_config('Character Config')`，缺少它会抛 `RuntimeError`。
> 它是基类运行期依赖，不代表本分支包含角色战斗功能。

## 已移除

其余 28 个业务任务与触发任务、`src/char/`、`src/combat/`、`src/gui/`、`src/web/`、`src/task/account/`、`tests/`、上游文档与 CI 元数据、`main_web*.py`、`ok_templates/`（840 MB 上游原始截图，**没有任何代码引用**，真正的模板在 `assets/templates/`）。

## 运行

```powershell
cd ok-ww\ok-wuthering-waves-master
D:\Dev\Python311\python.exe main_debug.py
```

依赖：`ok-script==2.0.20`、`onnxocr-ppocrv5`、`openvino`、`opencv-python`、`PySide6-Fluent-Widgets` 等；本机代理对 pypi.org 直连受限，装包请加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

游戏内前置：中文（简/繁）界面、背包 → 声骸页预筛并按**等级升序**排序、开启「阶段放入」、关闭「同步调谐」。

## ⚠️ 标定安全闸

`UpgradeEchoTask.CALIBRATED` 当前为 `False`，`DETAIL_LEVEL_BOX` / `PREVIEW_LEVEL_BOX` 为 `None`。

任务在 GUI 中可见、程序可正常启动，但 `run()` 会**直接拒绝执行**，不会点击任何按钮 —— 这是为了防止坐标未定时误投料、浪费游戏内材料。

标定方法见项目根目录 `docs/echo-upgrade/code/calibrate_ocr.py`：只读 dump 全屏 OCR 与归一化坐标，把两个等级区域填入类常量（两者必须不同），再把 `CALIBRATED` 改为 `True`。

## 分支说明

- `main`：初始化时的全量工作区快照（790 个文件），**未被本次改造修改**。
- `echo-only`：本分支。切回主干：`git checkout main`。

官方用户协议禁止第三方工具，存在封号风险；仅作个人学习，控制批量、不长时间挂机。
