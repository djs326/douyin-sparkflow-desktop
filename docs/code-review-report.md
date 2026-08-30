# 整仓代码审查报告：抖音多账号火花自动维护工具

**审查范围**：`DouYinSparkFlow/` 全部源码（launcher.py、main.py、login_desktop_server.py、core/ 9 文件、utils/ 6 文件、webui/ 6 文件 + 7 模板 + 静态资源、scripts/ 3 文件、tests/ 7 文件），并实跑测试基线。

**测试基线（实测）**：`python -m unittest discover -s tests -v` → **Ran 44 tests, OK (skipped=1)**。（注：实际测试文件共 6 个、44 个用例：`test_webui_safety.py` 20、`test_send_state.py` 7、`test_login_desktop_auth.py` 5、`test_config_contract.py` 5、`test_multiuser.py` 4、`test_network_fallback.py` 3；跳过 1 个为 Windows 预期跳过的 crontab 用例。）

---

## 🔴 Critical

无单点必现的 Critical 级漏洞（锁的 ABA 防护、CSRF 覆盖、token 全覆盖、Host 校验、pid 探测等红线大多守住了）。但以下 **H1、H3、H4 与 M4 的组合** 在本机威胁模型下等价于"本机任意进程 = 登录会话控制 + 本机文件读取"，建议按 Critical 对待并优先处理：

> `token 明文落盘`（utils/config.py:146-172） + `/debug/*` 任意 JS 执行 / `file://` 导航 / 全量网络捕获（login_desktop_server.py:938-1113） + 协议 SDK 无完整性校验且 vm 沙箱可逃逸（protocol_sender.mjs:116-152, 302-309）。任何能读 `data_dir()/state/login_desktop_auth.token` 的本地进程即可导出全部抖音 cookies、读取本机任意文件、篡改登录浏览器。

---

## 🟠 High

### H1. task_run_lock 对"空/不可解析"锁文件无条件删除 → 双任务并发竞态
**位置**：`core/tasks.py:2882-2904`
**问题**：锁文件内容无法 `int()` 解析（含空文件）时直接 `lock_path.unlink()` 重抢。空文件窗口真实存在：进程 A `open("x")` 创建锁后、写 PID 前（2876→2913 之间），进程 B 读到空内容 → 判 stale → 删除 → B 抢锁成功；Windows 下 A 的句柄仍指向已删除文件、照常执行任务 → **两个任务同时运行、重复发送**。
**为什么**：这是全局任务互斥的唯一防线，且与同文件账号锁（空内容等年龄）、`ops.py:184`（`pid is None` 需 `age>7200` 才 stale）策略互相矛盾，属疏漏。
**建议**：与账号锁对齐——不可解析内容 + 短年龄阈值（如 60s）才判 stale；unlink 前重读校验内容未变（消除 check-then-delete TOCTOU）。同族第二入口：stale 判定与 `unlink()` 非原子，两个等待进程可能 A 删 B 刚重建的新锁，需一并处理。

### H2. 账号锁对不可解析内容永不判 stale → 残留空锁卡死 2 小时
**位置**：`core/tasks.py:2259-2317`
**问题**：`_extract_lock_pid` 对空/半截内容返回 None → 只走年龄分支，`age<=7200` 判"不 stale" → 等待方每 5s 重试直到自己 7200s 超时。`stop_running_task` 的 `taskkill /F` 在持锁进程写完 PID 前强杀是常见操作 → 残留空锁使该账号后续任务**卡死 2 小时**；且账号锁获取发生在 `asyncio.wait_for` 超时守卫**之外**（2426 在 2437 之前），守卫救不了。
**建议**：pid 不可解析且年龄 > 60s 即按 stale 清理；或把账号锁获取纳入超时守卫。

