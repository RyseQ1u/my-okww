# fzgj · 声骸升级辅助

从 `ok-oldking/ok-wuthering-waves`（OK-WW，AGPL-3.0）复制出来、作为独立项目定制的「声骸升级（Echo）」功能。**直接在 `main` 上开发**。

## 分支

| 分支 | 内容 | 状态 |
|---|---|---|
| `main` | 唯一工作分支：代码 + 开发文档 + 运行必需配置 | 活跃 |

历史隔离分支 `echo-only` 已删除。

## 目录

```
docs/
├── 交接文档.md                     项目进度与环境踩坑
├── 项目分析-2026-11-07.md          REA 实测 + 坐标核对报告
├── 升级声骸功能-实现分析.md        核心逻辑 / 流程 / 数据处理
└── echo-upgrade/
    ├── code/test_offline.py        离线自测（不启动游戏、不点击）
    └── code/calibrate_ocr.py       只读 OCR 标定脚本

ok-ww/ok-wuthering-waves-master/   可运行程序（精简版）
    ├── src/task/UpgradeEchoTask.py    声骸升级主任务
    ├── src/wgc_enable.py              WGC 版本门禁放行
    └── src/desktop_dc_capture.py      BitBlt 回落时的桌面 DC 兜底
```

`assets/`（模板图、OCR 模型）、`i18n/`、`icons/` 等运行资源**只存本地、不纳入版本控制**，见 `.gitignore`。

## 构建与自测

```powershell
cd ok-ww\ok-wuthering-waves-master
D:\Dev\Python311\python.exe -m compileall -q src config.py main.py main_debug.py
cd ..\..
D:\Dev\Python311\python.exe docs\echo-upgrade\code\test_offline.py
```

`config.py` 的 `onetime_tasks` 只注册 `["src.task.UpgradeEchoTask", "UpgradeEchoTask"]`，`trigger_tasks` 为空。
离线自测当前 **132 项，全部通过**（PASS=132 FAIL=0，含 WGC 门禁放行的 13 项用例）。

## 当前状态

- `src/task/UpgradeEchoTask.py`：固定流程 + **检测即点击**（按钮走 OCR 文字框中心，仅 4 个无文字锚点用固定归一化坐标）+ 单步调试入口 + 每步截图与结构化日志。
- **抓帧 WGC 已放行**：`main.py` / `main_debug.py` 启动期调用 `src/wgc_enable.py`，把框架误用 `WGC_NO_BORDER_MIN_BUILD(20348)` 的门禁改为 `WGC_MIN_BUILD(19041)`，并覆盖框架内 4 处静态引用。WGC 抓的是窗口独立表面，**被遮挡时仍能取到实时帧**。Win10 上的黄色提示框不进入采集帧。
- **兜底**：`src/desktop_dc_capture.py` 仍会把 `BitBlt` 的抓帧源由「游戏窗口 DC」改为「桌面 DC」，解决 Win10 窗口 DC 取到 DWM 冻结旧帧导致 OCR 恒失败；仅在 WGC 不可用时生效，已加类型判断，**绝不覆盖 WGC 的 `do_get_frame`**。WGC 生效时也不会再最小化工具窗口。
- `CALIBRATED = True`，但 `SUBSTATS_BOX` 与 4 个固定点击点均为**按截图目测的估算值，尚未实机标定**。运行前需按日志截图核对，否则可能点偏或误投料。
- 交互方式为 `PostMessage`：向窗口消息队列投递 `WM_*` 鼠标消息，不移动真实鼠标、不要求窗口在前台。

## 日志与截图留存

每次运行生成独立目录 `logs/echo_upgrade/YYYYmmdd_HHMMSS_mmm/`（毫秒精度，不覆盖历史）：

| 文件 | 用途 |
|---|---|
| `NN_key_SS.png` | 带检测框标注的步骤截图 |
| `NN_key_SS_raw.png` | 同一步的**未标注原始帧**，可日后重新跑 OCR 标定 |
| `steps.jsonl` | 每步状态、检测结果、动作（追加写，不覆盖） |
| `report.json` | 本次汇总 |
| `meta.json` | **本次运行实际使用的坐标常量与开关快照**，便于对照截图优化估算值 |

`logs/` 已被 `.gitignore` 排除：日志只存本地、永久保留，不进入版本控制。

## 已知待办

1. **实机标定**：`SUBSTATS_BOX` 与 4 个固定点仍是目测估算值，需用 `*_raw.png` 原始帧逐帧核对，并用「仅升级(已在面板)」逐段验收。
2. **WGC 在真实游戏窗口尚未验证**：本机探针（自建测试窗口 + 不透明遮挡）六项判定全部 PASS，但游戏运行时的 Unreal 窗口、双 GPU 跨适配器、以及是否设置 `WDA_EXCLUDEFROMCAPTURE` 仍需实机确认。
3. 仓库**历史提交**中仍含 37.9 MB 的 `echo.onnx`（当前索引已移除），`.git` 体积较大，首次推送会比较慢。

## 风险

第三方自动化工具违反《鸣潮》公平运营声明（明确禁止宏脚本、自动挂机），且游戏接入了内核级反作弊 ACE，存在账号冻结或永久封禁风险。**本项目仅作本地技术学习与离线自测使用，不要用于正式游戏账号。**
