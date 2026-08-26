# AGENTS.md

抖音多账号火花自动维护工具 —— Windows 纯桌面应用（PyInstaller + pywebview + Inno Setup，无 Docker）。所有源码在 `DouYinSparkFlow/`，用户文档见 `README.md`，架构与二次开发指南见 `docs/DEVELOPMENT.md`（先读它）。

## 常用命令（均在 `DouYinSparkFlow/` 目录下运行）

```powershell
# 首次：venv + 依赖 + Playwright Chromium（约 170MB，仅一次）
python -m venv .venv; .\.venv\Scripts\activate
pip install -r requirements.txt -r requirements-web.txt -r requirements-build.txt
python -m playwright install chromium

# 开发运行：桌面窗口（launcher.py 拉起 8787 + 18090 双服务 + pywebview 窗口）
python launcher.py
# 或浏览器模式（不弹窗口，访问 http://127.0.0.1:8787）
python main.py --web

# 测试：必须从 DouYinSparkFlow/ 目录运行（测试用 `from core import ...`，依赖 cwd）
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 打包安装程序（在仓库根目录；产物 dist\DouYinSparkFlow-Setup-*.exe 与 dist\app\）
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 [-Version 1.0.1] [-AppOnly]
```

无 lint/typecheck/CI 配置；提交前跑 unittest 即可。本机需装 Node.js（协议发送 `core/protocol_sender.mjs` 需要；打包版自动内置 node.exe）。

## 架构要点

- 入口 `launcher.py` 启动两个 FastAPI 服务：Web 控制台 `127.0.0.1:8787`（pywebview 窗口加载）与登录桌面服务 `127.0.0.1:18090`（扫码登录弹真实 Chromium）。`main.py` 是传统 CLI 入口。
- 定时调度是应用内实现的（`webui/ops.py` 写 `dailySendWindow` 到 config.json），**Windows 无 cron**，不要引入 crontab。
- 扫码登录必须用真实 Chromium，WebView2 无法替代（抖音限制）。
- 发送核心：`core/tasks.py`（调度）→ `core/send_state.py`（强确认/重试状态机）；协议发送走 Node（`core/protocol_dispatch.py` + `core/protocol_sender.mjs`）。

## 路径约定（不要硬编码）

所有运行时路径必须经 `utils/config.py` 的 `data_dir()` / `_runtime_root()`，浏览器路径经 `core/browser.py` 的 `browser_profile_root()`：

- 开发版（LOCAL）：数据在 `DouYinSparkFlow/state/`；`config.json`/`usersData.json`/`webui_settings.json` 在 `DouYinSparkFlow/` 根（已被 gitignore，本地文件不是仓库内容）
- 打包版（PACKED，`sys.frozen`）：安装目录只读，一切写入必须落到 `%APPDATA%\DouYinSparkFlow\`（可用 `SPARKFLOW_DATA_DIR` 覆盖）
- GITHUBACTION 环境：userData 从环境变量 `USER_DATA` 读取（缺失即抛错）

## 测试怪癖

- `tests/test_network_fallback.py` 与 `test_config_contract.py` 是**源码契约测试**：直接读 `.py` 文本断言字符串（如 `"direct", "mihomo"`）。重命名/改写 `core/browser.py`、`core/friends.py`、`core/tasks.py` 的对应逻辑会破坏测试，需同步更新断言。
- `test_config_contract.py` 断言 `config.example.json` 与 `DEFAULT_CONFIG` 完全一致：改 `utils/config.py` 的默认配置时必须同步更新 example。
- `test_webui_safety.py` 中 crontab 用例在 Windows 自动跳过，属预期。

## 构建注意事项

- `packaging/windows/build.ps1` **必须保持纯 ASCII**：无 BOM 的 .ps1 被 Windows PowerShell 5.1 按 ANSI 读取，中文注释会破坏脚本（文件头有说明）。编辑时不要加中文。
- 构建产物：`dist\DouYinSparkFlow-Setup-<版本>.exe`（安装包，约 220MB）与 `dist\app\`（绿色版，约 680MB，含内置 Chromium + node.exe）；`build\` 为构建中间目录。体积大属正常，勿"优化"掉浏览器/Node。
- 依赖清单 `requirements.txt` 与 `requirements-web.txt` 存在重复包，属现状，安装时一起传 `-r` 即可。