### H3. WebSocket 代理绕过 Host 头校验，鉴权退化为"租约存在即通过"
**位置**：`webui/app.py:1374-1428`（`/login-desktop/proxy/websockify`）
**问题**：`localhost_guard` 是 `@app.middleware("http")`（BaseHTTPMiddleware），**WebSocket 握手完全不经过它**——无 Host 头/Origin 校验。且免登录下 `current_principal` 恒返回 admin、全生命周期无人调用 `issue_session`（`/login` 直接重定向）→ `session_id` 恒为 `""`，而租约写入的也是 `""` → `owns_login_lock(active, "admin", "")` 在租约活跃时**恒 True**。
**为什么**：这正是 Host 校验要防的 DNS rebinding 场景的明确绕过口子：恶意网页解析到 127.0.0.1 后即可在登录工作区活跃期间代理 noVNC 画面、看到并操控扫码登录（Firefox/Safari 无 PNA 防护）。
**建议**：WebSocket 端点手工校验 `Host`（复用 `web_middleware._hostname_allowed`）与 `Origin`（白名单 `http://127.0.0.1:8787` / `http://localhost:8787`）。现有测试无 websocket 用例，不会破坏断言。

### H4. 单目标重试在 webui 进程内同步执行；停止任务可杀死 Web 服务自身
**位置**：`webui/app.py:878-930`（`retry_account_target`）+ `webui/ops.py:112-133`（`stop_running_task`）
**问题**（两条独立链路）：
1. `retry_account_target` 用 `with task_run_lock(): await run_browser_tasks(...)` 在 **uvicorn 进程内**跑完整浏览器任务，锁内容写的是 `os.getpid()` = **webui 自身 pid**。用户点"停止任务" → `stop_running_task` 读锁得 pid 存活 → `taskkill /F /T /PID <webui自身>` → **整个 Web 控制台被自己杀死**。
2. `stop_running_task` 在 taskkill 失败/超时后仍**无条件 `unlink()` 任务锁**，且无 ABA 防护（删除前不重读锁内容比对）——taskkill 失败而旧任务存活时，新任务立即启动 → 双任务并发；极端时序下还会误删新任务的锁。
3. `task_run_lock` 等待循环是同步 `time.sleep(2)`（tasks.py:2910），在 async 路由内直接阻塞事件循环，最长 30 分钟。

**建议**：单目标重试改走 `run_background_command` 子进程（复用 `run_failed_retry_now` 机制，用 `SPARKFLOW_ACCOUNT_REFS` 环境变量指定账号+目标）；`stop_running_task` 在 taskkill 后轮询 `pid_is_alive()` 确认进程消失再删锁，删除前重读锁内容比对 pid，失败时保留锁并返回失败。

### H5. 协议 SDK 无完整性校验 + vm 沙箱注入真实 Node 全局 → 供应链任意代码执行
**位置**：`core/protocol_sender.mjs:116-152, 302-309`
**问题**：`ensureBundles()` 从硬编码 `douyinstatic.com` 下载 13 个 JS bundle，无 hash/SRI 校验（缓存命中仅 HEAD 探测可用性）；随后 `vm.runInNewContext` 执行，且第 266/297 行把真实 `Buffer`/`Blob`/`crypto`/`fetch` 注入沙箱——`Buffer.constructor('return process')()` 即可逃逸获得完整 Node 权限。
**为什么**：CDN 被攻破、DNS 劫持、或本地 `.im_sdk_cache` 被同机进程篡改（缓存路径在 `repoRoot` 下，同机可写）即任意代码执行。
**建议**：① 为每个 bundle 固定 sha256，下载与缓存读取均校验；② 沙箱注入替换为无逃逸的纯数据对象（自实现最小 Buffer polyfill、webcrypto 包装）；③ 考虑 `--experimental-permission` 收紧。

### H6. 登录服务 `/debug/*` 默认常开：任意 JS 执行、`file://` 导航、全量网络捕获
**位置**：`login_desktop_server.py:938-1113`
**问题**：`/debug/action` 的 `goto` 可导航任意 URL（含 `file://`），`eval`/`eval_in_frame` 在登录页上下文执行任意 JS；`/debug/net_capture` 的 `on_request` 把每个请求**完整 headers（含 Cookie）**存入内存 `_net_log` 可读回。
**为什么**：持 token 者可 `goto("file:///C:/...")` + eval 读回 → **任意本地文件读取**；可窃取已登录抖音会话；可 SSRF 内网。token 又明文落盘（M4），同机任意进程可达。这些调试端点**无开关、默认常开**。
**建议**：默认禁用（`LOGIN_DESKTOP_DEBUG=1` 才挂载）或使用第二把独立调试 token；`goto` 限制 `https://creator.douyin.com` / `https://www.douyin.com` 白名单；`net_capture` 落库前剥离 `cookie`/`authorization` 头。

