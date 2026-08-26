# 二次开发指南（Development Guide）

本文档面向希望在此基础上继续开发（二次/三次开发）的开发者，说明架构、数据流与常见开发场景。

## 1. 架构概览

桌面版由三个进程内服务组成，统一由 `DouYinSparkFlow/launcher.py` 拉起：

```
launcher.py（入口）
├── Web 控制台服务  FastAPI + uvicorn   127.0.0.1:8787  ← pywebview 原生窗口加载
├── 登录桌面服务    FastAPI + uvicorn   127.0.0.1:18090  ← 扫码登录（弹出真实 Chromium）
└── （浏览器发送）  Playwright Chromium（随任务启动）
```

- **主界面**：pywebview 原生窗口内嵌 Web 控制台（WebView2 内核），也可浏览器访问 `http://127.0.0.1:8787`
- **扫码登录**：控制台发起登录 → 登录桌面服务弹出 Chromium 窗口（真实浏览器环境，抖音要求）→ 扫码 → cookies 入库
- **定时发送**：`webui/ops.py` 的 `update_daily_schedule` 把时间窗口写入 `config.json` 的 `dailySendWindow`，由 web 进程内调度器执行（Windows 无 cron，已在代码中跳过 crontab 分支）

## 2. 目录速览

| 目录/文件 | 职责 | 常见开发场景 |
|---|---|---|
| `core/tasks.py` | 任务调度核心：扫描好友、构建消息、发送、确认 | 改发送策略、时序 |
| `core/browser.py` | Playwright 封装：浏览器定位、profile、网络路由 | 改浏览器参数、代理逻辑 |
| `core/send_state.py` | 发送状态机：已发送/待重试/强确认 | 改重试规则 |
| `core/protocol_dispatch.py` + `core/protocol_sender.mjs` | Node 协议发送 | 改协议发送 |
| `webui/app.py` | FastAPI 路由、认证、登录代理 | 加页面/接口 |
| `webui/ops.py` | 操作接口、调度、日志 | 加管理操作 |
| `webui/templates/` + `webui/static/` | 控制台前端（Jinja2 + Vanilla JS） | 改界面 |
| `utils/config.py` | 配置、环境识别、数据目录解析 | 加配置项 |
| `launcher.py` | 桌面启动器 | 改窗口行为 |

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

**新增路径时请调用 `data_dir()` / `browser_profile_root()`，不要硬编码。** 打包版安装目录是只读的，任何写入都必须落在 `data_dir()` 之下。

## 4. 常见开发场景

### 4.1 修改消息模板 / 发送内容
- 默认模板在 `utils/config.py` 的 `DEFAULT_CONFIG["messageTemplate"]` 与 `sendStrategy.messageVariants`
- 控制台「设置」页也可在线修改（存入 `config.json`）

### 4.2 新增一个 Web 页面
1. 在 `webui/app.py` 注册路由（注意认证与 CSRF，参考现有路由）
2. 模板放 `webui/templates/`，静态资源放 `webui/static/`
3. 测试：`tests/test_webui_safety.py` 风格补充用例

### 4.3 修改发送频率/延迟
- `config.json` 的 `sendStrategy.accountStartDelaySecondsMin/Max`、`messageIntervalSecondsMin/Max`
- 逻辑在 `core/tasks.py` 的 `_normalize_send_strategy` 与随机延迟处

### 4.4 换应用图标
- 替换 `packaging/windows/app.ico`（可用 `python packaging/windows/generate_icon.py` 重新生成占位图标）
- 重新运行 `build.ps1`

## 5. 构建与发布

```powershell
# 完整构建安装包
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1

# 发布流程建议
# 1. 本地开发验证（python launcher.py）
# 2. 跑全部测试（python -m unittest discover -s tests）
# 3. 构建安装包
# 4. 在干净机器/虚拟机安装验证（可选）
# 5. 上传 GitHub Releases 发布
```

构建产物说明：

```
dist\DouYinSparkFlow-Setup-<版本>.exe   # 安装包（Inno Setup，per-user 安装）
dist\app\                              # 绿色版应用目录
```

## 6. 测试

```powershell
cd DouYinSparkFlow
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

> 提示：`tests/test_deployment_contract.py` 已随 Docker 版移除；`test_webui_safety.py` 中 crontab 相关用例在 Windows 上自动跳过（桌面版用应用内调度）。

## 7. 已知边界

- 扫码登录依赖真实 Chromium（内置），WebView2 无法替代——这是抖音登录的技术限制
- 协议发送（`useProtocolSender=true`）需要 Node.js：打包版内置，开发版需本机安装
- 安装包约 350-400MB（含 Chromium 与 Node），属正常体积
- PyInstaller 打包的 exe 可能被杀软误报，发布说明中建议注明
