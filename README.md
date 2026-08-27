# DouYin SparkFlow — Windows 桌面版

<div align="center">

---

## 功能特性

- **自动维护火花标记** — 智能识别需要维护的好友关系，自动发送消息保持火花
- **多账号管理** — 支持同时管理多个抖音账号，集中控制、独立配置
- **定时任务调度** — 灵活的时间窗口策略，应用内调度，无需 cron
- **手动发送控制** — 发送控制台支持立即发送、全部重发、仅重发失败、仅补发未发送
- **消息模板系统** — 内置一言、节日祝福等模板，支持自定义
- **可视化控制台** — 内嵌原生窗口（pywebview），也可浏览器访问；多用户登录与账号权限隔离
- **扫码登录** — 弹出 Chromium 窗口扫码，登录态持久化到本地，多用户排队使用登录工作区
- **运行日志** — 实时查看任务输出，一键导出日志文件（弹出"另存为"对话框选择位置）
- **纯桌面端** — 无 Docker、无服务器组件；安装包内置 Python 运行时 + Playwright Chromium + Node.js，目标机器零依赖

## 快速开始（开发模式）

```bash
# 1. 克隆仓库
git clone <你的仓库地址> douyin-sparkflow-desktop
cd douyin-sparkflow-desktop/DouYinSparkFlow

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt -r requirements-web.txt -r requirements-build.txt

# 3. 安装 Playwright 浏览器（约 170MB，仅首次；协议发送还需本机安装 Node.js）
python -m playwright install chromium

# 4. 启动桌面版（原生窗口）
python launcher.py

# 或使用浏览器模式（不弹原生窗口，访问 http://127.0.0.1:8787）
python main.py --web
```

> 国内网络可加清华源：`-i https://pypi.tuna.tsinghua.edu.cn/simple`

## 构建安装程序

前置条件（仅构建机需要，用户机器不需要）：