---

## 🟡 Medium

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| M1 | `login_desktop_server.py:883-895, 692-697` | `/export` 不参与 `_page_operation_lock`，与二维码刷新/打开登录并发竞态：export 的 5s 超时后 `goto(WWW_SELF_URL)` 会反过来打断刷新中的二维码页面，cookies 提取路径随并发时序漂移 | export 同样走 `_page_operation_lock`，锁占用时返回 503 让 webui 重试 |
| M2 | `core/protocol_sender.mjs:154-166, 354-371, 500-520` | Node 全局 `fetch`（undici）不读任何代理环境变量——mihomo 模式下浏览器走代理而协议发送**静默直连**，行为与 Python 侧 `requests` 不一致 | payload 传 `proxy` 字段，mjs 用 `EnvHttpProxyAgent`/`ProxyAgent` 构造 fetch |
| M3 | `login_desktop_server.py:303-307, 280-286` | idle 超时（默认 1800s）在用户扫码/处理滑块期间静默关闭登录浏览器；`/status` 高频轮询不 `mark_activity`，无法续命 | status 低频续期（如距上次活动 <60s 才刷新），或把 idle 基准改为"页面已登录态" |
| M4 | `utils/config.py:146-172` | token 明文落盘无 ACL 收紧；`SPARKFLOW_DATA_DIR` 可指到共享盘/云同步目录 | 落盘后收紧 ACL（icacls/SetNamedSecurityInfo）；文档明示该文件等同密码 |
| M5 | `utils/config.py:250-296` + `core/tasks.py:2012-2216` + `webui/app.py:798,820,846,953,982` | `json_file_lock` 只保护**单次读**与**单次写**，不覆盖读-改-写整体窗口；webui 与任务子进程并发写 usersData → last-writer-wins，任务写入的发送账本（message_history/failure_queue）可被覆盖 → 重复发送/漏发 | 提供原子 `update_user_data(mutator)` 封装（锁内完成整个 RMW），路由与子进程统一走它 |
| M6 | `webui/ops.py:403-412` | `run_task_now` 锁检查是 TOCTOU 快照，无进程内互斥——连点按钮/调度器+手动并发会各拉起一个子进程，第二个空耗 30 分钟 | 路由入口加 `asyncio.Lock` 串行化三个 run 入口 |
| M7 | `webui/ops.py:173-203, 782-788` | 假活锁：任务崩溃后 PID 被复用，或子进程挂起 → 锁永远 `running=True`，调度器与手动补发**静默停摆**，横幅一直"运行中"无告警（age>7200 兜底仅在 pid=None 时生效） | 对"pid 存活但锁 age 超合理上限"标记可疑并醒目告警，引导用户手动停止（不自动删锁，守住 `test_stale_lock_inspection` 断言） |
| M8 | `webui/ops.py:756-790` vs `547-559` | 应用内调度器用 `now.hour > endHour` 判定，endHour=18 时 18:00-18:59 内每个 interval 刻度都触发 unsent 任务（约 4 次多余触发）；crontab 版只到 endHour-1 | 对齐语义：`now.hour >= endHour and now.minute > 0` 才跳过 |
| M9 | `webui/ops.py:441-445` | `Path("task_error.txt").write_text(traceback)` 用 **cwd 相对路径**，违反"路径一律经 data_dir()"约定；打包版安装目录只读时 except 内二次抛异常、掩盖原始错误 | 改 `logger.exception` 或写 `data_dir()/logs/`；`import traceback` 提到模块顶 |
| M10 | `core/tasks.py:2398-2400` | `run_browser_tasks` finally 中 `playwright.stop()` 在 `browser.close()` **之前**——stop 断开 driver 后 close 会抛异常并覆盖主流程结果；与 browser.py:141-146 的正确顺序相反 | 改为先 `browser.close()` 再 `playwright.stop()` |
| M11 | `core/tasks.py:1601-1617` | `_normalize_send_window` 直接 `int(raw.get("startHour"))`，config.json 中 `null`/非数字 → runTasks 崩溃（其余 `_normalize_*` 均有 `_coerce_*` 防御） | 改用 `_coerce_non_negative_int`，非法值走默认并告警 |
| M12 | `core/tasks.py:2366, 2421` | `taskCount=0` 时 `asyncio.Semaphore(0)` 合法创建，所有账号任务在 `async with semaphore`（超时守卫外）永久阻塞 → 任务挂死、任务锁永占 | `max(1, taskCount)` |
| M13 | `login_desktop_server.py:1050-1113` | `/debug/net_capture` 重复 start 累积 `page.on` handler（不注销）；内存滞留全量 Cookie 与响应体；`_net_log` 无锁 | start 前 `remove_listener` 或单例 handler；记录前剥离敏感头；body 预览限 JSON 短截 |
| M14 | `webui/app.py:487-498` | 无 CSP / X-Frame-Options / X-Content-Type-Options 安全头（无 XSS 漏洞但纵深防御缺失） | 加安全头中间件（注意不破坏现有测试对 cache-control 的断言） |
| M15 | `webui/ops.py:467-472, app.py:1203` | `read_log_tail` 每次全量读 5MB 日志再截尾，页面轮询下浪费 I/O | seek 尾部倒读或读尾部 N 字节解码切行；前端日志轮询改 JSON 接口（当前每 5s 拉整页 HTML+DOMParser） |
| M16 | `webui/app.py:1071` | `/settings` 保存 `int(form.get("ui_port"))` 无捕获，非数字 → 500（同文件其他字段用 `coerce_int`） | 用 `coerce_int` 并给下限 1 |
| M17 | `login_desktop_server.py:594, 672` | `logger` **未定义**（import 清单无 logger），两处 `logger.info("...安全验证...")` 必抛 NameError 被外层 `except Exception: pass` 静默吞掉——关键调试信息永不落日志 | 按项目约定 `from utils.logger import setup_logger` 建立 logger |

