(() => {
  const root = document.documentElement;
  const storageKey = "sparkflow-theme";

  const storedTheme = () => {
    try {
      return localStorage.getItem(storageKey);
    } catch {
      return null;
    }
  };

  const applyTheme = (theme) => {
    const value = theme === "light" ? "light" : "dark";
    root.dataset.theme = value;
    root.style.colorScheme = value;
    try {
      localStorage.setItem(storageKey, value);
    } catch {
      // The active page can still switch themes when storage is unavailable.
    }
  };

  applyTheme(storedTheme() || "light");
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      applyTheme(root.dataset.theme === "light" ? "dark" : "light");
    });
  });
})();

(() => {
  const dialog = document.getElementById("confirm-dialog");
  if (!dialog) return;
  const title = document.getElementById("confirm-title");
  const message = document.getElementById("confirm-message");
  const accept = dialog.querySelector("[data-confirm-accept]");
  const cancel = dialog.querySelector("[data-confirm-cancel]");
  let pendingForm = null;
  let pendingLink = "";
  let pendingButton = null;

  const openDialog = (node) => {
    const source = node.closest("[data-confirm]") || node;
    title.textContent = source.dataset.confirmTitle || "确认操作";
    message.textContent =
      source.dataset.confirm ||
      "该操作会立即影响续火花任务，请确认是否继续。";
    accept.textContent = source.dataset.confirmAccept || "确认执行";
    accept.className =
      source.dataset.confirmTone === "primary"
        ? "button button-primary"
        : "button button-danger";
    dialog.showModal();
  };

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      pendingForm = form;
      pendingLink = "";
      pendingButton = null;
      openDialog(form);
    });
  });

  document.querySelectorAll("a[data-confirm]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      pendingForm = null;
      pendingLink = link.href;
      pendingButton = null;
      openDialog(link);
    });
  });

  document.querySelectorAll("button[data-confirm]").forEach((button) => {
    button.addEventListener(
      "click",
      (event) => {
        if (button.dataset.confirmApproved === "1") {
          delete button.dataset.confirmApproved;
          return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        pendingForm = null;
        pendingLink = "";
        pendingButton = button;
        openDialog(button);
      },
      true,
    );
  });

  cancel.addEventListener("click", () => {
    pendingForm = null;
    pendingLink = "";
    pendingButton = null;
    dialog.close();
  });

  accept.addEventListener("click", () => {
    const form = pendingForm;
    const href = pendingLink;
    const button = pendingButton;
    pendingForm = null;
    pendingLink = "";
    pendingButton = null;
    dialog.close();
    if (form) {
      HTMLFormElement.prototype.submit.call(form);
    } else if (href) {
      window.location.assign(href);
    } else if (button) {
      button.dataset.confirmApproved = "1";
      button.click();
    }
  });

  dialog.addEventListener("cancel", () => {
    pendingForm = null;
    pendingLink = "";
    pendingButton = null;
  });
})();

(() => {
  document.querySelectorAll("[data-segment-group]").forEach((group) => {
    const buttons = [...group.querySelectorAll("[data-segment-target]")];
    const owner = group.closest("[data-segment-owner]") || document;
    const panels = [...owner.querySelectorAll("[data-segment-panel]")];
    const activate = (name) => {
      buttons.forEach((button) => {
        const active = button.dataset.segmentTarget === name;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
      });
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.segmentPanel !== name;
      });
    };
    buttons.forEach((button) => {
      button.addEventListener("click", () =>
        activate(button.dataset.segmentTarget),
      );
    });
    const initial =
      buttons.find((button) => button.classList.contains("active")) ||
      buttons[0];
    if (initial) activate(initial.dataset.segmentTarget);
  });
})();