- Windows 10/11 x64
- Python 3.9+、Node.js 18+（需在 PATH 中）
- [Inno Setup 6](https://jrsoftware.org/isdl.php)（生成安装包；不装则只产出应用目录）

```powershell
# 一键构建（自动：建 venv → 装依赖 → 下浏览器 → PyInstaller → 组装 → Inno Setup）
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1

# 指定版本号
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -Version 1.0.1

# 只打包应用目录（不生成安装包，日常验证打包版行为用这个更快）
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -AppOnly
```

**产物：**

```
dist\DouYinSparkFlow-Setup-1.0.0.exe   ← 安装包（发布给用户的就是这个，约 220MB）
dist\app\                              ← 应用目录（绿色版，约 680MB，可整体复制分发）
```

## 项目结构

```
douyin-sparkflow-desktop/
├── DouYinSparkFlow/               # 核心应用源码
│   ├── core/                      # 核心功能模块
│   │   ├── browser.py             # Playwright 浏览器控制（按环境解析浏览器与 profile 路径）
│   │   ├── friends.py             # 好友管理（含网络路由回退）
│   │   ├── login.py               # 登录处理
│   │   ├── msg_builder.py         # 消息构建
│   │   ├── protocol_dispatch.py   # 协议发送调度（Node.js）
│   │   ├── protocol_sender.mjs    # 协议发送脚本（Node）
│   │   ├── send_state.py          # 发送状态机（强确认/重试）
│   │   └── tasks.py               # 任务调度（核心）
│   ├── webui/                     # Web 控制台（FastAPI + Jinja2 + Vanilla JS）
│   │   ├── app.py                 # FastAPI 主应用（127.0.0.1:8787）
│   │   ├── auth.py                # 认证模块
│   │   ├── users.py               # Web 用户与账号权限
│   │   ├── ops.py                 # 操作接口（含定时调度、后台任务）
│   │   ├── login_lock.py          # 登录工作区租约与排队
│   │   ├── static/                # 静态资源
│   │   └── templates/             # HTML 模板
│   ├── utils/                     # 工具模块
│   │   ├── config.py              # 配置管理（含打包环境识别与数据目录解析）
│   │   └── logger.py              # 日志
│   ├── scripts/                   # 辅助脚本
│   ├── tests/                     # 单元测试（unittest）
│   ├── launcher.py                # ★ 桌面版启动器（pywebview 原生窗口 + 双服务 + js_api）
│   ├── login_desktop_server.py    # 登录桌面服务（127.0.0.1:18090，扫码）
│   ├── main.py                    # 传统入口（CLI / --web 浏览器模式 / --doTask）
│   ├── config.example.json        # 应用配置模板
│   ├── usersData.example.json     # 用户数据模板
│   └── requirements*.txt          # 依赖清单
├── packaging/windows/
│   ├── build.ps1                  # ★ 一键构建脚本
│   ├── installer.iss              # Inno Setup 安装脚本
│   ├── generate_icon.py           # 图标生成脚本
│   └── app.ico                    # 应用图标（可替换）
├── docs/                          # 文档（DEVELOPMENT.md：二次开发指南）
├── AGENTS.md                      # AI 辅助开发指令
└── README.md                      # 本文件
```

## 🔧 数据与路径约定（二次开发必读）

| 环境             | 配置/数据位置                  | 说明                                    |
| ---------------- | ------------------------------ | --------------------------------------- |
| 打包版（PACKED） | `%APPDATA%\DouYinSparkFlow\` | 可用环境变量`SPARKFLOW_DATA_DIR` 覆盖 |
| 开发版（LOCAL）  | `DouYinSparkFlow\state\`     | 与仓库隔离，不进 Git                    |

运行时数据包括：`config.json`、`usersData.json`、`webui_settings.json`、`browser-profiles\`（登录态）、`login-profile\`、`logs\`、`state\`。开发版的 `config.json` 等配置位于 `DouYinSparkFlow\` 根目录（已被 gitignore）。

路径解析逻辑集中在 `utils/config.py` 的 `data_dir()` / `_runtime_root()`，以及 `core/browser.py` 的 `browser_profile_root()`。**新增功能涉及路径时，请走这两个入口，不要硬编码绝对路径。**

浏览器定位：`core/browser.py` 的 `configure_playwright_environment()` 会把 `PLAYWRIGHT_BROWSERS_PATH` 指向：

- 打包版 → exe 旁 `chrome\` 目录
- 开发版 → 仓库 `DouYinSparkFlow\chrome\`（`python -m playwright install chromium` 后目录结构对齐）

## 测试

```bash
cd DouYinSparkFlow
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

> 测试必须从 `DouYinSparkFlow/` 目录运行（测试用 `from core import ...`，依赖 cwd）。

## 二次开发指南

1. **改界面**：编辑 `webui/templates/*.html` 与 `webui/static/*.js|css`，开发模式下刷新即生效
2. **改发送逻辑**：`core/tasks.py` 是核心调度，`core/send_state.py` 控制"已发送/待重试"状态机
3. **新增页面/接口**：在 `webui/app.py` 注册路由（注意认证与 CSRF），模板放 `webui/templates/`
4. **新增本地能力（弹窗/保存对话框等）**：通过 `launcher.py` 的 `Api` 类（js_api）暴露给前端，浏览器模式需提供降级路径
5. **打包**：改完代码后跑 `build.ps1` 验证安装包；验证打包版特有行为（环境识别、路径解析）必须重新构建，日常开发用 `python launcher.py` 即可
6. **内置 Node**：协议发送（`core/protocol_sender.mjs`）需要 Node，打包时自动内置；开发模式需本机安装 Node
7. **更换图标**：替换 `packaging/windows/app.ico`（或先跑 `generate_icon.py` 重新生成）后重新构建

更详细的架构、数据流与开发场景见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

## 常见问题

- **启动后窗口空白**：检查 WebView2 运行时（Win10/11 一般自带），可在 Edge 设置里修复
- **扫码登录弹不出窗口**：确认没有其他实例占用 18090 端口；登录窗口使用内置 Chromium，首次打开稍慢
- **点击下载日志没反应/想选保存位置**：桌面版点击后弹出 Windows"另存为"对话框，选择位置后保存；浏览器模式直接下载
- **杀软误报**：PyInstaller 打包的 exe 可能被部分杀软误报，属正常现象，可在发布说明中注明
- **用户机器无需任何环境**：Python、Node、浏览器全部内置

## 免责声明

> **本项目仅供学习研究使用**

- 本工具仅用于技术学习和个人使用，不得用于任何商业用途
- 使用本工具产生的一切后果由使用者自行承担
- 请遵守平台规则，合理使用，避免频繁操作
- 请评估使用风险，建议使用小号测试

## 许可证

[MIT License](DouYinSparkFlow/LICENSE)