---

## 🟢 Low

1. **`utils/web_middleware.py:19-21`**：`host_header.split(":",1)[0]` 在 `[]` 剥离**之前**执行，合法 IPv6 Host `[::1]:8787` 被拆成 `"["` → 403（白名单里的 `::1` 永远走不到）；空 Host 放行是 DNS rebinding 的边界妥协。建议先剥离 `[]` 再 split；空 Host 直接拒绝。
2. **`utils/config.py:265-277`**：`json_file_lock` 以 `a+b` 打开，`write(b"x")` 每次**追加 1 字节**——锁文件无限增长（每次配置写入 +1B）。应改为 `r+b` 或先清空。
3. **`webui/login_lock.py:21-32`**：LOCAL 分支锁文件用 `repo_root().parent / "state"`（**仓库根 state/**），与 `data_dir()`（`DouYinSparkFlow/state/`）不一致——锁文件与数据分离在"另一个人"的目录里。应统一走 `data_dir()`。
4. **`utils/config.py:299-310`**：`get_config`/`save_config` 无 `json_file_lock`（usersData 有）——当前 webui 是唯一写者、读-写靠原子写兜底，但 `_merge_defaults` 读-改-写窗口无互斥，建议与 M5 一起收敛到原子更新封装。
5. **`utils/config.py:202-209`**：`_merge_defaults` 仅一层 dict 合并——旧 config.json 中已存在的嵌套键（如 `sendStrategy`）被旧值整体覆盖，**DEFAULT_CONFIG 新增的子键在升级后不生效**（如将来给 `sendStrategy` 加新选项会静默丢失）。建议深合并。
6. **`core/browser.py:198-201, 230-233`**：打包版缺系统浏览器时尝试 `sys.executable -m playwright install`（exe 不支持 `-m`）→ 静默失败后 `sys.exit(1)`。打包版应直接报"未找到 Edge/Chrome"。
7. **`core/friends.py:204-208`**：`page.locator(...).element_handle()` 找不到时**抛异常而非返回 None**，`if not scrollable_element:` 是死代码分支。
8. **`core/tasks.py:1511, 2784, 2702-2705`**：发送成功写入 `message_history` 用的是 `_normalize_target_name()` 后的显示名，而 `_target_sent_today` 查询用 `user["targets"]` 原始串——原始 targets 含首尾空白/全角空格时 key 错位 → 已发送目标被重复发送。建议统一规范化口径。
9. **`core/tasks.py:964-967`**：`detect_message_already_sent` 裸 `except Exception: pass` 吞异常——异常时消息可能已发出，会入队重发且无日志。至少 `logger.warning`。
10. **`core/tasks.py:874`**：`confirm_message_sent` 固定 8 秒 deadline，抖音私信渲染慢于 8s 即确认失败入队 → 下一轮重发同一条。建议配置化或失败后仅标"待验证"。
11. **`core/tasks.py:468`**：`_detect_send_failure_indicator` 关键词含 `"重试"/"retry"/"resend"`，基于全页面 innerText 扫描，页面其他区域出现"重试"字样即误判失败 → 污染失败队列。建议限定到最近消息行区域。
12. **`core/tasks.py:384-395`**：`save_debug_artifacts` 无异常保护，截图失败会把一次可能成功的发送拖入失败分支。内部 try/except + warning。
13. **`login_desktop_server.py:1118-1121`**：启动分支 `print(f"...auth token: {AUTH_TOKEN}")`——全仓库唯一把共享密钥输出到 stdout 的地方（AGENTS.md 推荐的打包版复现方式就是 `python login_desktop_server.py`，stdout 重定向即永久落日志）。删除该 print。
14. **`login_desktop_server.py:892`**：`/export` 把底层异常原文拼进 400 响应（Playwright 异常含 URL/页面状态）。只回显异常类名，详情进 logger。
15. **`login_desktop_server.py:976-1042`**：`/debug/action` 参数 `int()/float()`/`payload["x"]` 均无校验，非法输入直接 500（调试端点应为 4xx）。
16. **`login_desktop_server.py:814-818`**：OPTIONS 预检不豁免——未来若浏览器直连 18090（跨端口+自定义头必触发预检）会静默阻断。建议 OPTIONS 返回 204 并注释"仅限本机后端调用"。
17. **`login_desktop_server.py:425-448`**：`stop(clear_profile=True)` 在 `context.close()` 后立即 `shutil.rmtree`，Windows 下 Chromium 异步退出致 profile 残留（rmtree 失败被 ignore_errors 吞掉），是"重置后登录页白屏"的隐性来源。rmtree 前重试几次并记日志。
18. **`login_desktop_server.py:476-568, 459-474`**：`/status` 返回本机 `profile_dir` 绝对路径、`focus`/`open-login` 返回 `page.url`——信息面偏宽。profile_dir 改布尔/相对标识。
19. **`webui/app.py:1207-1250, 1066`**：`ops_log_file` 设置无路径约束，指向任意文本文件时 `/ops/logs/download` 可读、`/ops/logs/save` 可覆盖（写入被限 home/data_dir，读取无限制）。读取前校验位于 `data_dir()/logs/` 下。
20. **`webui/app.py:330-345`**：`fetch_login_desktop_asset` 的 `quote(safe="/._-")` 保留 `..`，`/login-desktop/proxy/../foo` 归一化后可访问 noVNC 根下其他路径（限定同一 host，面窄）。拒绝含 `..` 的路径段。
21. **`webui/ops.py:336-344`**：`run_background_command` 未置 `stdin=subprocess.DEVNULL`——子进程继承 webui 的 stdin，若任务代码走 `input()` 会挂起。
22. **`webui/app.py:182-183` vs `ops.py:732-743` vs `tasks.py:1585-1598`**：时区实现三处重复且基准不一致——`app.py` 的"今日"判定固定 UTC+8，页面快照与任务侧支持 `SPARKFLOW_TIMEZONE`/`TZ`。收敛到单一实现。
23. **`webui/users.py` + `webui/auth.py` 全量**：完整的多用户体系（pbkdf2、webui_users.json、account_refs 权限、登录限速）在桌面版**不可达**（`current_user` 恒 admin、`/admin/users/*` 恒 403、`/login` 恒重定向）——容器版遗留死代码，与 AGENTS.md"免登录"表述并存，维护成本高。建议加注释说明或条件编译。
24. **`usersData.json` 明文 cookies 无权限收紧**：`webui/users.py:133` 对 webui_users.json 有 `chmod 0o600`，但 usersData.json（含全部抖音登录态 cookies）没有——同机其他用户可读。建议同样收紧 ACL（PACKED 下尤其）。
25. **`webui/app.py:1186-1191`**：`/ops/schedule` 失败把 stderr 原文 flash 到页面（Jinja2 转义防了 XSS，但信息面偏宽）。截断到 200 字符。
26. **`webui/app.py:1460-1462`**：上游 `Retry-After` 非数字时 `int()` 抛 ValueError → 500。try/except 回退默认 2。
27. **`core/msg_builder.py:15-16`**：节日窗口 `FESTIVAL_WINDOW_START/END` 硬编码 2026-02-16~03-03——2027 年后启用 `happyNewYear` 也永不生效。建议并入 config。
28. **`core/tasks.py:32-33`**：模块级副作用 `debug_artifacts_dir.mkdir(...)` 在 import 时写盘——`test_tasks_import_does_not_require_runtime_user_data` 隐式依赖它不抛错，且纯 import 也会创建目录。
29. **`core/tasks.py:2406`**：`zip(users, results)` 依赖调用处两列表长度一致，脆弱。建议断言长度。
30. **`scripts/migrate_web_users.py:31-37`**：一次性脚本，密码走 `--zxb-password` 命令行参数（进程列表可见）。建议仅支持环境变量传入。

---

## ⚪ Nit

- **魔法数字**：`7200`（tasks.py 账号锁 stale/等待口径 ×3）、`1800`（任务锁等待）、`8`（发送确认 deadline）、`-32000,-32000`（隐藏窗口位置）、QR 尺寸 `120/0.8/1.25`——建议抽为模块级命名常量，尤其 7200/1800 口径不同极易误改。
- **`webui/ops.py:148-156`**：`task_run_lock_status` 空文件分支内**连续两个相同 return**，第二段死代码。
- **`webui/ops.py:385`**：`contextlib_suppress_json` 自定义类全仓库无使用点。
- **`webui/app.py:103-120, 541-547`**：`_LOGIN_FAILURE_*` 限速与 `require_user`/`require_admin` 恒放行分支为免登录下的死代码（测试有依赖，不能删，建议加注释）。
- **`webui/app.py:1592-1595, 1663-1666`**：`request_workspace` 的 `"full"/"queued"` 分支在桌面版单机语义下永不返回（login_lock.py 只返回 active）。
- **`login_desktop_server.py:1067,1088,1098`**：内联 `__import__("time")`/`__import__("asyncio")` 替代顶层 import，不利静态分析。
- **`core/protocol_sender.mjs:58-65`**：`messageIntervalSecondsMax` 无上限，巨值配置挂起至 600s 超时；`:31-36` cookie 值含 `;`/`=` 不转义（当前数据源可信）；`:781-794` 消息全文进 stdout 落任务日志（业务可接受但需知晓）。
- **`webui/static/app.js:806-820`**：tags 路径直接赋 `location.href` 无 scheme 校验（仅本机页内导航，风险低）；`:654` countdownTimer 死代码；`qrRefreshTimer` 未在 pagehide 清理；`styles.css` 未被引用（app.css 是实际样式）。

---

## 📌 契约测试专项风险（改动前必读）

- **`tests/test_network_fallback.py`** 逐字符串断言源码文本，**调用形态即契约**：
  - `core/browser.py`：`return ("direct", "mihomo")`、`async def select_douyin_network_mode`、`get_browser(network_mode=network_mode)`
  - `core/friends.py`：`for index, network_mode in enumerate(modes)`、`returned zero friends; trying next route`、`get_browser(GUI=False, network_mode=network_mode)`
  - `core/tasks.py`：`select_douyin_network_mode(CREATOR_HOME_URL)`（现 2364 行）、`get_browser(network_mode=network_mode)`（现 2387 行）——**任何把 `network_mode` 参数改名（如 route/mode）、提取成模块级变量、或提前到 import 时执行的网络模式预选，都会直接破坏测试**。重构网络选择逻辑必须同步更新断言。
- **`tests/test_config_contract.py`**：
  - `config.example.json` 与 `DEFAULT_CONFIG` 必须**逐键相等**（改默认配置必须同步 example，含嵌套 sendStrategy/persistentBrowserProfiles）；
  - `_schedule_timezone()` 无 env 时 `key == "Asia/Shanghai"`（tasks.py:1585-1598）——注意测试只 patch `SPARKFLOW_TIMEZONE=""`、**不清 `TZ`**：若 CI/本机设了 `TZ`，`TZ` 分支（1588）优先命中导致测试失败，属测试脆弱点；改时区优先级顺序会破坏断言；
  - `_normalize_persistent_profile_config` 的 `SPARKFLOW_BROWSER_PROFILE_ROOT` 优先级（tasks.py:129-133）不可调整；
  - 隐含契约：`useProtocolSender==False`、`persistentBrowserProfiles.enabled==True` 的默认值决定 `_split_sender_modes` 的任务分流路径。
- **`tests/test_webui_safety.py`**：`task_run_lock_status` **不得自动删除 stale 锁**（`test_stale_lock_inspection_does_not_delete_file` 直接断言文件存在）；`get_overview_snapshot` 序列化后**不得出现 `cookies`/`reason`/`serverReceipt` 字符串**（repr 全量断言，M5 给快照加字段要小心）；`/login-desktop/open` 必须保持 `call_login_desktop("/open-login", ..., timeout=90)`；`current_principal is None` 时 `/api/ops/overview` 必须 401；`app.js` 登录工作区 IIFE 被**字符串顺序断言**（273-286 行，重构登录前端必挂）；`accounts.html` 的 `data-login-qr`/`data-refresh-login-qr`/`login-desktop-controls` 与 `dashboard.html` 的 `data-overview-root`/`data-task-banner` 属性不可删。
- **`tests/test_send_state.py`**：`ops._build_target_status`、`tasks._prepare_active_users_for_run`/`_pending_failed_targets`/`_pending_unsent_targets` 的行为契约（强确认唯一已发态、legacy 记录可重试、attempt 上限、manual force_all 含强确认目标）。
- 本报告 H1-H6、M1-M16 的修复建议**均不破坏现有断言**（已逐条核对），唯一需留意 M5 若动 `save_userData` 签名或 overview 输出结构。

---

## 总体评价

代码整体质量明显高于同类桌面工具的平均水平：锁的 ABA 防护与超时守卫、强确认发送状态机、`pid_is_alive()` 统一收敛、路径全部经 `data_dir()`、CSRF/Host/token 三层 Web 防护、契约测试对核心语义的守护——这些关键设计都落实到位且相互印证，44 个测试全绿不是偶然。真正的短板集中在两类：**① 锁的边角状态策略三处不一致**（H1/H2：空锁文件在任务锁被无条件删、在账号锁永不判 stale）在"任务被强杀"这一真实操作下可演化成双任务重复发送或 2 小时瘫痪；**② 同机威胁模型下的凭据与调试面过大**（token 明文落盘 + `/debug/*` 任意执行 + 协议 SDK 无校验执行），以及"webui 内同步跑任务 vs taskkill 停止模型"的设计矛盾（H4 会杀死 Web 服务自身）。建议下一轮优先处理这五件事，其余按级别排期。

## 🏆 优先修复清单（Top 5）

1. **H1 + H2：统一锁文件"内容不可解析"的处理策略**——不可解析 + 短年龄阈值即 stale，unlink 前重读校验；修复 task_run_lock 空文件竞态与账号锁 2 小时卡死（`core/tasks.py`）。
2. **H4：单目标重试改走子进程**，并让 `stop_running_task` 在 taskkill 失败时保留锁、删除前重读锁内容比对（`webui/app.py:878-930`、`webui/ops.py:112-133`）。
3. **H3：WebSocket 端点补 Host + Origin 校验**（`webui/app.py:1374-1428`），闭合 DNS rebinding 绕过口子。
4. **H6 + M4：登录服务调试面收敛**——`/debug/*` 默认关闭或独立 token；`goto` 白名单；net_capture 剥离 Cookie；token 文件收紧 ACL（`login_desktop_server.py`、`utils/config.py`）。
5. **H5：协议 SDK 加 sha256 完整性校验 + 沙箱注入替换为纯数据对象**（`core/protocol_sender.mjs`），并顺带补 Node 侧代理支持（M2）。
