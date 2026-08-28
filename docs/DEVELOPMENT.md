# 二次开发指南（Development Guide）

本文档面向希望在此基础上继续开发（二次/三次开发）的开发者，说明架构、数据流与常见开发场景。

## 1. 架构概览

桌面版由两个进程内服务 + 一个原生窗口组成，统一由 `DouYinSparkFlow/launcher.py` 拉起：

```
launcher.py（入口）
├── Web 控制台服务  FastAPI + uvicorn   127.0.0.1:8787  ← pywebview 原生窗口加载
├── 登录桌面服务    FastAPI + uvicorn   127.0.0.1:18090  ← 扫码登录（后台浏览器 + 二维码）
└── pywebview 窗口  WebView2 内核，加载 8787 控制台
     └── pystray 系统托盘：常驻/显示窗口/退出
```

- **主界面**：pywebview 原生窗口（WebView2 内核），也可浏览器访问 `http://127.0.0.1:8787`（`main.py --web`）
- **窗口生命周期**：关闭窗口不退出程序（销毁 WebView2 释放内存，常驻约 5MB）；托盘"显示窗口"重新创建；托盘"退出"才真正结束进程。`launcher.py` 的 `main()` 会等待窗口重建请求（`show_request` 事件）
- **单实例**：`utils/config.py` 的 `acquire_single_instance`（PID 锁 + 存活探测），8787 端口占用为兜底
- **扫码登录**：登录浏览器为后台进程（真实浏览器环境，抖音要求），应用窗口内显示二维码；触发安全验证时浏览器自动弹出；登录成功后 cookies 入库
- **定时发送**：`webui/ops.py` 把时间窗口写入 `config.json` 的 `dailySendWindow`，web 进程内调度器执行（Windows 无 cron，跳过 crontab 分支）
- **任务子进程**：定时触发/手动立即发送 = `webui/ops.py` 的 `run_background_command` 拉起子进程 `exe main.py --doTask`（打包版）或 `venv python main.py --doTask`（开发版）；子进程持有 `data_dir()/logs/task.run.lock` 任务锁

## 2. 目录速览

| 目录/文件 | 职责 | 常见开发场景 |
|---|---|---|
| `core/tasks.py` | 任务调度核心：扫描好友、构建消息、发送、确认、账号过滤 | 改发送策略、时序 |
| `core/browser.py` | Playwright 封装：系统浏览器探测、profile、网络路由 | 改浏览器参数、代理逻辑 |
| `core/send_state.py` | 发送状态机：强确认/待重试 | 改重试规则 |
| `core/protocol_dispatch.py` + `core/protocol_sender.mjs` | Node 协议发送 | 改协议发送 |
| `webui/app.py` | FastAPI 路由、登录代理 | 加页面/接口 |
| `webui/ops.py` | 操作接口、调度器、任务子进程、停止任务 | 加管理操作 |
| `webui/login_lock.py` | 登录工作区租约（单机语义） | 改登录并发行为 |
| `webui/templates/` + `webui/static/` | 控制台前端（Jinja2 + Vanilla JS） | 改界面 |
| `login_desktop_server.py` | 登录桌面服务：二维码、后台浏览器、安全验证弹窗 | 改登录流程 |
| `utils/config.py` | 配置、环境识别、数据目录、单实例锁 | 加配置项 |
| `utils/logger.py` | 日志（`read_text_autodetect` 兼容 GBK 旧日志） | 读日志 |
| `utils/process.py` | 进程存活探测（OpenProcess + GetExitCodeProcess） | 判活 |
| `launcher.py` | 桌面启动器：双服务、窗口、托盘、js_api、CLI 路由 | 改窗口/托盘行为 |

## 3. 环境识别与路径

`utils/config.py` 提供 `get_environment()`，返回 `PACKED`（PyInstaller 打包版）/ `LOCAL`（开发版）/ `GITHUBACTION`。

**路径约定（重要）：**