(() => {
  const overviewRoots = document.querySelectorAll("[data-overview-root]");
  if (!overviewRoots.length) return;
  let previousRunning = null;
  let timer = null;

  const formatTime = (raw) => {
    if (!raw) return "-";
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return raw;
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(parsed);
  };

  const setText = (selector, value) => {
    document.querySelectorAll(selector).forEach((node) => {
      node.textContent = String(value ?? "");
    });
  };

  // 任务横幅秒数：后端轮询只校准基准，前端每秒自增（实时显示，不等 10s 轮询）。
  // 状态放模块级变量，interval 每 tick 读取最新值（避免闭包持有旧基准导致校准失效）。
  let taskBannerTimer = null;
  let taskBannerBaseSeconds = 0;
  let taskBannerBaseTime = 0;
  let taskBannerSuspicious = false;
  let taskBannerPid = "";

  const formatRunSeconds = (seconds) => {
    const s = Math.max(0, Math.floor(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${h} 小时 ${m} 分`;
    if (m > 0) return `${m} 分 ${sec} 秒`;
    return `${sec} 秒`;
  };

  const renderTaskBanner = () => {
    const seconds = taskBannerBaseSeconds + Math.floor((Date.now() - taskBannerBaseTime) / 1000);
    document.querySelectorAll("[data-task-banner]").forEach((banner) => {
      banner.hidden = false;
      if (taskBannerSuspicious) {
        // 假活锁告警：pid 存活但锁龄超上限（任务崩溃后 PID 被复用 / 子进程挂起）
        banner.className = "status-banner danger";
        banner.querySelector("[data-task-text]").textContent =
          `发送任务疑似假死：已运行超过 6 小时（pid ${taskBannerPid || "?"}）。请确认是否正常，必要时点击"停止任务"`;
      } else {
        banner.className = "status-banner warning";
        banner.querySelector("[data-task-text]").textContent =
          `发送任务运行中，已运行约 ${formatRunSeconds(seconds)}`;
      }
    });
  };

  const updateTaskBanner = (task) => {
    if (!task.running) {
      // 任务结束：停止计时器并隐藏横幅
      if (taskBannerTimer) {
        window.clearInterval(taskBannerTimer);
        taskBannerTimer = null;
      }
      document.querySelectorAll("[data-task-banner]").forEach((banner) => {
        banner.hidden = true;
      });
      return;
    }
    // 用后端 ageSeconds 校准基准，之后前端每秒自增
    taskBannerBaseSeconds = task.ageSeconds || 0;
    taskBannerBaseTime = Date.now();
    taskBannerSuspicious = Boolean(task.suspicious);
    taskBannerPid = task.pid || "";
    renderTaskBanner();
    if (!taskBannerTimer) {
      taskBannerTimer = window.setInterval(renderTaskBanner, 1000);
    }
  };

  const updateAccounts = (accounts) => {
    accounts.forEach((account) => {
      const selector = `[data-account-overview="${CSS.escape(account.uniqueId)}"]`;
      document.querySelectorAll(selector).forEach((row) => {
        row.dataset.accountState = account.state;
        row.querySelectorAll("[data-account-confirmed]").forEach((node) => {
          node.textContent = account.confirmed;
        });
        row.querySelectorAll("[data-account-attention]").forEach((node) => {
          node.textContent = account.attention;
        });
        row.querySelectorAll("[data-account-pending]").forEach((node) => {
          node.textContent = account.pending;
        });
        row.querySelectorAll("[data-account-progress]").forEach((node) => {
          const pct = account.total
            ? Math.round((account.confirmed / account.total) * 100)
            : 0;
          node.style.width = `${pct}%`;
        });
        row.querySelectorAll("[data-account-progress-text]").forEach((node) => {
          node.textContent = `${account.confirmed}/${account.total}`;
        });
      });
    });
  };

  const updateActions = (summary, running) => {
    const counts = {
      attention: summary.attention,
      pending: summary.pending + summary.unprocessed,
      total: summary.total,
    };
    document.querySelectorAll("[data-action-count-source]").forEach((button) => {
      const count = counts[button.dataset.actionCountSource] || 0;
      button.disabled = running || count <= 0;
      const countNode = button.querySelector("[data-action-count]");
      if (countNode) countNode.textContent = count;
    });
    document.querySelectorAll("[data-disable-while-running]").forEach((button) => {
      if (!button.dataset.actionCountSource) {
        button.disabled = running;
      }
    });
  };

  const render = (data) => {
    const summary = data.summary || {};
    const task = data.task || {};
    updateTaskBanner(task);
    setText("[data-overview-value='total']", summary.total || 0);
    setText("[data-overview-value='confirmed']", summary.confirmed || 0);
    setText("[data-overview-value='attention']", summary.attention || 0);
    setText(
      "[data-overview-value='pending']",
      (summary.pending || 0) + (summary.unprocessed || 0),
    );
    setText("[data-overview-value='remaining']", summary.remaining || 0);
    setText(
      "[data-overview-value='progress']",
      `${summary.confirmed || 0}/${summary.total || 0}`,
    );
    setText(
      "[data-overview-value='progressPercent']",
      summary.total
        ? `${Math.round((summary.confirmed / summary.total) * 100)}%`
        : "0%",
    );
    setText(
      "[data-overview-value='lastConfirmedAt']",
      formatTime(summary.lastConfirmedAt),
    );
    setText(
      "[data-overview-value='nextTriggerAt']",
      formatTime(data.schedule?.nextTriggerAt),
    );
    setText(
      "[data-overview-value='scheduleLabel']",
      data.schedule?.label || "-",
    );
    updateAccounts(data.accounts || []);
    updateActions(summary, Boolean(task.running));

    if (previousRunning === true && !task.running) {
      document
        .querySelectorAll("[data-refresh-notice]")
        .forEach((node) => node.classList.add("visible"));
      // 任务刚结束：目标级列表是服务端渲染的，短暂提示后自动刷新页面展示最新结果。
      // 用户正在输入时跳过自动刷新（此时可手动点“刷新详情”）。
      const active = document.activeElement;
      const typing =
        active &&
        ["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName) &&
        String(active.value || "").length > 0;
      if (!typing) {
        window.setTimeout(() => {
          if (!document.hidden) window.location.reload();
        }, 2500);
      }
    }
    previousRunning = Boolean(task.running);
    document.querySelectorAll("[data-overview-live-state]").forEach((node) => {
      node.textContent = "实时";
      node.classList.remove("poll-stale");
    });
  };

  const refresh = async () => {
    if (document.visibilityState !== "visible") return;
    try {
      const response = await fetch("/api/ops/overview", {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch {
      document.querySelectorAll("[data-overview-live-state]").forEach((node) => {
        node.textContent = "更新延迟";
        node.classList.add("poll-stale");
      });
    }
  };

  document.querySelectorAll("[data-refresh-page]").forEach((button) => {
    button.addEventListener("click", () => window.location.reload());
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refresh();
  });
  refresh();
  timer = window.setInterval(refresh, 10000);
  window.addEventListener("pagehide", () => {
    window.clearInterval(timer);
    // taskBannerTimer 与 overview timer 同 IIFE，一并清理（bfcache 恢复时
    // interval 若残留会在隐藏页面继续每秒自增）
    window.clearInterval(taskBannerTimer);
  });
})();

(() => {
  const root = document.getElementById("login-desktop-controls");
  if (!root) return;
  const section = document.getElementById("interactive-login-section");
  const csrfToken = root.dataset.csrfToken || "";
  const displayMode = root.dataset.displayMode || "novnc";
  const configuredPublicUrl = root.dataset.publicUrl || "";
  const publicUrl = (() => {
    if (!configuredPublicUrl) return "";
    try {
      return new URL(configuredPublicUrl, window.location.href).href;
    } catch {
      return configuredPublicUrl;
    }
  })();
  const runtimeState = document.getElementById("login-desktop-runtime-state");
  const statusText = document.getElementById("login-desktop-status-text");
  const frame = document.querySelector("[data-login-frame]");
  const frameWrap = document.querySelector(".desktop-frame-wrap");
  const nativePanel = document.querySelector("[data-native-login]");
  const copyLoginUrlButton = document.querySelector("[data-copy-login-url]");
  const qrImage = document.querySelector("[data-login-qr]");
  const qrStatus = document.querySelector("[data-login-qr-status]");
  let timer = null;
  let heartbeatTimer = null;
  let qrRefreshTimer = null;
  let workspace = { state: "closed", active: false, position: 0, ticket: "" };
  if (displayMode === "native" && copyLoginUrlButton) copyLoginUrlButton.hidden = true;
  if (displayMode === "native" && frameWrap) frameWrap.hidden = true;

  const setStatus = (text, tone = "") => {
    if (statusText) statusText.textContent = text;
    if (runtimeState) {
      runtimeState.className = `pill${tone ? ` ${tone}` : ""}`;
      runtimeState.textContent = tone === "success" ? "使用中" : tone === "danger" ? "异常" : tone === "warning" ? "排队中" : "已关闭";
    }
  };

  const postForm = async (url, payload = {}) => {
    const formData = new FormData();
    formData.set("csrf_token", csrfToken);
    Object.entries(payload).forEach(([key, value]) => formData.set(key, String(value ?? "")));
    const response = await fetch(url, { method: "POST", body: formData, credentials: "same-origin" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.error || `请求失败：${response.status}`);
    return data;
  };

  const loadFrame = (force = false) => {
    if (displayMode === "native") return;
    if (frame && (force || frame.dataset.loaded !== "1") && frame.dataset.src) {
      frame.src = frame.dataset.src;
      frame.dataset.loaded = "1";
    }
  };

  const closeFrame = () => {
    if (nativePanel) nativePanel.hidden = true;
    if (frameWrap) {
      frameWrap.classList.remove("native-login-mode");
      if (displayMode === "native") frameWrap.hidden = true;
    }
    if (qrImage) {
      qrImage.hidden = true;
      const previous = qrImage.dataset.objectUrl || "";
      if (previous) URL.revokeObjectURL(previous);
      delete qrImage.dataset.objectUrl;
      qrImage.removeAttribute("src");
    }
    if (!frame) return;
    frame.removeAttribute("src");
    frame.dataset.loaded = "0";
  };

  const renderWorkspace = (next) => {
    workspace = next || { state: "closed", active: false, position: 0, ticket: "" };
    if (workspace.state === "queued") {
      setStatus(`登录工作区排队中，前面还有 ${Math.max(0, Number(workspace.position || 1) - 1)} 人。`, "warning");
      if (qrStatus) qrStatus.textContent = "排队成功，轮到你后会自动打开登录二维码。";
      return;
    }
    if (workspace.state === "resetting") {
      setStatus("正在清理上一位用户的登录环境，请稍候。", "warning");
      return;
    }
    if (workspace.state === "active" && workspace.active) {
      if (displayMode === "native") {
        if (nativePanel) nativePanel.hidden = false;
        if (frameWrap) {
          frameWrap.hidden = false;
          frameWrap.classList.add("native-login-mode");
        }
        // 工作区由心跳自动续期，不显示倒计时（避免续期导致的数字跳动）
        setStatus("登录浏览器已在后台运行，扫码完成后请保存登录态。", "success");
      } else {
        setStatus("登录工作区已分配，扫码完成后请保存登录态。", "success");
      }
      return;
    }
    setStatus("登录工作区当前关闭。请从账号卡片点击“重新登录”。");
  };

  const refreshLoginQr = async (delay = 0, retries = 40) => {
    if (!qrImage || workspace.state !== "active" || !workspace.active) return;
    window.clearTimeout(qrRefreshTimer);
    qrRefreshTimer = window.setTimeout(async () => {
      if (qrStatus) qrStatus.textContent = "正在读取登录二维码...";
      // 10 秒超时：Edge 冷启动/二维码生成期间 fetch 可能长时间挂起，
      // 超时按 202 处理继续重试，避免卡在"正在读取"
      const controller = new AbortController();
      const abortTimer = window.setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(`/login-desktop/qr?t=${Date.now()}`, {
          credentials: "same-origin",
          cache: "no-store",
          signal: controller.signal,
        });
        if (response.status === 202) {
          if (retries > 1 && workspace.state === "active") {
            if (qrStatus) qrStatus.textContent = "浏览器正在生成二维码，继续等待...";
            refreshLoginQr(1400, retries - 1);
          }
          return;
        }
        if (!response.ok) throw new Error(String(response.status));
        const blob = await response.blob();
        const previous = qrImage.dataset.objectUrl || "";
        const objectUrl = URL.createObjectURL(blob);
        qrImage.src = objectUrl;
        qrImage.dataset.objectUrl = objectUrl;
        qrImage.hidden = false;
        if (previous) URL.revokeObjectURL(previous);
        if (qrStatus) qrStatus.textContent = "二维码已加载。如果过期，点击刷新。";
      } catch {
        if (retries > 1 && workspace.state === "active") {
          if (qrStatus) qrStatus.textContent = "登录页正在加载，继续等待二维码...";
          refreshLoginQr(1400, retries - 1);
        } else if (qrStatus) {
          qrStatus.textContent = "二维码还未准备好，请确认自己已经获得登录工作区。";
        }
      } finally {
        window.clearTimeout(abortTimer);
      }
    }, delay);
  };

  const pollStatus = async () => {
    if (document.visibilityState !== "visible") return;
    try {
      const statusUrl = workspace.state === "active" ? "/login-desktop/status" : "/login-desktop/workspace-status";
      const response = await fetch(statusUrl, { credentials: "same-origin", cache: "no-store" });
      const data = await response.json();
      if (!response.ok || data.ok === false) {
        setStatus(data.error || "登录工作区不可用，请检查 login-desktop 服务。", "danger");
        return;
      }
      renderWorkspace(data.workspace);
      if (workspace.state === "active" && workspace.active) {
        loadFrame();
        if (data.logged_in) {
          setStatus(`当前浏览器已登录：${data.username}，请保存登录态。`, "success");
          // 重新登录已有账号：登录成功后自动保存登录态
          if (pendingReloginUid) {
            const uid = pendingReloginUid;
            pendingReloginUid = "";
            try {
              await postForm("/login-desktop/save", { relogin_unique_id: uid });
              const loginDialog = document.getElementById("login-dialog");
              if (loginDialog && loginDialog.open) loginDialog.close();
              window.setTimeout(() => window.location.reload(), 800);
            } catch (error) {
              setStatus(`保存登录态失败：${error.message}`, "danger");
            }
          }
        }
      } else {
        closeFrame();
      }
    } catch (error) {
      setStatus(`状态检查失败：${error.message}`, "danger");
    }
  };

  const heartbeat = async () => {
    if (workspace.state !== "active" || !workspace.active || !workspace.ticket) return;
    try {
      const data = await postForm("/login-desktop/heartbeat", { ticket: workspace.ticket });
      if (data.ok === false) {
        workspace = { state: "closed", active: false, position: 0, ticket: "" };
        closeFrame();
        setStatus(`登录工作区已释放：${data.error || "未知错误"}`, "danger");
      }
      // 心跳成功只续期，不覆盖 UI 状态（pollStatus 负责状态展示，
      // 否则会冲掉"当前浏览器已登录"提示）
    } catch (error) {
      workspace = { state: "closed", active: false, position: 0, ticket: "" };
      closeFrame();
      setStatus(`登录工作区已释放：${error.message}`, "danger");
    }
  };

  // 当前弹窗是否处于"重新登录已有账号"模式；登录成功后自动保存，无需手动点保存
  let pendingReloginUid = "";

  document.querySelectorAll(".login-desktop-open").forEach((button) => {
    button.addEventListener("click", async () => {
      // 登录流程在居中弹窗中完成
      const loginDialog = document.getElementById("login-dialog");
      if (loginDialog && !loginDialog.open) loginDialog.showModal();
      try {
        const reloginUniqueId = button.dataset.reloginUniqueId || "";
        pendingReloginUid = reloginUniqueId;
        const mode = button.dataset.loginMode || (reloginUniqueId ? "relogin" : "add");
        const data = await postForm("/login-desktop/open", {
          mode,
          ...(reloginUniqueId ? { relogin_unique_id: reloginUniqueId } : {}),
        });
        renderWorkspace(data.workspace);
        if (data.state === "queued") return;
        loadFrame(true);
        refreshLoginQr(500);
      } catch (error) {
        setStatus(`申请登录工作区失败：${error.message}`, "danger");
      }
    });
  });

  document.querySelectorAll("[data-focus-native-browser]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await postForm("/login-desktop/focus", { ticket: workspace.ticket });
        setStatus("已请求显示本机登录浏览器，请在桌面窗口中继续操作。", "success");
      } catch (error) {
        setStatus(`显示登录浏览器失败：${error.message}`, "danger");
      } finally {
        button.disabled = false;
      }
    });
  });

  document.querySelectorAll("[data-refresh-login-qr]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        // 先清掉旧二维码，避免误扫旧码；新码就绪后再显示
        if (qrImage) {
          const previous = qrImage.dataset.objectUrl || "";
          qrImage.hidden = true;
          qrImage.removeAttribute("src");
          if (previous) URL.revokeObjectURL(previous);
          delete qrImage.dataset.objectUrl;
        }
        if (qrStatus) qrStatus.textContent = "正在刷新二维码...";
        // 刷新请求返回时新二维码已就绪（后端等待渲染完成），再拉取图片
        await postForm("/login-desktop/qr/refresh", { ticket: workspace.ticket });
        refreshLoginQr(500);
      } catch (error) {
        if (qrStatus) qrStatus.textContent = `刷新二维码失败：${error.message}`;
      } finally {
        button.disabled = false;
      }
    });
  });

  document.querySelectorAll(".login-desktop-save").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const data = await postForm("/login-desktop/save", { relogin_unique_id: button.dataset.reloginUniqueId || "" });
        renderWorkspace(data.workspace);
        setStatus(`已保存登录账号：${data.account?.username || ""}`, "success");
        closeFrame();
        window.setTimeout(() => window.location.reload(), 800);
      } catch (error) {
        setStatus(`保存登录账号失败：${error.message}`, "danger");
      }
    });
  });

  document.querySelectorAll(".login-desktop-close").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const data = await postForm("/login-desktop/close");
        renderWorkspace(data.workspace);
        closeFrame();
        if (qrImage) qrImage.hidden = true;
        // 弹窗保留，状态显示"已关闭"，可点"重新打开登录"再次发起
      } catch (error) {
        setStatus(`关闭登录界面失败：${error.message}`, "danger");
      }
    });
  });

  document.querySelectorAll(".login-desktop-reset").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const data = await postForm("/login-desktop/reset");
        renderWorkspace(data.workspace);
        if (data.state === "queued") return;
        closeFrame();
        if (qrImage) qrImage.hidden = true;
        // 重新加载二维码
        loadFrame(true);
        refreshLoginQr(500);
      } catch (error) {
        setStatus(`重置工作区失败：${error.message}`, "danger");
      }
    });
  });

  document.querySelectorAll("[data-copy-login-url]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(publicUrl);
        setStatus("登录工作区地址已复制。", "success");
      } catch (error) {
        setStatus(`复制失败：${error.message}`, "danger");
      }
    });
  });

  if (section) section.addEventListener("toggle", () => { if (section.open) pollStatus(); });
  pollStatus();
  timer = window.setInterval(pollStatus, 5000);
  heartbeatTimer = window.setInterval(heartbeat, 5000);
  // 工作区由心跳自动续期，无需本地倒计时
  window.addEventListener("pagehide", () => {
    window.clearInterval(timer);
    window.clearInterval(heartbeatTimer);
    // qrRefreshTimer 是 setTimeout，pagehide 时一并清理，避免页面切换后残留重试
    window.clearTimeout(qrRefreshTimer);
  });
})();

(() => {
  const parseJson = (id) => {
    const node = document.getElementById(id);
    if (!node) return [];
    try {
      return JSON.parse(node.textContent || "[]");
    } catch {
      return [];
    }
  };

  document.querySelectorAll(".friend-picker").forEach((picker) => {
    const accountId = picker.dataset.accountId;
    const refreshUrl = picker.dataset.refreshUrl;
    const csrfToken = picker.dataset.csrfToken;
    const form = picker.closest("form");
    const textarea = form?.querySelector(".targets-textarea");
    const search = picker.querySelector(".friend-search-input");
    const refreshButton = picker.querySelector(".friend-refresh-button");
    const list = picker.querySelector(".friend-picker-list");
    const summary = picker.querySelector(".friend-picker-summary");
    const status = picker.querySelector(".friend-picker-status");
    let friends = parseJson(`friends-cache-${accountId}`);
    let selected = new Set(parseJson(`selected-targets-${accountId}`));

    const parseTargets = (value) =>
      [...new Set(
        String(value || "")
          .replaceAll(",", "\n")
          .split(/\r?\n/)
          .map((item) => item.trim())
          .filter(Boolean),
      )];

    const combined = () => [...new Set([...selected, ...friends])];

    const syncTextarea = () => {
      if (textarea) textarea.value = [...selected].join("\n");
    };

    const render = () => {
      const query = String(search?.value || "").trim().toLowerCase();
      const names = combined().filter((name) =>
        name.toLowerCase().includes(query),
      );
      if (summary) summary.textContent = `已选 ${selected.size} 人`;
      list.innerHTML = "";
      if (!names.length) {
        const empty = document.createElement("div");
        empty.className = "friend-picker-empty";
        empty.textContent = combined().length
          ? "没有匹配的好友。"
          : "点击“刷新好友列表”后再选择目标。";
        list.appendChild(empty);
        return;
      }
      names.forEach((name) => {
        const label = document.createElement("label");
        label.className = `friend-option${selected.has(name) ? " selected" : ""}`;
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = selected.has(name);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selected.add(name);
          else selected.delete(name);
          syncTextarea();
          render();
        });
        const text = document.createElement("span");
        text.textContent = name;
        label.append(checkbox, text);
        list.appendChild(label);
      });
    };

    textarea?.addEventListener("input", () => {
      selected = new Set(parseTargets(textarea.value));
      render();
    });
    search?.addEventListener("input", render);
    refreshButton?.addEventListener("click", async () => {
      refreshButton.disabled = true;
      if (status) status.textContent = "正在读取好友列表...";
      try {
        const formData = new FormData();
        formData.set("csrf_token", csrfToken);
        const response = await fetch(refreshUrl, {
          method: "POST",
          body: formData,
          credentials: "same-origin",
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "刷新失败");
        friends = data.friends || [];
        if (status) status.textContent = data.message || "好友列表已刷新";
        render();
      } catch (error) {
        if (status) status.textContent = `刷新失败：${error.message}`;
      } finally {
        refreshButton.disabled = false;
      }
    });
    render();
  });
})();

(() => {
  const scroll = document.getElementById("tags-view-scroll");
  if (!scroll) return;
  const HOME_PATH = "/";
  const storageKey = "sparkflow-tags";

  const readTags = () => {
    try {
      const parsed = JSON.parse(sessionStorage.getItem(storageKey) || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  };

  const writeTags = (tags) => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(tags));
    } catch {
      // 忽略
    }
  };

  // 安全校验：tag.path 来自 sessionStorage，仅允许同源站内路径——
  // startsWith("/") 会被 "//evil.com"（协议相对）或 "/\evil.com"（反斜杠当正斜杠）绕过
  const isSafeTagPath = (value) => {
    try {
      const url = new URL(String(value || ""), window.location.origin);
      return url.origin === window.location.origin;
    } catch {
      return false;
    }
  };

  const render = () => {
    const tags = readTags();
    const currentPath = window.location.pathname;
    scroll.innerHTML = "";
    tags.forEach((tag) => {
      const tagPath = isSafeTagPath(tag.path) ? tag.path : HOME_PATH;
      const chip = document.createElement("span");
      chip.className = `tag-chip${tagPath === currentPath ? " active" : ""}`;
      const label = document.createElement("span");
      label.textContent = tag.title;
      chip.append(label);
      if (tagPath !== HOME_PATH) {
        const close = document.createElement("span");
        close.className = "tag-close";
        close.setAttribute("role", "button");
        close.setAttribute("aria-label", `关闭 ${tag.title}`);
        close.textContent = "✕";
        close.addEventListener("click", (event) => {
          event.stopPropagation();
          const remaining = readTags().filter((item) => isSafeTagPath(item.path) && item.path !== tagPath);
          writeTags(remaining);
          render();
          if (tagPath === currentPath) {
            const fallback = remaining[remaining.length - 1] || { path: HOME_PATH };
            window.location.href = isSafeTagPath(fallback.path) ? fallback.path : HOME_PATH;
          }
        });
        chip.append(close);
      }
      chip.addEventListener("click", () => {
        if (tagPath !== currentPath) window.location.href = tagPath;
      });
      scroll.append(chip);
    });
  };

  const registerCurrent = () => {
    const path = window.location.pathname;
    const anchor = document.querySelector(
      `.nav-item[data-tag-path="${CSS.escape(path)}"]`,
    );
    const title = anchor ? anchor.dataset.tagTitle : document.title;
    const tags = readTags();
    if (!tags.some((tag) => tag.path === path)) {
      tags.push({ path, title: title || path });
      writeTags(tags);
    }
    render();
  };

  registerCurrent();
  window.addEventListener("pageshow", render);
})();

window.addEventListener("DOMContentLoaded", () => {
  if (window.lucide) {
    window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
  }
});