| 用途 | 打包版（PACKED） | 开发版（LOCAL） |
|---|---|---|
| 数据根目录 `data_dir()` | `%APPDATA%\DouYinSparkFlow\`（可用 `SPARKFLOW_DATA_DIR` 覆盖） | `DouYinSparkFlow\state\` |
| 配置/用户数据（config.json 等） | `data_dir()` | `DouYinSparkFlow\`（仓库根） |
| 浏览器 profile | `data_dir()\browser-profiles\` | `state\browser-profiles\` |
| 登录 profile | `data_dir()\login-profile\` | `state\login-profile\` |
| 日志 | `data_dir()\logs\` | `state\logs\` 与仓库 `logs\` |
| 任务锁 | `data_dir()\logs\task.run.lock` | 同上 |
| 账号锁 | `data_dir()\logs\browser-account-locks\` | 同上 |

**新增路径时请调用 `data_dir()` / `browser_profile_root()`，不要硬编码。** 打包版安装目录是只读的，任何写入都必须落在 `data_dir()` 之下。

**浏览器定位**：`core/browser.py` 的 `system_browser_executable()` 探测系统 Edge / Chrome；任何用 Playwright 的模块（登录服务、发送任务）必须调用 `configure_playwright_environment()`（登录服务曾漏调导致打包版 `/open-login` 500）。

## 4. 登录流程（单机工作区）

- `webui/login_lock.py`：单机租约（无队列）——同一时刻只允许一个账号处于登录工作区；`request_workspace` 直接激活，心跳保活，退出/超时释放
- 前端：账号管理页弹窗内嵌二维码（轮询 `/login-desktop/qr` 代理获取，携带 `X-Login-Desktop-Token` 头）；登录完成后前端自动保存，保存后后端显式 `/close` 关闭浏览器并清理 profile
- 安全验证：`login_desktop_server.py` 检测页面出现"安全验证/滑块"关键词时自动弹出浏览器窗口供人工处理
- 重登录自动保存：发送控制台触发重新登录时记录 `pendingReloginUid`，登录完成自动保存该账号
- 18090 端口全部端点需 token 认证（`utils/config.py` 的 `login_desktop_auth_token()`），webui 经 `call_login_desktop()` 自动携带；两服务均有 Host 头校验

## 5. 常见开发场景

### 5.1 修改消息模板 / 发送内容
- 默认模板在 `utils/config.py` 的 `DEFAULT_CONFIG["messageTemplate"]` 与 `sendStrategy.messageVariants`
- 控制台「运行配置」页也可在线修改（存入 `config.json`）

### 5.2 新增一个 Web 页面
1. 在 `webui/app.py` 注册路由（保留 CSRF 校验，参考现有路由）
2. 模板放 `webui/templates/`，静态资源放 `webui/static/`（模板继承 `base.html`，样式走 `app.css` 的 CSS 变量）
3. 涉及本地能力（保存对话框等）时：桌面版经 `launcher.py` 的 `Api`（js_api）实现，浏览器模式必须提供降级路径
4. 测试：`tests/test_webui_safety.py` 风格补充用例

### 5.3 修改发送频率/延迟
- `config.json` 的 `sendStrategy.accountStartDelaySecondsMin/Max`、`messageIntervalSecondsMin/Max`
- 逻辑在 `core/tasks.py` 的 `_normalize_send_strategy` 与随机延迟处

### 5.4 新增后台子进程任务
- 沿用 `webui/ops.py` 的 `run_background_command`（已注入 `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1` 防子进程日志乱码）
- 打包版下子进程 = `exe main.py --doTask` 等 CLI 模式；launcher 的 `main()` 会把首个参数为 `main.py` 的调用路由到 CLI 模式，**不要让子进程再开桌面窗口**
- 存活探测用 `utils/process.py` 的 `pid_is_alive()`，**不要**用 `os.kill(pid, 0)`（Windows 上会广播 Ctrl+C）

### 5.5 换应用图标
- 替换 `packaging/windows/app.ico`（可用 `python packaging/windows/generate_icon.py` 重新生成占位图标）
- 重新运行 `build.ps1`

## 6. 构建与发布

```powershell
# 完整构建安装包
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 [-Version x.y.z] [-AppOnly]
```

- `build.ps1` **必须保持纯 ASCII**（无 BOM 的 .ps1 被 Windows PowerShell 5.1 按 ANSI 读取，中文注释会破坏脚本；`.gitattributes` 强制 CRLF）
- 打包版不再内置 Chromium（改用用户系统 Edge/Chrome），安装包约 70MB、应用目录约 180MB

**发布流程（推 tag 自动构建）：**

1. 本地开发验证（`python launcher.py`）+ 跑全部测试
2. 提交并推送代码
3. 推 tag：`git tag v1.3.0 && git push origin v1.3.0`
4. GitHub Actions 自动构建并创建 Release（见 `.github/workflows/release.yml`）

产物说明：

```
dist\DouYinSparkFlow-Setup-<版本>.exe   # 安装包（Inno Setup，per-user 安装）
dist\app\                              # 绿色版应用目录
```

## 7. 测试

```powershell
cd DouYinSparkFlow
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

> 测试必须从 `DouYinSparkFlow/` 目录运行（测试用 `from core import ...`，依赖 cwd）。

**测试怪癖：**

- `tests/test_network_fallback.py` 与 `test_config_contract.py` 是源码契约测试：直接读 `.py` 文本断言字符串（如 `"direct", "mihomo"`）。改写 `core/browser.py`、`core/friends.py`、`core/tasks.py`、`utils/config.py` 的对应逻辑需同步更新断言
- `test_config_contract.py` 断言 `config.example.json` 与 `DEFAULT_CONFIG` 完全一致：改默认配置必须同步更新 example
- `test_webui_safety.py` 中 crontab 用例在 Windows 自动跳过（桌面版用应用内调度），属预期

## 8. 已知边界

- 扫码登录依赖真实浏览器（系统 Edge/Chrome），WebView2 无法替代——这是抖音登录的技术限制
- 协议发送（`useProtocolSender=true`）需要 Node.js：打包版内置，开发版需本机安装
- 打包版窗口无控制台输出：一切异常先查 `%APPDATA%\DouYinSparkFlow\logs\launcher.log`（launcher 自身）与 `douyin-sparkflow.log`（任务子进程）
- 复现打包版后端异常：开发模式跑 `python login_desktop_server.py`，必要时临时设 `$env:PLAYWRIGHT_BROWSERS_PATH = "<仓库>\dist\app\chrome"` 模拟（注意当前打包版已改用系统浏览器，该变量仅历史用途）
- PyInstaller 打包的 exe 可能被杀软误报，发布说明中建议注明
