const state = {
  busy: false,
  drafts: [],
  managementProposals: [],
  editing: null,
  transactions: [],
  billSort: { key: "date", direction: "desc" },
  activeView: "chat",
  logKind: "operations",
  controller: null,
  cancelReason: "",
  currentProvider: "",
  currentModel: "",
  providers: [],
  references: { category: [], payment_method: [] },
  subscriptions: [],
  liabilities: [],
  expandedLiabilityCharges: new Set(),
  liabilityAccounts: [],
  accounts: [],
  capital: null,
  sessionId: "web",
  currentRequestId: "",
  chatQueue: [],
  draftRequestId: "",
  pendingPollTimer: null,
  progressSource: null,
  selectedImages: [],
  speechToText: { configured: false },
  recording: false,
  transcribingAudio: false,
  audioRecorder: null,
  audioStream: null,
};

const $ = (id) => document.getElementById(id);
const viewMeta = {
  chat: ["财务助理", "自然语言记账与查询"],
  bills: ["资金明细", "查看收入、支出与还款记录"],
  accounts: ["账户与对账", "管理真实余额、账户转账和对账差异"],
  budgets: ["预算规划", "设置每月分类预算并跟踪使用情况"],
  subscriptions: ["订阅", "管理预计的周期扣款，并在实际扣款后写入账本"],
  liabilities: ["待还", "按月管理信用卡、花呗、月付和分期账单"],
  logs: ["日志", "操作记录与 Agent 调用状态"],
  memory: ["偏好记忆", "管理 Agent 可使用的个人长期规则"],
  settings: ["账本设置", "分类、支付方式与本地备份"],
};

function money(value) {
  return `¥${Number(value || 0).toFixed(2)}`;
}

function nextStatementMonth(transactionDate) {
  const match = String(transactionDate || "").match(/^(\d{4})-(\d{2})-\d{2}$/);
  if (!match) return $("month").value;
  const year = Number(match[1]);
  const month = Number(match[2]);
  return month === 12 ? `${year + 1}-01` : `${year}-${String(month + 1).padStart(2, "0")}`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    "\"": "&quot;",
  })[char]);
}

async function request(url, options = {}) {
  const { retryNetwork = false, ...fetchOptions } = options;
  let response;
  for (let attempt = 0; attempt < (retryNetwork ? 2 : 1); attempt += 1) {
    try {
      response = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        ...fetchOptions,
      });
      break;
    } catch (error) {
      if (!retryNetwork || attempt > 0 || error.name === "AbortError") throw error;
      await new Promise((resolve) => setTimeout(resolve, 350));
    }
  }
  if (!response) throw new Error("无法连接本地服务");
  let data;
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok) {
    const detail = data.detail;
    const error = new Error(typeof detail === "string" ? detail : detail?.message || `请求失败 (${response.status})`);
    error.status = response.status;
    error.data = detail;
    throw error;
  }
  return data;
}

async function requestAudio(url, audio, filename) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": audio.type || "audio/webm",
      "X-Ledger-Audio-Name": filename,
    },
    body: audio,
  });
  let data;
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : `语音转写失败 (${response.status})`);
  }
  return data;
}

let toastTimer;
function toast(text) {
  const node = $("toast");
  node.textContent = text;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 2400);
}

function scrollMessagesToLatest(behavior = "smooth") {
  const messages = $("messages");
  requestAnimationFrame(() => {
    messages.scrollTo({ top: messages.scrollHeight, behavior });
  });
}

const INSIGHTS_WIDTH_KEY = "ledger-agent.insights-width";

function insightsWidthBounds() {
  const navigationWidth = window.innerWidth <= 1100 ? 76 : 214;
  const workspaceMinimum = window.innerWidth <= 1100 ? 440 : 520;
  const available = Math.max(240, window.innerWidth - navigationWidth - workspaceMinimum - 7);
  return { min: Math.min(300, available), max: Math.max(Math.min(300, available), Math.min(720, available)) };
}

function setInsightsWidth(value, persist = false) {
  if (window.innerWidth <= 780) return;
  const bounds = insightsWidthBounds();
  const width = Math.round(Math.min(bounds.max, Math.max(bounds.min, Number(value) || 360)));
  document.querySelector(".app-shell").style.setProperty("--insights-width", `${width}px`);
  $("insights-resizer").setAttribute("aria-valuenow", String(width));
  if (persist) {
    try { localStorage.setItem(INSIGHTS_WIDTH_KEY, String(width)); } catch {}
  }
}

function initializeInsightsResizer() {
  const handle = $("insights-resizer");
  let storedWidth = 360;
  try { storedWidth = Number(localStorage.getItem(INSIGHTS_WIDTH_KEY)) || 360; } catch {}
  setInsightsWidth(storedWidth);
  let startX = 0;
  let startWidth = 0;
  let resizing = false;
  handle.addEventListener("pointerdown", (event) => {
    if (window.innerWidth <= 780) return;
    resizing = true;
    startX = event.clientX;
    startWidth = document.querySelector(".insights").getBoundingClientRect().width;
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add("resizing-insights");
    event.preventDefault();
  });
  handle.addEventListener("pointermove", (event) => {
    if (resizing) setInsightsWidth(startWidth + startX - event.clientX);
  });
  const finishResize = (event) => {
    if (!resizing) return;
    resizing = false;
    document.body.classList.remove("resizing-insights");
    setInsightsWidth(document.querySelector(".insights").getBoundingClientRect().width, true);
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
  };
  handle.addEventListener("pointerup", finishResize);
  handle.addEventListener("pointercancel", finishResize);
  handle.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const current = document.querySelector(".insights").getBoundingClientRect().width;
    setInsightsWidth(current + (event.key === "ArrowLeft" ? 24 : -24), true);
    event.preventDefault();
  });
  window.addEventListener("resize", () => setInsightsWidth(document.querySelector(".insights").getBoundingClientRect().width));
}

function addMessage(text, role, isError = false, autoScroll = true, attachments = []) {
  $("empty-chat")?.remove();
  const node = document.createElement("div");
  node.className = `message ${role}${isError ? " error" : ""}`;
  const textNode = document.createElement("div");
  textNode.className = "message-text";
  textNode.textContent = text;
  node.appendChild(textNode);
  if (attachments.length) {
    const gallery = document.createElement("div");
    gallery.className = "message-attachments";
    for (const [index, attachment] of attachments.entries()) {
      const link = document.createElement("a");
      link.className = "message-attachment";
      link.href = attachment.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.ariaLabel = `查看第 ${index + 1} 张账单截图`;
      const image = document.createElement("img");
      image.src = attachment.url;
      image.alt = attachment.name || `账单截图 ${index + 1}`;
      link.appendChild(image);
      gallery.appendChild(link);
    }
    node.appendChild(gallery);
  }
  const queuedMessage = role === "agent" ? $("messages").querySelector(".message.user.queued") : null;
  if (queuedMessage) {
    $("messages").insertBefore(node, queuedMessage);
  } else {
    $("messages").appendChild(node);
  }
  if (autoScroll) scrollMessagesToLatest();
  return node;
}

function addPendingMessage(startedAt = Date.now(), requestId = "") {
  $("empty-chat")?.remove();
  const node = document.createElement("div");
  node.className = "message agent pending";
  if (requestId) node.dataset.requestId = requestId;
  const model = escapeHtml(state.currentModel || "LLM");
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  node.innerHTML = `<span class="spinner" aria-hidden="true"></span><span>${model} 正在分析 · <b>${seconds}</b> 秒</span>`;
  $("messages").appendChild(node);
  scrollMessagesToLatest();
  return node;
}

function setPendingProgress(node, message) {
  const label = node?.querySelector("span:last-child");
  if (label && message) label.innerHTML = `${escapeHtml(message)} · <b>0</b> 秒`;
}

function watchChatProgress(requestId, pending) {
  state.progressSource?.close();
  const source = new EventSource(`/api/chat/requests/${encodeURIComponent(requestId)}/events`);
  state.progressSource = source;
  source.addEventListener("progress", (event) => {
    try {
      const data = JSON.parse(event.data);
      setPendingProgress(pending, data.message || "正在处理");
    } catch {}
  });
  source.addEventListener("done", () => source.close());
  source.onerror = () => source.close();
}

function editableValue(value) {
  return value === "未指定" ? "" : (value ?? "");
}

function statementDayOptions(selected = 0) {
  const current = Number(selected || 0);
  const options = [`<option value="">未设置</option>`];
  for (let day = 1; day <= 31; day += 1) {
    options.push(`<option value="${day}" ${day === current ? "selected" : ""}>每月 ${day} 日</option>`);
  }
  return options.join("");
}

function initializeStatementDaySelectors() {
  ["liability-statement-day", "liability-edit-statement-day"].forEach((id) => {
    $(id).innerHTML = statementDayOptions();
  });
}

function resultText(data) {
  if (data.native_answer) return data.native_answer;
  const action = data.agent_action?.action;
  if (action === "summary") {
    const summary = data.summary;
    return `${summary.month}：收入 ${money(summary.income)}，支出 ${money(summary.expense)}，结余 ${money(summary.net)}。`;
  }
  if (action === "where") {
    const items = data.where?.items || [];
    return items.length
      ? `支出共 ${money(data.where.total_expense)}\n${items.map((item) => `${item.name || "未指定"}：${money(item.total)}`).join("\n")}`
      : "这个范围内还没有支出记录。";
  }
  if (action === "search") {
    const rows = data.results || [];
    return rows.length
      ? rows.map((row) => `${row.date}  ${row.merchant || row.category}  ${money(row.amount)}`).join("\n")
      : "没有找到匹配的账单。";
  }
  if (action === "plan") return (data.advice || []).join("\n");
  if (action === "analyze") return data.narrative || "分析已经完成。";
  if (action === "compare") {
    const value = data.comparison?.comparison;
    return value
      ? `两个时间段支出变化 ${money(value.change)}，笔数变化 ${value.count_change} 笔，均笔金额变化 ${money(value.average_change)}。`
      : "时间段比较已经完成。";
  }
  if (action === "recurring") {
    const items = data.recurring?.candidates || [];
    return items.length
      ? `发现 ${items.length} 个周期性支出候选：\n${items.slice(0, 6).map((item) => `${item.merchant}：${money(item.average_amount)}，${item.monthly_occurrences} 个月出现`).join("\n")}`
      : "这个范围内没有足够稳定的周期性支出候选。";
  }
  if (action === "subscriptions") {
    const summary = data.subscriptions?.summary;
    return summary ? `${data.subscriptions.month} 预计扣款 ${money(summary.scheduled_amount)}，共 ${summary.due_count} 项。` : "订阅清单已经读取。";
  }
  if (action === "liabilities") {
    const summary = data.liabilities?.summary;
    return summary ? `${data.liabilities.month} 本月应还 ${money(summary.due_amount)}，本月未还 ${money(summary.remaining_amount)}。` : "待还清单已经读取。";
  }
  if (action === "reminder") {
    const reminder = data.reminder;
    if (!reminder?.enabled) return "每日记账提醒已关闭。";
    if (reminder.skipped_today) return "今天的记账提醒已跳过，明天会恢复。";
    return `每日记账提醒已设为 ${reminder.time}。`;
  }
  if (action === "memory") {
    return `已记住：${data.memory?.title || "个人偏好"}。你可以在“偏好记忆”中随时编辑或删除。`;
  }
  if (action === "report") return data.narrative || (data.report?.recommendations || []).join("\n");
  return "已经处理完成。";
}

function agentMessageText(data) {
  if (!data || typeof data !== "object") return "已经处理完成。";
  if (data.kind === "drafts" || data.kind === "draft") {
    const drafts = data.drafts || (data.draft ? [data.draft] : []);
    return `我整理成了 ${drafts.length} 条账单草稿，请在右侧检查后确认。`;
  }
  if (data.kind === "management_drafts") {
    return `我整理了 ${Number(data.draft_count || data.proposals?.length || 0)} 条订阅或待还草稿，请在右侧确认。`;
  }
  if (data.kind === "clarification") return data.question || "请补充账单信息。";
  if (data.kind === "error") return data.message || "这次请求处理失败。";
  return resultText(data);
}

function setChatBusy(busy) {
  state.busy = busy;
  $("send").disabled = state.transcribingAudio;
  $("send").title = busy ? "加入等待队列" : "发送";
  $("send").setAttribute("aria-label", busy ? "加入等待队列" : "发送");
  $("receipt-images").disabled = busy;
  $("attach-images").classList.toggle("disabled", busy);
  $("attach-images").setAttribute("aria-disabled", String(busy));
  $("cancel-request").hidden = !busy;
  updateAudioButton();
}

function applyAgentResult(data, showMessage = true) {
  if (showMessage) addMessage(agentMessageText(data), "agent", data.kind === "error");
  if (data.kind === "drafts" || data.kind === "draft") {
    const drafts = data.drafts || (data.draft ? [data.draft] : []);
    state.draftRequestId = data.request_id || "";
    showDrafts(drafts);
  }
  if (data.kind === "management_drafts") {
    state.draftRequestId = data.request_id || "";
    showManagementDrafts(data.proposals || []);
  }
  if (data.agent_action?.action === "reminder") renderReminderSettings(data.reminder || {});
  if (data.agent_action?.action === "memory" && state.activeView === "memory") loadPersonalMemories();
}

function renderPersonalMemories(items) {
  const list = $("personal-memory-list");
  $("personal-memory-empty").hidden = items.length > 0;
  list.innerHTML = items.map((item) => `
    <article class="personal-memory-item ${item.enabled ? "" : "disabled"}" data-personal-memory-id="${escapeHtml(item.id)}">
      <div class="personal-memory-item-heading">
        <div><strong>${escapeHtml(item.title)}</strong><span>${item.source === "agent" ? "由 Agent 按你的明确指令写入" : "手动添加"}</span></div>
        <label class="toggle-field"><input type="checkbox" data-toggle-personal-memory="${escapeHtml(item.id)}" ${item.enabled ? "checked" : ""}><span>${item.enabled ? "启用" : "停用"}</span></label>
      </div>
      <textarea data-personal-memory-content="${escapeHtml(item.id)}" maxlength="500" rows="3" aria-label="${escapeHtml(item.title)} 的规则内容">${escapeHtml(item.content)}</textarea>
      <div class="personal-memory-actions"><button class="button" type="button" data-save-personal-memory="${escapeHtml(item.id)}">保存修改</button><button class="button danger" type="button" data-delete-personal-memory="${escapeHtml(item.id)}">删除</button></div>
    </article>`).join("");
}

async function loadPersonalMemories() {
  try {
    const data = await request("/api/personal-memories");
    renderPersonalMemories(data.items || []);
  } catch (error) { toast(error.message); }
}

async function createPersonalMemory(event) {
  event.preventDefault();
  try {
    await request("/api/personal-memories", {
      method: "POST",
      body: JSON.stringify({
        title: $("personal-memory-title").value.trim(),
        content: $("personal-memory-content").value.trim(),
      }),
    });
    event.target.reset();
    toast("个人偏好记忆已添加");
    await loadPersonalMemories();
  } catch (error) { toast(error.message); }
}

async function updatePersonalMemory(id, changes) {
  try {
    await request(`/api/personal-memories/${encodeURIComponent(id)}`, {
      method: "PATCH", body: JSON.stringify(changes),
    });
    toast("个人偏好记忆已更新");
    await loadPersonalMemories();
  } catch (error) { toast(error.message); }
}

async function deletePersonalMemory(id) {
  if (!window.confirm("删除这条个人偏好记忆？Agent 之后不会再使用它。")) return;
  try {
    await request(`/api/personal-memories/${encodeURIComponent(id)}`, { method: "DELETE" });
    toast("个人偏好记忆已删除");
    await loadPersonalMemories();
  } catch (error) { toast(error.message); }
}

function chatRequestId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return `web_${Date.now()}_${Math.random().toString(36).slice(2, 12)}`;
}

async function pollChatRequest(requestId, pending, startedAt) {
  clearTimeout(state.pendingPollTimer);
  let resumeAttempted = false;
  const elapsed = setInterval(() => {
    const counter = pending.querySelector("b");
    if (counter) counter.textContent = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  }, 1000);
  async function poll() {
    try {
      const item = await request(`/api/chat/requests/${encodeURIComponent(requestId)}`);
      if (item.status === "pending") {
        if (item.can_resume && !resumeAttempted) {
          resumeAttempted = true;
          await request(`/api/chat/requests/${encodeURIComponent(requestId)}/resume`, { method: "POST" });
          state.pendingPollTimer = setTimeout(poll, 100);
          return;
        }
        state.pendingPollTimer = setTimeout(poll, 1600);
        return;
      }
      clearInterval(elapsed);
      state.progressSource?.close();
      pending.remove();
      state.currentRequestId = "";
      if (item.status === "awaiting_confirmation" || item.status === "completed") {
        applyAgentResult(item.result || {});
        await loadDashboard();
      } else if (item.status === "error") {
        addMessage(item.error_message || "这次请求处理失败。", "agent", true);
      }
      setChatBusy(false);
      processNextQueuedChat();
    } catch (error) {
      clearInterval(elapsed);
      pending.remove();
      addMessage(error.message, "agent", true);
      setChatBusy(false);
      processNextQueuedChat();
    }
  }
  state.pendingPollTimer = setTimeout(poll, 500);
}

async function loadChatHistory() {
  try {
    const history = await request(`/api/chat/history?session_id=${encodeURIComponent(state.sessionId)}&limit=100`);
    $("messages").innerHTML = "";
    for (const message of history.messages) {
      if (message.role === "user") {
        const attachments = message.attachments || [];
        const content = message.has_images && !attachments.length
          ? `${message.content}\n旧图片未保存`
          : message.content;
        addMessage(content, "user", false, false, attachments);
      } else if (message.role === "assistant") {
        const data = message.data;
        addMessage(data ? agentMessageText(data) : message.content, "agent", data?.kind === "error", false);
      }
    }
    if (!history.messages.length) {
      $("messages").innerHTML = '<div id="empty-chat" class="empty-state"><strong>今天记点什么？</strong><span>输入一笔消费，或问我本月的钱花在了哪里。</span></div>';
    }

    const awaiting = [...history.active_requests].reverse().find((item) => item.status === "awaiting_confirmation");
    if (awaiting?.result) {
      state.draftRequestId = awaiting.request_id;
      if (awaiting.result.kind === "management_drafts") {
        showManagementDrafts(awaiting.result.proposals || []);
      } else {
        const drafts = awaiting.result.drafts || (awaiting.result.draft ? [awaiting.result.draft] : []);
        showDrafts(drafts);
      }
    }

    const pendingRequest = [...history.active_requests].reverse().find((item) => item.status === "pending");
    if (pendingRequest) {
      state.currentRequestId = pendingRequest.request_id;
      setChatBusy(true);
      const startedAt = Date.parse(pendingRequest.created_at) || Date.now();
      const pending = addPendingMessage(startedAt, pendingRequest.request_id);
      pollChatRequest(pendingRequest.request_id, pending, startedAt);
    }
    scrollMessagesToLatest("auto");
  } catch (error) {
    toast(error.message);
  }
}

async function clearChatHistory() {
  if (state.busy) return toast("当前请求仍在处理中");
  if (!window.confirm("清空当前聊天记录、图片附件和 Agent 会话上下文？账本、欠债和日志不会受影响。")) return;
  try {
    const result = await request(`/api/chat/history?session_id=${encodeURIComponent(state.sessionId)}`, {
      method: "DELETE",
    });
    state.drafts = [];
    state.managementProposals = [];
    state.draftRequestId = "";
    $("draft").hidden = true;
    $("messages").innerHTML = '<div id="empty-chat" class="empty-state"><strong>今天记点什么？</strong><span>输入一笔消费，或问我本月的钱花在了哪里。</span></div>';
    const reclaimed = result.reclaimed_bytes
      ? `，已回收 ${(result.reclaimed_bytes / 1024 / 1024).toFixed(1)} MB`
      : "";
    toast(`已清空 ${result.messages} 条聊天记录和 ${result.attachments} 张图片${reclaimed}`);
    await loadLogs();
  } catch (error) {
    toast(error.message);
  }
}

async function loadHealth() {
  try {
    const health = await request("/api/health");
    state.currentProvider = health.provider;
    state.currentModel = health.model;
    state.providers = health.providers || [];
    state.speechToText = health.speech_to_text || { configured: false };
    $("model-name").textContent = health.model;
    $("provider-name").textContent = health.provider_label;
    $("health-dot").className = `status-dot ${health.api_key_configured ? "ok" : "error"}`;
    if (!health.api_key_configured) $("model-name").textContent = "API key 未配置";
    updateAudioButton();
  } catch {
    $("model-name").textContent = "服务连接失败";
    $("health-dot").className = "status-dot error";
    updateAudioButton();
  }
}

async function loadDashboard() {
  try {
    const data = await request(`/api/dashboard?month=${encodeURIComponent($("month").value)}`);
    $("expense").textContent = money(data.summary.expense);
    $("income").textContent = money(data.summary.income);
    $("month-count").textContent = `${Number(data.summary.count || 0)} 笔`;
    $("liability-paid").textContent = money(data.forecast?.liability_paid || 0);
    $("liability-remaining").textContent = money(data.forecast?.liability_remaining || 0);
    $("cash-change").textContent = money(data.forecast?.cash_change ?? data.summary.income);
    state.capital = data.capital || null;
    $("capital-balance").textContent = data.capital?.configured
      ? money(data.capital.current_balance)
      : "未设置";
    $("current-debt").textContent = money(data.forecast?.current_debt || 0);
    $("bars").innerHTML = data.where.items.length
      ? data.where.items.map((item) => `
        <div>
          <div class="bar-head"><span>${escapeHtml(item.name || "未指定")}</span><strong>${money(item.total)}</strong></div>
          <div class="track"><div class="fill" style="width:${Math.max(2, item.share * 100)}%"></div></div>
        </div>`).join("")
      : '<span class="muted">暂无支出</span>';
    $("recent-transactions").innerHTML = data.recent.length
      ? data.recent.map((tx) => {
        const isRepayment = tx.record_type === "liability_payment";
        const isTransfer = tx.record_type === "transfer";
        const isLiabilityChange = tx.record_type === "liability_statement";
        const isLiabilityCharge = tx.record_type === "liability_charge";
        const detail = [
          tx.date,
          tx.account,
          (isRepayment || isLiabilityChange || isLiabilityCharge) && tx.statement_month ? `归属 ${tx.statement_month} 账单` : "",
        ].filter(Boolean).join(" · ");
        return `
        <div class="recent-item">
          <div class="recent-icon">${escapeHtml(isLiabilityChange ? "债" : (isLiabilityCharge ? "信" : (tx.category || "其").slice(0, 1)))}</div>
          <div class="recent-main"><strong>${escapeHtml(tx.merchant || tx.category)}${isRepayment ? " · 还款" : ""}${isTransfer ? " · 转账" : ""}${isLiabilityCharge ? " · 信用消费" : ""}${isLiabilityChange ? (tx.event_kind === "recovery" ? " · 账单恢复" : " · 待还变动") : ""}</strong><span>${escapeHtml(detail)}</span></div>
          <div class="recent-amount ${escapeHtml(tx.direction)}">${isLiabilityChange ? "负债 " : (isTransfer ? "↔" : (tx.direction === "income" ? "+" : "-"))}${money(tx.amount)}</div>
        </div>`;
      }).join("")
      : '<span class="muted">暂无资金记录</span>';
  } catch (error) {
    toast(error.message);
  }
}

function accountKindLabel(kind) {
  return { wallet: "电子钱包", bank: "银行卡", cash: "现金", other: "其他" }[kind] || "其他";
}

function renderAccountSelectors() {
  const options = state.accounts.map((item) =>
    `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)} · ${money(item.balance)}</option>`
  ).join("");
  for (const id of ["transfer-source", "transfer-target", "reconcile-account"]) {
    const select = $(id);
    const previous = select.value;
    select.innerHTML = `<option value="">请选择账户</option>${options}`;
    if (state.accounts.some((item) => item.name === previous)) select.value = previous;
  }
}

async function loadAccounts() {
  try {
    const data = await request("/api/accounts");
    state.accounts = data.items || [];
    $("account-summary").innerHTML = `
      <div><span>账户总余额</span><strong>${money(data.total_balance)}</strong></div>
      <div><span>已启用账户</span><strong>${state.accounts.length} 个</strong></div>
      <div><span>对账说明</span><strong>余额截至所选日期</strong></div>`;
    $("account-list").innerHTML = state.accounts.map((item) => {
      const difference = Number(item.last_difference || 0);
      return `<article class="account-card">
        <div class="account-card-head"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(accountKindLabel(item.kind))}</span></div>
        <span class="account-card-balance">${money(item.balance)}</span>
        <div class="account-card-meta"><span>最近对账 ${escapeHtml(item.baseline_date || "尚未")}</span><span class="difference">${difference ? `差异 ${difference > 0 ? "+" : ""}${money(difference)}` : "已对平"}</span></div>
        <div class="account-card-actions"><button class="row-action" type="button" data-edit-account="${escapeHtml(item.name)}" title="编辑账户" aria-label="编辑 ${escapeHtml(item.name)}">⋯</button></div>
      </article>`;
    }).join("") || '<div class="table-empty">还没有资金账户</div>';
    renderAccountSelectors();
  } catch (error) {
    toast(error.message);
  }
}

function openAccountEditor(name) {
  const account = state.accounts.find((item) => item.name === name);
  if (!account) return;
  $("account-edit-original").value = account.name;
  $("account-edit-name").value = account.name;
  $("account-edit-kind").value = account.kind;
  $("account-edit-balance").value = Number(account.balance).toFixed(2);
  $("account-edit-date").value = new Date().toISOString().slice(0, 10);
  $("account-dialog").showModal();
}

async function saveAccountEdit(event) {
  event.preventDefault();
  const original = $("account-edit-original").value;
  try {
    await request(`/api/accounts/${encodeURIComponent(original)}`, {
      method: "PATCH",
      body: JSON.stringify({
        new_name: $("account-edit-name").value.trim(),
        kind: $("account-edit-kind").value,
        actual_balance: Number($("account-edit-balance").value),
        reconciled_on: $("account-edit-date").value,
      }),
    });
    $("account-dialog").close();
    toast("账户已更新");
    await Promise.all([loadAccounts(), loadDashboard(), loadReferences(), loadLogs()]);
  } catch (error) { toast(error.message); }
}

async function deleteAccount() {
  const name = $("account-edit-original").value;
  if (!name || !window.confirm(`删除“${name}”吗？只有没有余额和关联记录的账户可以删除。`)) return;
  try {
    await request(`/api/accounts/${encodeURIComponent(name)}`, { method: "DELETE" });
    $("account-dialog").close();
    toast("账户已删除");
    await Promise.all([loadAccounts(), loadDashboard(), loadReferences(), loadLogs()]);
  } catch (error) { toast(error.message); }
}

async function saveTransfer(event) {
  event.preventDefault();
  try {
    const data = await request("/api/transfers", {
      method: "POST",
      body: JSON.stringify({
        source_account: $("transfer-source").value,
        target_account: $("transfer-target").value,
        amount: Number($("transfer-amount").value),
        transferred_on: $("transfer-date").value,
        note: $("transfer-note").value.trim(),
      }),
    });
    $("transfer-form").reset();
    $("transfer-date").value = new Date().toISOString().slice(0, 10);
    toast(`已从 ${data.transfer.source_account} 转入 ${data.transfer.target_account}`);
    await Promise.all([loadAccounts(), loadDashboard(), loadBills(), loadLogs()]);
  } catch (error) { toast(error.message); }
}

async function saveReconciliation(event) {
  event.preventDefault();
  try {
    const data = await request("/api/accounts/reconcile", {
      method: "POST",
      body: JSON.stringify({
        account: $("reconcile-account").value,
        actual_balance: Number($("reconcile-balance").value),
        reconciled_on: $("reconcile-date").value,
        note: $("reconcile-note").value.trim(),
      }),
    });
    const difference = Number(data.reconciliation.difference || 0);
    $("reconcile-form").reset();
    $("reconcile-date").value = new Date().toISOString().slice(0, 10);
    toast(difference ? `已对账，差异 ${difference > 0 ? "+" : ""}${money(difference)}` : "已对账，账面余额一致");
    await Promise.all([loadAccounts(), loadDashboard(), loadLogs()]);
  } catch (error) { toast(error.message); }
}

async function saveAccount(event) {
  event.preventDefault();
  try {
    await request("/api/accounts", {
      method: "POST",
      body: JSON.stringify({
        name: $("account-name").value.trim(), kind: $("account-kind").value,
        actual_balance: Number($("account-balance").value), reconciled_on: $("account-date").value,
      }),
    });
    $("account-form").reset();
    $("account-balance").value = "0";
    $("account-date").value = new Date().toISOString().slice(0, 10);
    toast("资金账户已新增");
    await Promise.all([loadAccounts(), loadDashboard(), loadReferences(), loadLogs()]);
  } catch (error) { toast(error.message); }
}

function openCapitalEditor() {
  switchView("accounts");
}

async function saveCapital(event) {
  event.preventDefault();
  const month = $("month").value;
  try {
    await request(`/api/capital/${encodeURIComponent(month)}`, {
      method: "PUT",
      body: JSON.stringify({ current_balance: Number($("capital-current").value) }),
    });
    $("capital-dialog").close();
    toast("当前本金已校准，后续月份会自动继承");
    await Promise.all([loadDashboard(), loadLogs()]);
  } catch (error) {
    toast(error.message);
  }
}

function openModelSettings() {
  $("model-provider").innerHTML = state.providers.map((provider) =>
    `<option value="${escapeHtml(provider.id)}">${escapeHtml(provider.label)}${provider.configured ? "" : "（未配置 Key）"}</option>`
  ).join("");
  $("model-provider").value = state.currentProvider;
  renderModelPresets(state.currentProvider, state.currentModel);
  $("model-dialog").showModal();
}

function renderModelPresets(providerId, selectedModel = "") {
  const provider = state.providers.find((item) => item.id === providerId);
  const presets = provider?.models || [];
  const model = selectedModel || provider?.model || presets[0] || "";
  const isPreset = presets.includes(model);
  $("model-preset").innerHTML = [
    ...presets.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`),
    '<option value="custom">自定义模型名</option>',
  ].join("");
  $("model-preset").value = isPreset ? model : "custom";
  $("custom-model-field").hidden = isPreset;
  $("custom-model").value = isPreset ? "" : model;
}

function changeModelProvider() {
  renderModelPresets($("model-provider").value);
}

function toggleCustomModel() {
  const custom = $("model-preset").value === "custom";
  $("custom-model-field").hidden = !custom;
  if (custom) $("custom-model").focus();
}

async function saveModel(event) {
  event.preventDefault();
  const provider = $("model-provider").value;
  const preset = $("model-preset").value;
  const model = preset === "custom" ? $("custom-model").value.trim() : preset;
  if (!model) return toast("请输入模型名");
  try {
    const data = await request("/api/settings/model", {
      method: "PUT",
      body: JSON.stringify({ provider, model }),
    });
    state.currentProvider = data.provider;
    state.currentModel = data.model;
    $("model-name").textContent = data.model;
    const selectedProvider = state.providers.find((item) => item.id === data.provider);
    if (selectedProvider) {
      selectedProvider.model = data.model;
      $("provider-name").textContent = selectedProvider.label;
    }
    $("model-dialog").close();
    toast(`已切换到 ${data.model}`);
  } catch (error) {
    toast(error.message);
  }
}

function draftEditor(draft, index) {
  const entryType = draft.entry_type || (draft.direction === "income" ? "income" : "expense");
  const direction = entryType === "income" ? "income" : "expense";
  const selectedLiabilityId = draft.liability_id || state.liabilityAccounts.find(
    (item) => item.name === draft.account
  )?.id || "";
  const liabilityOptions = state.liabilityAccounts.map((item) =>
    `<option value="${escapeHtml(item.id)}" ${item.id === selectedLiabilityId ? "selected" : ""}>${escapeHtml(item.name)}</option>`
  ).join("");
  const fundingFields = entryType === "credit"
    ? `
      <label>信贷账户<select data-field="liability_id"><option value="">请选择待还账户</option>${liabilityOptions}</select></label>
      <label>归属账单月<input data-field="statement_month" type="month" value="${escapeHtml(draft.statement_month || nextStatementMonth(draft.date))}"></label>
      <p class="wide muted">将新增或累加这个账单月的待还；其他月份的原有账单、已还金额和还款日不会被覆盖。</p>
    `
    : `<label>${entryType === "income" ? "入账账户" : "支付方式"}<input data-field="account" list="payment-methods" value="${escapeHtml(editableValue(draft.account))}" placeholder="未指定"></label>`;
  const confidence = Math.round(Number(draft.category_confidence ?? 1) * 100);
  const classificationText = draft.classification_source === "merchant_rule"
    ? `商户记忆 · ${escapeHtml(draft.category_reason || "已确认分类")}`
    : `${draft.needs_category_review ? "需要确认" : `模型置信度 ${confidence}%`}${draft.category_reason ? ` · ${escapeHtml(draft.category_reason)}` : ""}`;
  return `<div class="draft-item" data-draft-index="${index}">
    <div class="draft-item-heading">
      <div><strong>第 ${index + 1} 笔</strong><span class="classification-hint ${draft.needs_category_review ? "review" : ""}">${classificationText}</span></div>
      <div class="draft-item-actions"><button class="button primary" type="button" data-confirm-draft="${index}">确认这笔</button><button class="row-action" type="button" data-remove-draft="${index}" title="移除这笔" aria-label="移除第 ${index + 1} 笔">×</button></div>
    </div>
    <div class="form-grid">
      <label>日期<input data-field="date" type="date" value="${escapeHtml(draft.date)}"></label>
      <label>金额<input data-field="amount" type="number" min="0" step="0.01" value="${escapeHtml(draft.amount)}"></label>
      <label>类型<select data-field="entry_type"><option value="expense" ${entryType === "expense" ? "selected" : ""}>直接支付</option><option value="credit" ${entryType === "credit" ? "selected" : ""}>信贷消费</option><option value="income" ${entryType === "income" ? "selected" : ""}>收入</option></select></label>
      <label>分类<input data-field="category" list="category-values" value="${escapeHtml(editableValue(draft.category))}" placeholder="其他"></label>
      ${fundingFields}
      <label>商户 / 用途<input data-field="merchant" value="${escapeHtml(editableValue(draft.merchant))}" placeholder="未指定"></label>
      <label class="wide">备注（可选）<input data-field="note" value="${escapeHtml(editableValue(draft.note))}" placeholder="补充细节"></label>
    </div>
    ${draft.proposed_category ? `<div class="category-proposal"><span>建议新增分类：<strong>${escapeHtml(draft.proposed_category)}</strong></span><button class="button primary" type="button" data-approve-proposal="${index}">新增并应用</button></div>` : ""}
  </div>`;
}

function renderDrafts() {
  $("draft-title").textContent = "待确认账单";
  $("draft-list").innerHTML = state.drafts.map(draftEditor).join("");
  $("draft-list").hidden = false;
  $("management-draft-list").hidden = true;
  $("draft-count").textContent = `${state.drafts.length} 笔`;
  $("confirm-draft").hidden = true;
  $("draft").hidden = state.drafts.length === 0;
}

function showDrafts(drafts) {
  state.managementProposals = [];
  state.drafts = drafts.map((draft) => ({ ...draft }));
  $("draft").hidden = false;
  renderDrafts();
}

function managementDraftEditor(proposal, index) {
  const draft = proposal.draft || {};
  const remove = `<button class="row-action" type="button" data-remove-management-draft="${index}" title="移除这条" aria-label="移除第 ${index + 1} 条">×</button>`;
  const controls = `<div class="draft-item-actions"><button class="button primary" type="button" data-confirm-management-draft="${index}">确认这条</button>${remove}</div>`;
  if (proposal.type === "subscription_create") {
    return `<div class="draft-item" data-management-index="${index}">
      <div class="draft-item-heading"><strong>新建订阅</strong>${controls}</div>
      <div class="form-grid">
        <label>名称<input data-managed-field="name" value="${escapeHtml(draft.name)}"></label>
        <label>金额<input data-managed-field="amount" data-managed-number type="number" min="0.01" step="0.01" value="${escapeHtml(draft.amount)}"></label>
        <label>周期<select data-managed-field="cycle_months" data-managed-number><option value="1" ${Number(draft.cycle_months) === 1 ? "selected" : ""}>每月</option><option value="3" ${Number(draft.cycle_months) === 3 ? "selected" : ""}>每季</option><option value="6" ${Number(draft.cycle_months) === 6 ? "selected" : ""}>每半年</option><option value="12" ${Number(draft.cycle_months) === 12 ? "selected" : ""}>每年</option></select></label>
        <label>下次扣款<input data-managed-field="next_charge_date" type="date" value="${escapeHtml(draft.next_charge_date)}"></label>
        <label>分类<input data-managed-field="category" list="category-values" value="${escapeHtml(editableValue(draft.category))}"></label>
        <label>支付方式<input data-managed-field="account" list="payment-methods" value="${escapeHtml(editableValue(draft.account))}"></label>
        <label class="wide">备注（可选）<input data-managed-field="note" value="${escapeHtml(draft.note || "")}"></label>
      </div>
    </div>`;
  }
  if (proposal.type === "subscription_charge") {
    return `<div class="draft-item" data-management-index="${index}">
      <div class="draft-item-heading"><strong>确认订阅扣款</strong>${controls}</div>
      <div class="management-confirmation"><strong>${escapeHtml(draft.name)}</strong><span>${escapeHtml(draft.date)} · ${money(draft.amount)} · ${escapeHtml(draft.category)} · ${escapeHtml(draft.account)}</span></div>
    </div>`;
  }
  if (proposal.type === "subscription_skip") {
    return `<div class="draft-item" data-management-index="${index}">
      <div class="draft-item-heading"><strong>跳过本期订阅</strong>${controls}</div>
      <div class="management-confirmation"><strong>${escapeHtml(draft.name)}</strong><span>跳过 ${escapeHtml(draft.skipped_date)} · 下次 ${escapeHtml(draft.next_charge_date)}</span></div>
    </div>`;
  }
  if (proposal.type === "liability_charge") {
    const batchAmount = Number(proposal.batch_charge_amount || draft.amount || 0);
    const projected = Number(proposal.projected_due_amount ?? (Number(proposal.previous_due_amount || 0) + batchAmount));
    const statementMonthAdjustment = proposal.statement_month_adjusted_from
      ? `<small>已按出账日从 ${escapeHtml(proposal.statement_month_adjusted_from)} 调整至 ${escapeHtml(draft.statement_month)} 账单</small>`
      : "";
    return `<div class="draft-item" data-management-index="${index}">
      <div class="draft-item-heading"><strong>登记信用消费</strong>${controls}</div>
      <div class="management-confirmation credit-confirmation">
        <strong>${escapeHtml(draft.liability_name || "待还账户")} · ${escapeHtml(draft.statement_month)} 账单</strong>
        <span>本笔 ${money(draft.amount)} · 本批 ${money(batchAmount)} · 应还 ${money(proposal.previous_due_amount || 0)} → ${money(projected)}</span>
        ${statementMonthAdjustment}
        <small>不影响真实账户余额</small>
      </div>
      <div class="form-grid">
        <label>消费日期<input data-managed-field="charged_at" type="date" value="${escapeHtml(draft.charged_at)}"></label>
        <label>账单月份<input data-managed-field="statement_month" type="month" value="${escapeHtml(draft.statement_month)}"></label>
        <label>金额<input data-managed-field="amount" data-managed-number type="number" min="0.01" step="0.01" value="${escapeHtml(draft.amount)}"></label>
        <label>分类<input data-managed-field="category" list="category-values" value="${escapeHtml(editableValue(draft.category))}"></label>
        <label>商户 / 用途<input data-managed-field="merchant" value="${escapeHtml(editableValue(draft.merchant))}"></label>
        <label class="wide">备注（可选）<input data-managed-field="note" value="${escapeHtml(draft.note || "")}"></label>
      </div>
    </div>`;
  }
  if (proposal.type === "account_transfer") {
    return `<div class="draft-item" data-management-index="${index}">
      <div class="draft-item-heading"><strong>账户转账</strong>${controls}</div>
      <div class="form-grid">
        <label>转出账户<input data-managed-field="source_account" list="payment-methods" value="${escapeHtml(draft.source_account)}"></label>
        <label>转入账户<input data-managed-field="target_account" list="payment-methods" value="${escapeHtml(draft.target_account)}"></label>
        <label>金额<input data-managed-field="amount" data-managed-number type="number" min="0.01" step="0.01" value="${escapeHtml(draft.amount)}"></label>
        <label>日期<input data-managed-field="transferred_on" type="date" value="${escapeHtml(draft.transferred_on)}"></label>
        <label class="wide">备注（可选）<input data-managed-field="note" value="${escapeHtml(draft.note || "")}"></label>
      </div>
    </div>`;
  }
  if (proposal.type === "liability_create" || proposal.type === "liability_update") {
    const heading = proposal.type === "liability_create" ? "新建待还" : "写入已有待还账户的本期账单";
    const mergeSummary = proposal.type === "liability_update" && proposal.merged_amount !== undefined
      ? `<div class="management-confirmation"><span>已有本月应还 ${money(proposal.previous_due_amount)} + 本次 ${money(proposal.merged_amount)} = ${money(draft.due_amount)}</span></div>`
      : "";
    return `<div class="draft-item" data-management-index="${index}">
      <div class="draft-item-heading"><strong>${heading}</strong>${controls}</div>
      ${mergeSummary}
      <div class="form-grid">
        <label>名称<input data-managed-field="name" value="${escapeHtml(draft.name)}"></label>
        <label>平台 / 发卡行<input data-managed-field="provider" value="${escapeHtml(draft.provider || "")}"></label>
        <label>类型<select data-managed-field="kind"><option value="consumer_credit" ${draft.kind === "consumer_credit" ? "selected" : ""}>消费信贷</option><option value="credit_card" ${draft.kind === "credit_card" ? "selected" : ""}>信用卡</option><option value="installment" ${draft.kind === "installment" ? "selected" : ""}>分期</option><option value="other" ${draft.kind === "other" ? "selected" : ""}>其他</option></select></label>
        <label>每月出账日（可选）<select data-managed-field="statement_day">${statementDayOptions(draft.statement_day)}</select></label>
        <label>账单月份<input data-managed-field="statement_month" type="month" value="${escapeHtml(draft.statement_month || "")}"></label>
        <label>本月应还<input data-managed-field="due_amount" data-managed-number type="number" min="0" step="0.01" value="${escapeHtml(draft.due_amount || "")}"></label>
        <label>还款日（可选）<input data-managed-field="due_date" type="date" value="${escapeHtml(draft.due_date || "")}"></label>
        <label>最低还款<input data-managed-field="minimum_payment" data-managed-number type="number" min="0" step="0.01" value="${escapeHtml(draft.minimum_payment || "")}" placeholder="无最低还款可不填"></label>
        <label>还款方式<input data-managed-field="repayment_account" list="payment-methods" value="${escapeHtml(editableValue(draft.repayment_account))}"></label>
        <label>额度（可选）<input data-managed-field="credit_limit" data-managed-number data-managed-nullable type="number" min="0" step="0.01" value="${escapeHtml(draft.credit_limit ?? "")}"></label>
        <label class="wide">备注（可选）<input data-managed-field="note" value="${escapeHtml(draft.note || "")}"></label>
      </div>
    </div>`;
  }
  return `<div class="draft-item" data-management-index="${index}">
    <div class="draft-item-heading"><strong>登记还款</strong>${controls}</div>
    <div class="form-grid">
      <label>还款金额<input data-managed-field="amount" data-managed-number type="number" min="0.01" step="0.01" value="${escapeHtml(draft.amount)}"></label>
      <label>账单月份<input data-managed-field="statement_month" type="month" value="${escapeHtml(draft.statement_month || "")}"></label>
      <label>还款日期<input data-managed-field="paid_at" type="date" value="${escapeHtml(draft.paid_at)}"></label>
      <label>还款方式<input data-managed-field="account" list="payment-methods" value="${escapeHtml(editableValue(draft.account))}" placeholder="未指定"></label>
      <label class="wide">备注（可选）<input data-managed-field="note" value="${escapeHtml(draft.note || "")}"></label>
    </div>
  </div>`;
}

function renderManagementDrafts() {
  $("draft-title").textContent = "待确认管理操作";
  $("draft-list").hidden = true;
  $("management-draft-list").hidden = false;
  $("management-draft-list").innerHTML = state.managementProposals.map(managementDraftEditor).join("");
  $("draft-count").textContent = `${state.managementProposals.length} 条`;
  $("confirm-draft").hidden = true;
  $("draft").hidden = state.managementProposals.length === 0;
}

function showManagementDrafts(proposals) {
  state.drafts = [];
  state.managementProposals = proposals.map((proposal) => ({ ...proposal, draft: { ...(proposal.draft || {}) } }));
  renderManagementDrafts();
}

function collectManagementProposals() {
  return [...document.querySelectorAll("[data-management-index]")].map((node, index) => {
    const proposal = { ...state.managementProposals[index], draft: { ...(state.managementProposals[index].draft || {}) } };
    node.querySelectorAll("[data-managed-field]").forEach((field) => {
      let value = field.value;
      if (field.dataset.managedNullable !== undefined && value === "") value = null;
      else if (field.dataset.managedNumber !== undefined) value = Number(value);
      proposal.draft[field.dataset.managedField] = value;
    });
    return proposal;
  });
}

async function removeManagementDraft(index) {
  const proposals = collectManagementProposals();
  const remainingProposals = proposals.filter((_, proposalIndex) => proposalIndex !== index);
  if (!state.draftRequestId) return;
  try {
    await request(`/api/chat/requests/${encodeURIComponent(state.draftRequestId)}/management-proposals`, {
      method: "PATCH",
      body: JSON.stringify({ proposals: remainingProposals }),
    });
    state.managementProposals = remainingProposals;
    if (!remainingProposals.length) {
      state.draftRequestId = "";
      $("draft").hidden = true;
    } else {
      renderManagementDrafts();
    }
    addMessage("已移除这条待确认草稿。", "agent");
  } catch (error) {
    toast(error.message);
  }
}

function collectDrafts() {
  return [...document.querySelectorAll("[data-draft-index]")].map((node, index) => {
    const draft = { ...state.drafts[index] };
    for (const field of ["date", "amount", "entry_type", "category", "account", "liability_id", "statement_month", "merchant", "note"]) {
      const input = node.querySelector(`[data-field="${field}"]`);
      if (input) draft[field] = input.value;
    }
    draft.entry_type = draft.entry_type || (draft.direction === "income" ? "income" : "expense");
    draft.direction = draft.entry_type === "income" ? "income" : "expense";
    draft.amount = Number(draft.amount);
    if (draft.category !== state.drafts[index].category) {
      draft.category_confidence = 1;
      draft.category_reason = "用户手动确认";
      draft.classification_source = "manual";
      draft.suggested_category = "";
      draft.proposed_category = "";
      draft.needs_category_review = false;
    }
    return draft;
  });
}

function removeDraft(index) {
  state.drafts = collectDrafts();
  state.drafts.splice(index, 1);
  renderDrafts();
}

async function approveCategoryProposal(index) {
  state.drafts = collectDrafts();
  const draft = state.drafts[index];
  const proposal = draft?.proposed_category?.trim();
  if (!proposal) return;
  try {
    await request("/api/references/category", {
      method: "POST",
      body: JSON.stringify({ name: proposal, aliases: [], is_favorite: true }),
    });
  } catch (error) {
    await loadReferences();
    const alreadyExists = state.references.category.some((item) => item.name === proposal);
    if (!alreadyExists) return toast(error.message);
  }
  draft.category = proposal;
  draft.category_confidence = 1;
  draft.category_reason = "用户批准 Agent 分类提案";
  draft.classification_source = "manual";
  draft.proposed_category = "";
  draft.suggested_category = "";
  draft.needs_category_review = false;
  renderDrafts();
  await loadReferences();
  toast(`已新增并应用分类“${proposal}”`);
}

function takeComposerMessage() {
  const text = $("prompt").value.trim();
  const imageItems = state.selectedImages.map((item) => ({ ...item }));
  if (!text && !imageItems.length) return null;
  $("prompt").value = "";
  state.selectedImages = [];
  $("receipt-images").value = "";
  renderSelectedImages();
  return { text, imageItems };
}

function addQueuedChatMessage(item) {
  const node = addMessage(
    item.text || `上传了 ${item.imageItems.length} 张账单截图`,
    "user",
    false,
    true,
    item.imageItems.map((image) => ({ url: image.dataUrl, name: image.name })),
  );
  node.classList.add("queued");
  const status = document.createElement("small");
  status.className = "queue-status";
  status.textContent = "等待中";
  node.appendChild(status);
  return node;
}

function enqueueChatMessage(item) {
  if (state.chatQueue.length >= 10) {
    toast("等待队列已满，请等当前消息处理完成");
    $("prompt").value = item.text;
    return;
  }
  item.node = addQueuedChatMessage(item);
  state.chatQueue.push(item);
  toast(`已加入等待队列（${state.chatQueue.length} 条）`);
}

function processNextQueuedChat() {
  if (state.busy || state.transcribingAudio || !state.chatQueue.length) return;
  const item = state.chatQueue.shift();
  void startChatRequest(item);
}

async function submitChat(event) {
  event.preventDefault();
  if (state.transcribingAudio) return;
  const item = takeComposerMessage();
  if (!item) return;
  if (state.busy) {
    enqueueChatMessage(item);
    return;
  }
  await startChatRequest(item);
}

async function startChatRequest(item) {
  const { text, imageItems } = item;
  const images = imageItems.map((image) => image.dataUrl);
  const requestId = chatRequestId();
  state.currentRequestId = requestId;
  state.cancelReason = "";
  state.controller = new AbortController();
  setChatBusy(true);
  if (item.node) {
    item.node.classList.remove("queued");
    item.node.querySelector(".queue-status")?.remove();
  } else {
    addMessage(
      text || `上传了 ${images.length} 张账单截图`,
      "user",
      false,
      true,
      imageItems.map((image) => ({ url: image.dataUrl, name: image.name })),
    );
  }
  const pending = addPendingMessage(Date.now(), requestId);
  watchChatProgress(requestId, pending);
  const startedAt = Date.now();
  const elapsed = setInterval(() => {
    pending.querySelector("b").textContent = Math.floor((Date.now() - startedAt) / 1000);
  }, 1000);
  const timeout = setTimeout(() => {
    state.cancelReason = "timeout";
    state.controller?.abort();
  }, 120000);
  let handedToPoll = false;
  try {
    const data = await request(images.length ? "/api/chat/image" : "/api/chat", {
      method: "POST",
      body: JSON.stringify({ text, images, session_id: state.sessionId, request_id: requestId }),
      signal: state.controller.signal,
      retryNetwork: true,
    });
    pending.remove();
    state.progressSource?.close();
    if (data.kind === "pending") {
      handedToPoll = true;
      const restoredPending = addPendingMessage(startedAt, requestId);
      pollChatRequest(requestId, restoredPending, startedAt);
    } else {
      applyAgentResult(data);
    }
    await loadDashboard();
  } catch (error) {
    pending.remove();
    state.progressSource?.close();
    if (error.name === "AbortError") {
      addMessage(
        state.cancelReason === "timeout"
          ? "模型响应超过 120 秒，已停止等待；刷新页面可以恢复后台进度。"
          : "已停止等待；刷新页面可以恢复后台进度。",
        "agent",
        state.cancelReason === "timeout",
      );
    } else {
      addMessage(
        error.message === "Failed to fetch" || error.message === "无法连接本地服务"
          ? "无法连接本地服务。请稍后重试；若持续出现，请从桌面入口重新启动 Ledger Agent。"
          : error.message,
        "agent",
        true,
      );
    }
  } finally {
    clearInterval(elapsed);
    clearTimeout(timeout);
    if (!handedToPoll) setChatBusy(false);
    state.controller = null;
    if (!handedToPoll) state.currentRequestId = "";
    state.cancelReason = "";
    $("prompt").focus();
    if (!handedToPoll) processNextQueuedChat();
  }
}

function readImage(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("图片读取失败"));
    reader.readAsDataURL(file);
  });
}

function imageFileKey(file) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function renderSelectedImages() {
  const previews = $("image-previews");
  previews.hidden = !state.selectedImages.length;
  previews.innerHTML = state.selectedImages.map((item, index) => `
    <div class="image-preview" title="${escapeHtml(item.name)}">
      <img src="${item.dataUrl}" alt="">
      <button class="image-remove" type="button" data-remove-image="${index}" title="移除图片" aria-label="移除 ${escapeHtml(item.name)}">×</button>
    </div>
  `).join("");
}

function removeSelectedImage(index) {
  state.selectedImages.splice(index, 1);
  renderSelectedImages();
  $("receipt-images").value = "";
}

async function addReceiptFiles(files) {
  if (state.busy) return;
  const acceptedTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
  const candidates = [...files];
  if (!candidates.length) return;
  if (candidates.some((file) => (
    !acceptedTypes.has(file.type) && !/\.(png|jpe?g|webp)$/i.test(file.name)
  ))) {
    throw new Error("只支持 PNG、JPEG 或 WebP 图片");
  }
  if (candidates.some((file) => file.size > 6 * 1024 * 1024)) {
    throw new Error("单张图片不能超过 6 MB");
  }

  const existingKeys = new Set(state.selectedImages.map((item) => item.key));
  const uniqueFiles = candidates.filter((file) => !existingKeys.has(imageFileKey(file)));
  const available = Math.max(0, 3 - state.selectedImages.length);
  const selected = uniqueFiles.slice(0, available);
  if (!selected.length) {
    toast(available ? "这些图片已经添加" : "一次最多添加 3 张图片");
    return;
  }

  const imageItems = await Promise.all(selected.map(async (file) => ({
    key: imageFileKey(file),
    name: file.name || "账单截图",
    dataUrl: await readImage(file),
  })));
  state.selectedImages.push(...imageItems);
  renderSelectedImages();
  if (uniqueFiles.length > available) {
    toast(`已添加 ${selected.length} 张，一次最多 3 张`);
  } else {
    toast(`已添加 ${state.selectedImages.length} 张截图`);
  }
}

async function selectReceiptImages(event) {
  try {
    await addReceiptFiles(event.target.files);
  } catch (error) {
    toast(error.message);
  } finally {
    event.target.value = "";
  }
}

function updateAudioButton() {
  const button = $("record-audio");
  const supported = Boolean(globalThis.MediaRecorder && navigator.mediaDevices?.getUserMedia);
  const configured = Boolean(state.speechToText?.configured);
  button.hidden = !supported;
  button.disabled = state.busy || state.transcribingAudio || (!configured && !state.recording);
  button.classList.toggle("recording", state.recording);
  button.innerHTML = state.recording
    ? '<svg class="button-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1"></rect></svg>'
    : '<svg class="button-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"></rect><path d="M5 10a7 7 0 0 0 14 0"></path><path d="M12 17v4"></path><path d="M8 21h8"></path></svg>';
  const label = state.recording ? "停止录音" : state.transcribingAudio ? "正在转写语音" : "开始语音输入";
  const missingConfig = configured ? "" : "（需要配置 GROQ_API_KEY）";
  button.title = `${label}${missingConfig}`;
  button.setAttribute("aria-label", `${label}${missingConfig}`);
}

function stopAudioStream() {
  state.audioStream?.getTracks().forEach((track) => track.stop());
  state.audioStream = null;
}

async function transcribeRecordedAudio(audio) {
  state.transcribingAudio = true;
  updateAudioButton();
  try {
    const extension = audio.type.includes("ogg") ? "ogg" : audio.type.includes("mp4") ? "mp4" : "webm";
    const data = await requestAudio("/api/audio/transcriptions", audio, `voice-note.${extension}`);
    const original = $("prompt").value.trim();
    $("prompt").value = [original, data.text].filter(Boolean).join(original ? " " : "");
    $("prompt").focus();
    toast("语音已转成文字，请检查后发送");
  } catch (error) {
    toast(error.message);
  } finally {
    state.transcribingAudio = false;
    updateAudioButton();
  }
}

async function toggleAudioRecording() {
  if (state.recording && state.audioRecorder) {
    state.audioRecorder.stop();
    return;
  }
  if (state.busy || state.transcribingAudio) return;
  if (!state.speechToText?.configured) {
    toast("请先在 .env 配置 GROQ_API_KEY");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks = [];
    state.audioStream = stream;
    state.audioRecorder = recorder;
    state.recording = true;
    updateAudioButton();
    toast("正在录音，点击红色按钮结束");
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) chunks.push(event.data);
    });
    recorder.addEventListener("stop", async () => {
      state.recording = false;
      state.audioRecorder = null;
      stopAudioStream();
      updateAudioButton();
      const audio = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      if (!audio.size) return toast("没有录到声音，请检查麦克风权限后重试");
      await transcribeRecordedAudio(audio);
    }, { once: true });
    recorder.start();
  } catch (error) {
    state.recording = false;
    state.audioRecorder = null;
    stopAudioStream();
    updateAudioButton();
    toast(error.name === "NotAllowedError" ? "请允许浏览器使用麦克风" : "无法开始录音");
  }
}

let composerDragDepth = 0;

function hasDraggedFiles(event) {
  return Array.from(event.dataTransfer?.types || []).includes("Files");
}

function setComposerDragging(active) {
  $("composer").classList.toggle("dragging", active);
  $("composer-drop-hint").hidden = !active;
}

function handleComposerDragEnter(event) {
  if (!hasDraggedFiles(event) || state.busy) return;
  event.preventDefault();
  composerDragDepth += 1;
  setComposerDragging(true);
}

function handleComposerDragOver(event) {
  if (!hasDraggedFiles(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = state.busy ? "none" : "copy";
}

function handleComposerDragLeave(event) {
  if (!composerDragDepth) return;
  composerDragDepth = Math.max(0, composerDragDepth - 1);
  if (!composerDragDepth) setComposerDragging(false);
}

async function handleComposerDrop(event) {
  if (!hasDraggedFiles(event)) return;
  event.preventDefault();
  composerDragDepth = 0;
  setComposerDragging(false);
  if (state.busy) {
    toast("当前请求完成后再添加图片");
    return;
  }
  try {
    await addReceiptFiles(event.dataTransfer.files);
    $("prompt").focus();
  } catch (error) {
    toast(error.message);
  }
}

async function confirmDrafts(selectedIndex = null) {
  if (state.managementProposals.length) {
    return confirmManagementDrafts();
  }
  if (!state.drafts.length) return;
  const allDrafts = collectDrafts();
  const selectedDrafts = selectedIndex === null ? allDrafts : [allDrafts[selectedIndex]];
  const remainingDrafts = selectedIndex === null
    ? []
    : allDrafts.filter((_, index) => index !== selectedIndex);
  const creditCharges = selectedDrafts.filter((draft) => draft.entry_type === "credit");
  const transactions = selectedDrafts.filter((draft) => draft.entry_type !== "credit");
  if (creditCharges.some((draft) => !draft.liability_id || !draft.statement_month)) {
    toast("请为每笔信贷消费选择信贷账户和归属账单月");
    return;
  }
  transactions.forEach((draft) => {
    if (draft.needs_category_review && draft.category !== "待分类") {
      draft.needs_category_review = false;
      draft.classification_source = "user_confirmed";
      draft.category_reason = `${draft.category_reason || "模型低置信度分类"}（用户确认）`;
    }
  });
  const submitBatch = (allowDuplicate = false) => {
    if (!creditCharges.length) {
      return request("/api/transactions/confirm-batch", {
        method: "POST",
        body: JSON.stringify({ transactions, allow_duplicate: allowDuplicate, request_id: state.draftRequestId, complete_request: remainingDrafts.length === 0 }),
      });
    }
    return request("/api/transactions/confirm-mixed", {
      method: "POST",
      body: JSON.stringify({
        transactions,
        credit_charges: creditCharges.map((draft) => ({
          liability_id: draft.liability_id,
          statement_month: draft.statement_month,
          amount: draft.amount,
          charged_at: draft.date,
          category: draft.category,
          merchant: draft.merchant,
          note: draft.note,
        })),
        allow_duplicate: allowDuplicate,
        request_id: state.draftRequestId,
        complete_request: remainingDrafts.length === 0,
      }),
    });
  };
  try {
    await submitBatch();
    addMessage(`已写入 ${selectedDrafts.length} 笔账单，合计 ${money(selectedDrafts.reduce((sum, item) => sum + item.amount, 0))}。`, "agent");
    state.drafts = remainingDrafts;
    if (!state.drafts.length) {
      state.draftRequestId = "";
      $("draft").hidden = true;
    } else {
      renderDrafts();
    }
    await Promise.all([loadDashboard(), loadBills()]);
  } catch (error) {
    if (error.status === 409 && error.data?.code === "duplicate_transaction") {
      const matches = (error.data.duplicates || []).map((item) => {
        if (item.match === "batch") return `草稿第 ${item.draft_index + 1} 笔与本批第 ${item.duplicate_of_draft_index + 1} 笔相同`;
        const tx = item.transaction || {};
        return `草稿第 ${item.draft_index + 1} 笔可能重复：${tx.date || ""} ${tx.merchant || tx.category || ""} ${money(tx.amount)}`;
      }).join("\n");
      if (window.confirm(`检测到可能重复的账单：\n${matches}\n\n仍要整批写入吗？`)) {
        try {
          await submitBatch(true);
          addMessage(`已确认重复风险并写入 ${selectedDrafts.length} 笔账单。`, "agent");
          state.drafts = remainingDrafts;
          if (!state.drafts.length) {
            state.draftRequestId = "";
            $("draft").hidden = true;
          } else {
            renderDrafts();
          }
          await Promise.all([loadDashboard(), loadBills()]);
        } catch (retryError) { toast(retryError.message); }
      }
    } else {
      toast(error.message);
    }
  }
}

async function confirmManagementDrafts(selectedIndex = null) {
  const allProposals = collectManagementProposals();
  if (!allProposals.length || !state.draftRequestId) return;
  const proposals = selectedIndex === null ? allProposals : [allProposals[selectedIndex]];
  const remainingProposals = selectedIndex === null
    ? []
    : allProposals.filter((_, index) => index !== selectedIndex);
  try {
    const data = await request("/api/management-proposals/confirm", {
      method: "POST",
      body: JSON.stringify({
        request_id: state.draftRequestId,
        proposals,
        remaining_proposals: remainingProposals,
        complete_request: remainingProposals.length === 0,
      }),
    });
    addMessage(`已保存 ${data.applied} 条管理记录。`, "agent");
    state.managementProposals = remainingProposals;
    if (!state.managementProposals.length) {
      state.draftRequestId = "";
      $("draft").hidden = true;
    } else {
      renderManagementDrafts();
    }
    await Promise.all([loadSubscriptions(), loadLiabilities(), loadAccounts(), loadDashboard(), loadLogs()]);
  } catch (error) {
    if (error.status === 409) toast("检测到相同订阅扣款账单，未重复写入");
    else toast(error.message);
  }
}

function switchView(view, updateUrl = true) {
  if (!viewMeta[view]) view = "chat";
  state.activeView = view;
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((node) => node.classList.remove("active"));
  $(`view-${view}`).classList.add("active");
  $("clear-chat").hidden = view !== "chat";
  [$("view-title").textContent, $("view-subtitle").textContent] = viewMeta[view];
  if (updateUrl) {
    const url = new URL(window.location.href);
    url.searchParams.set("view", view);
    window.history.replaceState({}, "", url);
  }
  if (view === "bills") loadBills();
  if (view === "accounts") loadAccounts();
  if (view === "budgets") loadBudgets();
  if (view === "subscriptions") loadSubscriptions();
  if (view === "liabilities") loadLiabilities();
  if (view === "logs") loadLogs();
  if (view === "memory") loadPersonalMemories();
  if (view === "settings") loadSettings();
  if (view === "chat") scrollMessagesToLatest("auto");
}

const operationFilters = [
  ["", "全部操作"],
  ["transaction.", "账单操作"],
  ["budget.", "预算操作"],
  ["subscription.", "订阅操作"],
  ["liability.", "待还操作"],
  ["account.", "账户操作"],
  ["chat.", "聊天记录"],
  ["settings.", "设置操作"],
  ["import.", "导入操作"],
];
const agentFilters = [["", "全部状态"], ["success", "成功"], ["error", "失败"]];

function renderLogFilter() {
  const options = state.logKind === "operations" ? operationFilters : agentFilters;
  $("log-filter").innerHTML = options.map(([value, label]) =>
    `<option value="${value}">${label}</option>`
  ).join("");
}

function formatLogTime(value) {
  return String(value || "").replace("T", " ");
}

function operationLogRows(logs) {
  $("log-head").innerHTML = "<tr><th>时间</th><th>操作</th><th>内容</th><th>来源</th></tr>";
  return logs.map((log) => {
    const changes = (log.changes || []).map((change) =>
      `${escapeHtml(change.field)}：${escapeHtml(change.before || "空")} → ${escapeHtml(change.after || "空")}`
    ).join("；");
    return `<tr>
      <td>${escapeHtml(formatLogTime(log.created_at))}</td>
      <td><strong>${escapeHtml(log.label)}</strong></td>
      <td class="log-main"><strong>${escapeHtml(log.description || "-")}</strong>${changes ? `<span>${changes}</span>` : ""}</td>
      <td>${escapeHtml(log.source === "web" ? "Web UI" : "本地")}</td>
    </tr>`;
  }).join("");
}

function agentLogRows(logs) {
  $("log-head").innerHTML = "<tr><th>时间</th><th>状态</th><th>调用</th><th>耗时</th></tr>";
  return logs.map((log) => {
    const statusText = log.status === "success" ? "成功" : "失败";
    const output = Number(log.output_count || 0) > 0 ? ` · ${log.output_count} 条草稿` : "";
    const detail = log.error_message
      ? `${escapeHtml(log.action || "未识别")} · ${escapeHtml(log.error_type)}：${escapeHtml(log.error_message)}`
      : `${escapeHtml(log.action || "未识别")}${output} · ${log.tool_mode === "structured_output" ? "结构化分类" : "原生工具"} · session ${escapeHtml(log.session_id || "-")}`;
    const steps = (log.steps || []).map((step) => {
      const risk = step.risk === "write" ? "写入" : "只读";
      const status = step.status === "success" ? "成功" : step.status === "blocked" ? "已阻止" : "失败";
      return `${step.step_index}. ${escapeHtml(step.tool_name)} · ${risk} · ${status}`;
    }).join("；");
    const usage = Number(log.model_requests || 0) > 0
      ? `${Number(log.model_requests)} 次模型调用 · Token ${Number(log.input_tokens || 0)} 输入 / ${Number(log.output_tokens || 0)} 输出${log.estimated_cost == null ? " · 费用未配置" : ` · 估算费用 ${Number(log.estimated_cost).toFixed(6)}`}`
      : "未记录模型 Token";
    const modelTiming = (log.model_calls || []).length
      ? (log.model_calls || []).map((call, index) =>
        `模型 ${index + 1}：${(Number(call.duration_ms || 0) / 1000).toFixed(2)} 秒 · ${Number(call.input_tokens || 0)} 输入 / ${Number(call.output_tokens || 0)} 输出`
      ).join("；")
      : "";
    return `<tr>
      <td>${escapeHtml(formatLogTime(log.created_at))}</td>
      <td><span class="log-status ${escapeHtml(log.status)}">${statusText}</span></td>
      <td class="log-main"><strong>${escapeHtml(log.provider)} · ${escapeHtml(log.model)}</strong><span>${detail}</span><span>${usage}</span>${modelTiming ? `<span>${modelTiming}</span>` : ""}${steps ? `<span>${steps}</span>` : ""}</td>
      <td>${(Number(log.duration_ms || 0) / 1000).toFixed(2)} 秒</td>
    </tr>`;
  }).join("");
}

async function loadLogs() {
  const filter = $("log-filter").value;
  const url = state.logKind === "operations"
    ? `/api/logs/operations?limit=200&action_prefix=${encodeURIComponent(filter)}`
    : `/api/logs/agent?limit=200&status=${encodeURIComponent(filter)}`;
  try {
    const data = await request(url);
    $("logs-empty").hidden = data.logs.length > 0;
    $("log-rows").innerHTML = state.logKind === "operations"
      ? operationLogRows(data.logs)
      : agentLogRows(data.logs);
  } catch (error) {
    toast(error.message);
  }
}

function switchLogKind(kind) {
  state.logKind = kind;
  document.querySelectorAll("[data-log-kind]").forEach((button) => {
    button.classList.toggle("active", button.dataset.logKind === kind);
  });
  renderLogFilter();
  loadLogs();
}

async function loadBills() {
  const params = new URLSearchParams({
    month: $("month").value,
    query: $("bill-search").value.trim(),
    direction: $("bill-direction").value,
    limit: "200",
  });
  try {
    const data = await request(`/api/financial-records?${params}`);
    state.transactions = data.results;
    renderBills();
  } catch (error) {
    toast(error.message);
  }
}

function billCategoryLabel(tx) {
  if (tx.record_type === "liability_payment") return "还款";
  if (tx.record_type === "transfer") return "账户转账";
  if (tx.record_type === "liability_statement") return "待还变动";
  if (tx.record_type === "liability_charge") return "信用消费";
  return tx.category || "待分类";
}

function billSortValue(tx, key) {
  if (key === "date") return String(tx.date || "");
  if (key === "subject") return tx.record_type === "transfer"
    ? String(tx.account || "")
    : String(tx.merchant || tx.note || "未填写");
  if (key === "category") return billCategoryLabel(tx);
  if (key === "account") return tx.record_type === "transfer" ? "内部转账" : String(tx.account || "未指定");
  return Number(tx.amount || 0);
}

function sortedBills() {
  const { key, direction } = state.billSort;
  const multiplier = direction === "asc" ? 1 : -1;
  return [...state.transactions].sort((left, right) => {
    const first = billSortValue(left, key);
    const second = billSortValue(right, key);
    const result = key === "amount"
      ? first - second
      : String(first).localeCompare(String(second), "zh-CN", { numeric: true });
    if (result) return result * multiplier;
    return Number(right.id || 0) - Number(left.id || 0);
  });
}

function renderBillSortIndicators() {
  document.querySelectorAll("[data-bill-sort]").forEach((button) => {
    const active = button.dataset.billSort === state.billSort.key;
    button.closest("th").setAttribute("aria-sort", active
      ? (state.billSort.direction === "asc" ? "ascending" : "descending")
      : "none");
    button.querySelector(".sort-indicator").textContent = active
      ? (state.billSort.direction === "asc" ? "↑" : "↓")
      : "↕";
  });
}

function renderBills() {
  const records = sortedBills();
  $("bills-empty").hidden = records.length > 0;
  renderBillSortIndicators();
  $("bill-rows").innerHTML = records.map((tx) => {
    const isRepayment = tx.record_type === "liability_payment";
    const isTransfer = tx.record_type === "transfer";
    const isLiabilityChange = tx.record_type === "liability_statement";
    const isLiabilityCharge = tx.record_type === "liability_charge";
    const categoryDetail = isRepayment
      ? `还款${tx.statement_month ? `<span>归属 ${escapeHtml(tx.statement_month)} 账单</span>` : ""}`
      : isTransfer
        ? "账户转账<span>不计入收入或支出</span>"
      : isLiabilityChange
        ? `待还变动${tx.statement_month ? `<span>归属 ${escapeHtml(tx.statement_month)} 账单 · 不影响现金</span>` : "<span>不影响现金</span>"}`
        : isLiabilityCharge
          ? `信用消费${tx.statement_month ? `<span>归属 ${escapeHtml(tx.statement_month)} 账单 · 不影响现金</span>` : "<span>不影响现金</span>"}`
          : `${escapeHtml(tx.category)}${tx.needs_category_review && tx.suggested_category ? `<span>建议：${escapeHtml(tx.suggested_category)}</span>` : ""}`;
    let action;
    if (isRepayment) {
      action = `<div class="record-actions">
        <button class="row-action" type="button" data-edit-payment="${escapeHtml(tx.id)}" title="编辑还款" aria-label="编辑还款">⋯</button>
        <button class="row-action" type="button" data-open-liability="${escapeHtml(tx.liability_id)}" data-statement-month="${escapeHtml(tx.statement_month)}" title="查看对应待还账单" aria-label="查看对应待还账单">→</button>
      </div>`;
    } else if (tx.source === "subscription") {
      action = `<button class="row-action" type="button" data-reverse-subscription-charge="${tx.id}" title="撤销订阅扣款" aria-label="撤销订阅扣款">↶</button>`;
    } else if (isTransfer) {
      action = "";
    } else if (isLiabilityChange || isLiabilityCharge) {
      action = `<button class="row-action" type="button" data-open-liability="${escapeHtml(tx.liability_id)}" data-statement-month="${escapeHtml(tx.statement_month)}" title="查看对应待还账单" aria-label="查看对应待还账单">→</button>`;
    } else {
      action = `<button class="row-action" type="button" data-edit-id="${tx.id}" title="编辑" aria-label="编辑">⋯</button>`;
    }
    const subject = isTransfer ? tx.account : (tx.merchant || tx.note || "未填写");
    const account = isTransfer ? "内部转账" : tx.account;
    return `
    <tr>
      <td>${escapeHtml(tx.date)}</td>
      <td title="${escapeHtml(subject)}">${escapeHtml(subject)}</td>
      <td class="category-cell">${categoryDetail}</td>
      <td title="${escapeHtml(account)}">${escapeHtml(account)}</td>
      <td class="amount ${escapeHtml(tx.direction)}">${isLiabilityChange ? "负债 " : (isTransfer ? "↔" : (tx.direction === "income" ? "+" : "-"))}${money(tx.amount)}</td>
      <td>${action}</td>
    </tr>`;
  }).join("");
}

function openEditor(id) {
  const tx = state.transactions.find((item) => item.id === id);
  if (!tx) return;
  state.editing = tx;
  for (const key of ["date", "amount", "direction", "category", "account", "merchant", "note"]) {
    const value = key === "category" && tx.needs_category_review && tx.suggested_category
      ? tx.suggested_category : tx[key];
    $(`edit-${key}`).value = editableValue(value);
  }
  $("edit-dialog").showModal();
}

async function openLiabilityFromRecord(id, statementMonth) {
  if (!id || !statementMonth) return;
  $("month").value = statementMonth;
  switchView("liabilities");
  await Promise.all([loadLiabilities(), loadDashboard()]);
  const row = [...document.querySelectorAll("[data-liability-row]")].find(
    (item) => item.dataset.liabilityRow === id && item.dataset.statementMonth === statementMonth
  );
  if (!row) return;
  row.classList.add("link-target");
  row.scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(() => row.classList.remove("link-target"), 1800);
}

async function saveEdit(event) {
  event.preventDefault();
  if (!state.editing) return;
  const payload = {};
  for (const key of ["date", "amount", "direction", "category", "account", "merchant", "note"]) {
    payload[key] = $(`edit-${key}`).value;
  }
  payload.amount = Number(payload.amount);
  try {
    await request(`/api/transactions/${state.editing.id}`, { method: "PATCH", body: JSON.stringify(payload) });
    $("edit-dialog").close();
    toast("账单已更新");
    await Promise.all([loadBills(), loadDashboard()]);
  } catch (error) {
    toast(error.message);
  }
}

async function deleteEditingTransaction() {
  if (!state.editing || !window.confirm("确认删除这条账单？删除后仍可撤销。")) return;
  try {
    await request(`/api/transactions/${state.editing.id}`, { method: "DELETE" });
    $("edit-dialog").close();
    toast("账单已删除，可使用撤销恢复");
    await Promise.all([loadBills(), loadDashboard()]);
  } catch (error) {
    toast(error.message);
  }
}

async function undoLastAction() {
  try {
    const preview = await request("/api/undo/preview");
    if (!window.confirm(preview.message)) return;
    const data = await request("/api/undo", { method: "POST" });
    const count = Array.isArray(data.ids) ? data.ids.length : 1;
    toast(count > 1
      ? `已整批撤销 ${count} 笔账单（批次 ${String(data.batch_id).slice(0, 8)}）`
      : "已撤销最近一次账单操作");
    await Promise.all([loadBills(), loadDashboard()]);
  } catch (error) {
    toast(error.message);
  }
}

async function loadBudgets() {
  try {
    const data = await request(`/api/budgets?month=${encodeURIComponent($("month").value)}`);
    $("budgets-empty").hidden = data.budgets.length > 0;
    $("budget-list").innerHTML = data.budgets.map((budget) => {
      const ratio = budget.amount ? budget.spent / budget.amount : 0;
      return `
        <div class="budget-row">
          <div class="budget-name"><strong>${escapeHtml(budget.category)}</strong><span>已用 ${money(budget.spent)} / ${money(budget.amount)}</span></div>
          <div class="budget-track"><div class="budget-fill ${ratio > 1 ? "over" : ""}" style="width:${Math.min(100, ratio * 100)}%"></div></div>
          <div class="budget-amount">${budget.remaining >= 0 ? "剩余" : "超出"} ${money(Math.abs(budget.remaining))}</div>
          <button class="row-action" type="button" data-delete-budget="${escapeHtml(budget.category)}" title="删除预算" aria-label="删除预算">×</button>
        </div>`;
    }).join("");
  } catch (error) {
    toast(error.message);
  }
}

async function saveBudget(event) {
  event.preventDefault();
  const category = $("budget-category").value.trim();
  const amount = Number($("budget-amount").value);
  try {
    await request(`/api/budgets/${encodeURIComponent(category)}`, {
      method: "PUT",
      body: JSON.stringify({ month: $("month").value, amount }),
    });
    event.target.reset();
    toast("预算已保存");
    await Promise.all([loadBudgets(), loadDashboard()]);
  } catch (error) {
    toast(error.message);
  }
}

async function deleteBudget(category) {
  if (!window.confirm(`确认删除 ${category} 预算？`)) return;
  try {
    await request(`/api/budgets/${encodeURIComponent(category)}?month=${encodeURIComponent($("month").value)}`, { method: "DELETE" });
    toast("预算已删除");
    await loadBudgets();
  } catch (error) {
    toast(error.message);
  }
}

function cycleLabel(months) {
  return ({ 1: "每月", 3: "每季", 6: "每半年", 12: "每年" })[Number(months)] || `${months} 个月`;
}

function subscriptionStatusLabel(status) {
  return ({ overdue: "已逾期", due: "今日扣款", upcoming: "等待扣款", paused: "已停用" })[status] || "待确认";
}

function liabilityKindLabel(kind) {
  return ({ credit_card: "信用卡", consumer_credit: "消费信贷", installment: "分期", other: "其他" })[kind] || "其他";
}

function liabilityStatusLabel(status) {
  return ({ overdue: "已逾期", due: "本月应还", upcoming: "尚未到期", settled: "本月已结清", no_due_date: "无固定日期", no_statement: "本月无账单", carried_forward: "历史结转待还" })[status] || "待确认";
}

async function loadSubscriptions() {
  try {
    const data = await request(`/api/subscriptions?month=${encodeURIComponent($("month").value)}&include_inactive=true`);
    state.subscriptions = data.items || [];
    const summary = data.summary || {};
    $("subscription-summary").innerHTML = `
      <div><span>本月预计扣款</span><strong>${money(summary.scheduled_amount)}</strong></div>
      <div><span>本月到期</span><strong>${Number(summary.due_count || 0)} 项</strong></div>
      <div><span>已逾期</span><strong>${Number(summary.overdue_count || 0)} 项 · ${money(summary.overdue_amount)}</strong></div>
      <div><span>启用中</span><strong>${Number(summary.active_count || 0)} 项</strong></div>`;
    $("subscriptions-empty").hidden = state.subscriptions.length > 0;
    $("subscription-list").innerHTML = state.subscriptions.map((item) => `
      <div class="management-row">
        <div class="management-main"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.category)} · ${escapeHtml(item.account || "未指定")} · ${cycleLabel(item.cycle_months)}</span></div>
        <div class="management-meta"><span>下次扣款</span><strong>${escapeHtml(item.next_charge_date)}</strong><span class="status-text ${escapeHtml(item.charge_status)}">${subscriptionStatusLabel(item.charge_status)}</span></div>
        <div class="management-meta"><span>金额</span><strong>${money(item.amount)}</strong><span>${item.last_charge_date ? `上次 ${escapeHtml(item.last_charge_date)} · 已扣 ${Number(item.charge_count || 0)} 次` : "尚无扣款记录"}</span></div>
        <div class="management-actions">
          ${["due", "overdue"].includes(item.charge_status) ? `<button class="button primary" type="button" data-charge-subscription="${escapeHtml(item.id)}">确认已扣款</button>` : ""}
          ${item.is_active ? `<button class="button" type="button" data-skip-subscription="${escapeHtml(item.id)}">跳过本期</button>` : ""}
          <button class="button" type="button" data-edit-subscription="${escapeHtml(item.id)}">编辑</button>
        </div>
      </div>`).join("");
  } catch (error) {
    toast(error.message);
  }
}

async function saveSubscription(event) {
  event.preventDefault();
  const payload = {
    name: $("subscription-name").value.trim(),
    amount: Number($("subscription-amount").value),
    cycle_months: Number($("subscription-cycle").value),
    next_charge_date: $("subscription-next-date").value,
    category: $("subscription-category").value.trim() || "其他",
    account: $("subscription-account").value.trim() || "未指定",
    note: $("subscription-note").value.trim(),
  };
  try {
    await request("/api/subscriptions", { method: "POST", body: JSON.stringify(payload) });
    event.target.reset();
    $("subscription-cycle").value = "1";
    $("subscription-next-date").value = new Date().toISOString().slice(0, 10);
    $("subscription-category").value = "其他";
    toast("订阅已新增");
    await loadSubscriptions();
  } catch (error) {
    toast(error.message);
  }
}

function openSubscriptionEditor(id) {
  const item = state.subscriptions.find((value) => value.id === id);
  if (!item) return;
  $("subscription-edit-id").value = item.id;
  $("subscription-edit-name").value = item.name;
  $("subscription-edit-amount").value = item.amount;
  $("subscription-edit-cycle").value = item.cycle_months;
  $("subscription-edit-date").value = item.next_charge_date;
  $("subscription-edit-category").value = item.category;
  $("subscription-edit-account").value = editableValue(item.account);
  $("subscription-edit-note").value = item.note || "";
  $("subscription-edit-active").checked = Boolean(item.is_active);
  $("subscription-dialog").showModal();
}

async function saveSubscriptionEdit(event) {
  event.preventDefault();
  const id = $("subscription-edit-id").value;
  const payload = {
    name: $("subscription-edit-name").value.trim(),
    amount: Number($("subscription-edit-amount").value),
    cycle_months: Number($("subscription-edit-cycle").value),
    next_charge_date: $("subscription-edit-date").value,
    category: $("subscription-edit-category").value.trim() || "其他",
    account: $("subscription-edit-account").value.trim() || "未指定",
    note: $("subscription-edit-note").value.trim(),
    is_active: $("subscription-edit-active").checked,
  };
  try {
    await request(`/api/subscriptions/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload) });
    $("subscription-dialog").close();
    toast("订阅已更新");
    await loadSubscriptions();
  } catch (error) {
    toast(error.message);
  }
}

async function chargeSubscription(id) {
  const item = state.subscriptions.find((value) => value.id === id);
  if (!item || !window.confirm(`确认 ${item.next_charge_date} 已扣 ${money(item.amount)} 的“${item.name}”吗？\n\n将写入一笔支出账单，并推进下次扣款日。`)) return;
  try {
    await request(`/api/subscriptions/${encodeURIComponent(id)}/charge`, { method: "POST" });
    toast("已写入订阅扣款账单");
    await Promise.all([loadSubscriptions(), loadDashboard(), loadBills()]);
  } catch (error) {
    if (error.status === 409) toast("检测到相同账单，未重复写入");
    else toast(error.message);
  }
}

async function skipSubscription(id) {
  const item = state.subscriptions.find((value) => value.id === id);
  if (!item || !window.confirm(`跳过“${item.name}”在 ${item.next_charge_date} 的扣款吗？\n\n不会写入支出，下次扣款日会按周期向后推进。`)) return;
  try {
    await request(`/api/subscriptions/${encodeURIComponent(id)}/skip`, {
      method: "POST",
      body: JSON.stringify({ expected_date: item.next_charge_date }),
    });
    toast("已跳过本期扣款");
    await Promise.all([loadSubscriptions(), loadDashboard()]);
  } catch (error) {
    toast(error.message);
  }
}

async function loadLiabilities() {
  try {
    const data = await request(`/api/liabilities?month=${encodeURIComponent($("month").value)}`);
    state.liabilities = data.items || [];
    state.liabilityAccounts = data.accounts || [];
    const selectedAccount = $("liability-existing").value;
    $("liability-existing").innerHTML = `<option value="">新建账户</option>${state.liabilityAccounts.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}${item.is_active ? "" : "（已停用）"}</option>`).join("")}`;
    if (state.liabilityAccounts.some((item) => item.id === selectedAccount)) $("liability-existing").value = selectedAccount;
    const summary = data.summary || {};
    const monthSelector = (data.available_months || []).length
      ? `<div class="management-months segmented" aria-label="待还账单月份">${data.available_months.map((month) => `<button class="segment ${month === data.month ? "active" : ""}" type="button" data-liability-month="${escapeHtml(month)}">${escapeHtml(month.replace("-", "年"))}月</button>`).join("")}</div>`
      : "";
    $("liability-summary").innerHTML = `
      <div><span>本月应还</span><strong>${money(summary.due_amount)}</strong></div>
      <div><span>本月已还</span><strong>${money(summary.paid_amount)}</strong></div>
      <div><span>本月未还</span><strong>${money(summary.remaining_amount)}</strong></div>
      <div><span>已逾期</span><strong>${money(summary.overdue_amount)}</strong></div>
      <div><span>账单数</span><strong>${Number(summary.due_count || 0)} 项</strong></div>
      ${Number(summary.carried_count || 0) ? `<div><span>历史结转待还</span><strong>${money(summary.carried_remaining_amount)} · ${Number(summary.carried_count)} 项</strong></div>` : ""}
      ${monthSelector}`;
    $("liabilities-empty").hidden = state.liabilities.length > 0;
    $("liability-list").innerHTML = state.liabilities.map((item) => {
      const isCarried = Boolean(item.is_carried_forward);
      const detailKey = `${item.id}:${item.statement_month}`;
      const isExpanded = state.expandedLiabilityCharges.has(detailKey);
      const paymentCount = Number(item.payment_count || 0);
      const paymentDateLabel = paymentCount > 1 ? "最近还款" : "还款日期";
      const paymentDate = item.latest_payment_date
        ? `<span>${paymentDateLabel} ${escapeHtml(item.latest_payment_date)} · ${money(item.latest_payment_amount)}</span>`
        : "";
      const charges = Array.isArray(item.charges) ? item.charges : [];
      const chargeRows = charges.map((charge) => `
        <div class="liability-charge-row">
          <span>${escapeHtml(charge.charged_at)} · ${escapeHtml(charge.category || "待分类")} · ${escapeHtml(charge.merchant || "未说明")}</span>
          <div class="liability-charge-actions"><strong>${money(charge.amount)}</strong><button class="icon-button" type="button" data-edit-liability-charge="${escapeHtml(charge.id)}" aria-label="编辑消费" title="编辑消费">&#9998;</button></div>
        </div>`).join("");
      const historicalRow = Number(item.unitemized_amount || 0) > 0
        ? `<div class="liability-charge-row historical"><span>未说明的历史消费</span><strong>${money(item.unitemized_amount)}</strong></div>`
        : "";
      const chargeDetails = chargeRows || historicalRow
        ? `${chargeRows}${historicalRow}`
        : '<div class="liability-charge-empty">暂无已登记消费</div>';
      return `
      <div class="liability-entry" data-liability-row="${escapeHtml(item.id)}" data-statement-month="${escapeHtml(item.statement_month)}">
        <div class="management-row">
          <div class="management-main"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.provider || liabilityKindLabel(item.kind))} · ${liabilityKindLabel(item.kind)} · ${escapeHtml(item.repayment_account || "未指定")}</span></div>
          <div class="management-meta"><span>${isCarried ? "历史结转 / 原账单" : "本月应还 / 日期"}</span><strong>${money(item.due_amount)} · ${isCarried ? escapeHtml(item.carried_from_month) : (item.due_date ? escapeHtml(item.due_date) : "无固定日期")}</strong><span class="status-text ${escapeHtml(item.payment_status)}">${liabilityStatusLabel(item.payment_status)}</span></div>
          <div class="management-meta"><span>${isCarried ? "结转未还" : "本月未还"}</span><strong>${money(item.remaining_amount)}</strong><span>已还 ${money(item.paid_amount)} · ${paymentCount} 次${Number(item.minimum_payment) ? ` · 最低还 ${money(item.minimum_payment)}` : ""}</span>${paymentDate}</div>
          <div class="management-actions">
            <button class="button" type="button" data-toggle-liability-charges="${escapeHtml(detailKey)}">${isExpanded ? "收起消费" : "消费明细"}</button>
            ${Number(item.remaining_amount) > 0 ? `<button class="button primary" type="button" data-pay-liability="${escapeHtml(item.id)}" data-statement-month="${escapeHtml(item.statement_month)}">登记还款</button>` : ""}
            <button class="button" type="button" data-edit-liability="${escapeHtml(item.id)}" data-statement-month="${escapeHtml(item.statement_month)}">编辑</button>
          </div>
        </div>
        ${isExpanded ? `<div class="liability-charge-list">${chargeDetails}</div>` : ""}
      </div>`;
    }).join("");
  } catch (error) {
    toast(error.message);
  }
}

async function loadLiabilityAccounts() {
  try {
    const data = await request(`/api/liabilities?month=${encodeURIComponent($("month").value)}`);
    state.liabilityAccounts = data.accounts || [];
  } catch (error) {
    toast(error.message);
  }
}

async function saveLiability(event) {
  event.preventDefault();
  const limit = $("liability-limit").value;
  const payload = {
    name: $("liability-name").value.trim(),
    provider: $("liability-provider").value.trim(),
    kind: $("liability-kind").value,
    statement_day: Number($("liability-statement-day").value || 0),
    statement_month: $("liability-statement-month").value,
    due_amount: Number($("liability-due-amount").value),
    due_date: $("liability-due-date").value,
    minimum_payment: Number($("liability-minimum").value),
    repayment_account: $("liability-account").value.trim() || "未指定",
    credit_limit: limit === "" ? null : Number(limit),
    note: $("liability-note").value.trim(),
  };
  const existingId = $("liability-existing").value;
  if (existingId) payload.is_active = true;
  try {
    const url = existingId ? `/api/liabilities/${encodeURIComponent(existingId)}` : "/api/liabilities";
    await request(url, { method: existingId ? "PATCH" : "POST", body: JSON.stringify(payload) });
    event.target.reset();
    $("liability-existing").value = "";
    $("liability-due-amount").value = "";
    $("liability-minimum").value = "";
    $("liability-statement-day").value = "";
    $("liability-due-date").value = "";
    $("liability-statement-month").value = $("month").value;
    $("liability-submit").textContent = "新增账户并保存账单";
    toast(existingId ? "账单已保存" : "待还账户和账单已新增");
    await Promise.all([loadLiabilities(), loadDashboard()]);
  } catch (error) {
    toast(error.message);
  }
}

function selectLiabilityAccount() {
  const id = $("liability-existing").value;
  const account = state.liabilityAccounts.find((item) => item.id === id);
  const statement = state.liabilities.find((item) => item.id === id);
  if (!account) {
    $("liability-name").value = "";
    $("liability-provider").value = "";
    $("liability-kind").value = "consumer_credit";
    $("liability-statement-day").value = "";
    $("liability-statement-month").value = $("month").value;
    $("liability-due-amount").value = "";
    $("liability-due-date").value = "";
    $("liability-minimum").value = "";
    $("liability-account").value = "";
    $("liability-limit").value = "";
    $("liability-note").value = "";
    $("liability-submit").textContent = "新增账户并保存账单";
    return;
  }
  $("liability-name").value = account.name;
  $("liability-provider").value = account.provider || "";
  $("liability-kind").value = account.kind;
  $("liability-statement-day").value = account.statement_day || "";
  $("liability-statement-month").value = $("month").value;
  $("liability-account").value = editableValue(account.repayment_account);
  $("liability-limit").value = account.credit_limit ?? "";
  $("liability-note").value = account.note || "";
  $("liability-due-amount").value = statement?.due_amount ?? "";
  $("liability-due-date").value = statement?.due_date || "";
  $("liability-minimum").value = statement?.minimum_payment ?? "";
  $("liability-submit").textContent = statement ? "更新账单" : "新增账单";
}

function alignDueDateWithStatementMonth(dateInputId, statementMonthId) {
  const dateInput = $(dateInputId);
  const statementMonth = $(statementMonthId).value;
  if (!/^\d{4}-\d{2}$/.test(statementMonth)) return;
  const currentDay = /^\d{4}-\d{2}-(\d{2})$/.exec(dateInput.value)?.[1] || "01";
  const [year, month] = statementMonth.split("-").map(Number);
  const lastDay = new Date(year, month, 0).getDate();
  dateInput.value = `${statementMonth}-${String(Math.min(Number(currentDay), lastDay)).padStart(2, "0")}`;
}

function prepareLiabilityDueDatePicker(dateInputId, statementMonthId) {
  const dateInput = $(dateInputId);
  if (!dateInput.value) alignDueDateWithStatementMonth(dateInputId, statementMonthId);
}

async function loadLiabilityStatementForForm() {
  const id = $("liability-existing").value;
  const statementMonth = $("liability-statement-month").value;
  if (!statementMonth) return;
  if (!id) {
    alignDueDateWithStatementMonth("liability-due-date", "liability-statement-month");
    return;
  }
  try {
    const data = await request(`/api/liabilities?month=${encodeURIComponent(statementMonth)}`);
    const statement = (data.items || []).find((item) => item.id === id && !item.is_carried_forward);
    $("liability-due-amount").value = statement?.due_amount ?? "";
    $("liability-due-date").value = statement?.due_date || "";
    $("liability-minimum").value = statement?.minimum_payment ?? "";
    if (!statement) alignDueDateWithStatementMonth("liability-due-date", "liability-statement-month");
    $("liability-submit").textContent = statement ? "更新账单" : "新增账单";
  } catch (error) {
    toast(error.message);
  }
}

function openLiabilityEditor(id, statementMonth = "") {
  const item = state.liabilities.find((value) => value.id === id && (!statementMonth || value.statement_month === statementMonth));
  if (!item) return;
  $("liability-edit-id").value = item.id;
  $("liability-edit-source-statement-month").value = item.statement_month;
  $("liability-edit-name").value = item.name;
  $("liability-edit-provider").value = item.provider || "";
  $("liability-edit-kind").value = item.kind;
  $("liability-edit-statement-day").value = item.statement_day || "";
  $("liability-edit-statement-month").value = item.statement_month;
  $("liability-edit-due-amount").value = item.due_amount;
  $("liability-edit-due-date").value = item.due_date;
  $("liability-edit-minimum").value = item.minimum_payment;
  $("liability-edit-account").value = editableValue(item.repayment_account);
  $("liability-edit-limit").value = item.credit_limit ?? "";
  $("liability-edit-note").value = item.note || "";
  $("liability-edit-active").checked = Boolean(item.is_active);
  $("liability-dialog").showModal();
}

async function saveLiabilityEdit(event) {
  event.preventDefault();
  const id = $("liability-edit-id").value;
  const limit = $("liability-edit-limit").value;
  const payload = {
    name: $("liability-edit-name").value.trim(),
    provider: $("liability-edit-provider").value.trim(),
    kind: $("liability-edit-kind").value,
    statement_day: Number($("liability-edit-statement-day").value || 0),
    statement_month: $("liability-edit-statement-month").value,
    source_statement_month: $("liability-edit-source-statement-month").value,
    due_amount: Number($("liability-edit-due-amount").value),
    due_date: $("liability-edit-due-date").value,
    minimum_payment: Number($("liability-edit-minimum").value),
    repayment_account: $("liability-edit-account").value.trim() || "未指定",
    credit_limit: limit === "" ? null : Number(limit),
    note: $("liability-edit-note").value.trim(),
    is_active: $("liability-edit-active").checked,
  };
  try {
    await request(`/api/liabilities/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload) });
    $("liability-dialog").close();
    toast("待还项目已更新");
    await Promise.all([loadLiabilities(), loadDashboard()]);
  } catch (error) {
    toast(error.message);
  }
}

function openPaymentDialog(id, statementMonth = "") {
  const item = state.liabilities.find((value) => value.id === id && (!statementMonth || value.statement_month === statementMonth));
  if (!item) return;
  $("payment-id").value = "";
  $("payment-liability-id").value = item.id;
  $("payment-statement-month").value = item.statement_month;
  $("payment-dialog-title").textContent = "登记还款";
  $("payment-note").textContent = `${item.name} 本月应还 ${money(item.due_amount)}，本月未还 ${money(item.remaining_amount)}。还款会减少本月未还并扣减可用本金，但不会新增消费支出。`;
  $("payment-amount").value = item.remaining_amount;
  $("payment-date").value = new Date().toISOString().slice(0, 10);
  $("payment-account").value = editableValue(item.repayment_account);
  $("payment-comment").value = "";
  $("delete-payment").hidden = true;
  $("save-payment").textContent = "确认还款";
  $("payment-dialog").showModal();
}

function findLiabilityCharge(chargeId) {
  for (const liability of state.liabilities) {
    const charge = (liability.charges || []).find((item) => item.id === chargeId);
    if (charge) return { charge, liability };
  }
  return null;
}

function openLiabilityChargeEditor(chargeId) {
  const found = findLiabilityCharge(chargeId);
  if (!found) return;
  const { charge, liability } = found;
  $("liability-charge-id").value = charge.id;
  $("liability-charge-note").textContent = `${liability.name} · 当前归属 ${charge.statement_month} 账单。修改金额或归属月份会同步调整对应账单的应还、未还和当前负债。`;
  $("liability-charge-date").value = charge.charged_at;
  $("liability-charge-month").value = charge.statement_month;
  $("liability-charge-amount").value = Number(charge.amount).toFixed(2);
  $("liability-charge-category").value = charge.category || "待分类";
  $("liability-charge-merchant").value = charge.merchant || "";
  $("liability-charge-comment").value = charge.note || "";
  $("liability-charge-dialog").showModal();
}

async function saveLiabilityChargeEdit(event) {
  event.preventDefault();
  const chargeId = $("liability-charge-id").value;
  const payload = {
    charged_at: $("liability-charge-date").value,
    statement_month: $("liability-charge-month").value,
    amount: Number($("liability-charge-amount").value),
    category: $("liability-charge-category").value.trim(),
    merchant: $("liability-charge-merchant").value.trim(),
    note: $("liability-charge-comment").value.trim(),
  };
  try {
    await request(`/api/liability-charges/${encodeURIComponent(chargeId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    $("liability-charge-dialog").close();
    toast("信用消费已更新");
    await Promise.all([loadLiabilities(), loadDashboard(), loadBills(), loadLogs()]);
  } catch (error) {
    toast(error.message);
  }
}

function openPaymentEditor(paymentId) {
  const payment = state.transactions.find(
    (item) => item.record_type === "liability_payment" && item.id === paymentId
  );
  if (!payment) return;
  $("payment-id").value = payment.id;
  $("payment-liability-id").value = payment.liability_id;
  $("payment-statement-month").value = payment.statement_month;
  $("payment-dialog-title").textContent = "编辑还款";
  $("payment-note").textContent = `${payment.merchant} · 归属 ${payment.statement_month} 账单。修改后会重新计算待还、本金和现金变化。`;
  $("payment-amount").value = payment.amount;
  $("payment-date").value = payment.date;
  $("payment-account").value = editableValue(payment.account);
  $("payment-comment").value = payment.note || "";
  $("delete-payment").hidden = false;
  $("save-payment").textContent = "保存修改";
  $("payment-dialog").showModal();
}

async function saveLiabilityPayment(event) {
  event.preventDefault();
  const paymentId = $("payment-id").value;
  const id = $("payment-liability-id").value;
  const payload = {
    amount: Number($("payment-amount").value),
    paid_at: $("payment-date").value,
    account: $("payment-account").value.trim() || "未指定",
    note: $("payment-comment").value.trim(),
  };
  if (!paymentId) payload.statement_month = $("payment-statement-month").value;
  try {
    const url = paymentId
      ? `/api/liability-payments/${encodeURIComponent(paymentId)}`
      : `/api/liabilities/${encodeURIComponent(id)}/payments`;
    await request(url, {
      method: paymentId ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    });
    $("payment-dialog").close();
    toast(paymentId ? "还款记录已更新" : "还款已登记，不会重复计入支出");
    await Promise.all([loadLiabilities(), loadDashboard(), loadBills(), loadLogs()]);
  } catch (error) {
    toast(error.message);
  }
}

async function deletePayment() {
  const paymentId = $("payment-id").value;
  if (!paymentId || !window.confirm("确认撤销这笔还款？原账单未还金额和本金会同步恢复。")) return;
  try {
    await request(`/api/liability-payments/${encodeURIComponent(paymentId)}`, { method: "DELETE" });
    $("payment-dialog").close();
    toast("还款已撤销");
    await Promise.all([loadLiabilities(), loadDashboard(), loadBills(), loadLogs()]);
  } catch (error) {
    toast(error.message);
  }
}

async function reverseSubscriptionCharge(transactionId) {
  const item = state.transactions.find(
    (record) => record.record_type === "transaction" && Number(record.id) === Number(transactionId)
  );
  if (!item || !window.confirm(`确认撤销“${item.merchant}”在 ${item.date} 的订阅扣款 ${money(item.amount)}？\n\n支出记录和订阅的下次扣款日会一起回退。`)) return;
  try {
    await request(`/api/subscription-charges/${Number(transactionId)}`, { method: "DELETE" });
    toast("订阅扣款已撤销");
    await Promise.all([loadSubscriptions(), loadDashboard(), loadBills(), loadLogs()]);
  } catch (error) {
    toast(error.message);
  }
}

function referenceLabel(kind) {
  return kind === "category" ? "分类" : "支付方式";
}

function renderReferenceList(kind, items) {
  const target = kind === "category" ? $("category-list") : $("payment-method-list");
  const usageLabel = kind === "payment_method" ? "笔资金记录" : "笔账单";
  target.innerHTML = items.map((item) => `
    <div class="reference-row">
      <button class="favorite-button ${item.is_favorite ? "active" : ""}" type="button" data-favorite-reference="${kind}" data-name="${escapeHtml(item.name)}" title="${item.is_favorite ? "取消常用" : "设为常用"}" aria-label="${item.is_favorite ? "取消常用" : "设为常用"}">★</button>
      <div class="reference-main"><strong>${escapeHtml(item.name)}</strong><span>${item.aliases.length ? `别名：${escapeHtml(item.aliases.join("、"))}` : "无别名"} · ${item.usage_count} ${usageLabel}</span></div>
      <button class="button" type="button" data-edit-reference="${kind}" data-name="${escapeHtml(item.name)}">编辑</button>
      <button class="button" type="button" data-merge-reference="${kind}" data-name="${escapeHtml(item.name)}">合并</button>
    </div>`).join("");
}

function refreshReferenceDatalists() {
  $("category-values").innerHTML = state.references.category.map((item) => `<option value="${escapeHtml(item.name)}"></option>`).join("");
  $("payment-methods").innerHTML = state.references.payment_method.map((item) => `<option value="${escapeHtml(item.name)}"></option>`).join("");
}

async function loadReferences() {
  const [categories, methods] = await Promise.all([
    request("/api/references/category"),
    request("/api/references/payment_method"),
  ]);
  state.references.category = categories.items;
  state.references.payment_method = methods.items;
  renderReferenceList("category", categories.items);
  renderReferenceList("payment_method", methods.items);
  refreshReferenceDatalists();
}

async function loadBackups() {
  const data = await request("/api/backups");
  $("backup-list").innerHTML = data.backups.length ? data.backups.map((item) => `
    <div class="backup-row">
      <div><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(formatLogTime(item.created_at))} · ${(item.size / 1024).toFixed(1)} KB</span></div>
      <a class="button link-button" href="/api/backups/${encodeURIComponent(item.name)}/download">下载</a>
      <button class="button danger" type="button" data-restore-backup="${escapeHtml(item.name)}">恢复</button>
    </div>`).join("") : '<div class="table-empty">还没有账本备份</div>';
}

async function loadClassificationStatus() {
  const data = await request("/api/transactions?category=%E5%BE%85%E5%88%86%E7%B1%BB&limit=200");
  const count = data.results.length;
  $("classification-status").textContent = count
    ? `${count} 笔账单等待分类，高置信度结果会自动更新`
    : "没有待分类账单";
  $("classify-pending").disabled = count === 0;
}

function renderReminderSettings(data) {
  $("reminder-enabled").checked = Boolean(data.enabled);
  $("reminder-time").value = data.time || "22:00";
  const skipButton = $("skip-reminder-today");
  if (!data.enabled) {
    $("reminder-status").textContent = "每日提醒已关闭";
  } else if (data.skipped_today) {
    $("reminder-status").textContent = "今天已跳过，明天会按设定时间恢复提醒";
  } else if (data.sent_today) {
    $("reminder-status").textContent = "今天已提醒，明天会按设定时间再次提醒";
  } else {
    $("reminder-status").textContent = `今天将在 ${data.time} 提醒记账`;
  }
  skipButton.textContent = data.skipped_today ? "恢复今日提醒" : "今天不提醒";
  skipButton.disabled = !data.enabled || data.sent_today;
}

async function loadReminderSettings() {
  renderReminderSettings(await request("/api/settings/reminder"));
}

async function openReminderSettings() {
  try {
    await loadReminderSettings();
    $("reminder-dialog").showModal();
  } catch (error) {
    toast(error.message);
  }
}

async function saveReminderSettings(event) {
  event.preventDefault();
  const button = $("save-reminder");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "保存中…";
  try {
    const data = await request("/api/settings/reminder", {
      method: "PUT",
      body: JSON.stringify({
        enabled: $("reminder-enabled").checked,
        time: $("reminder-time").value,
      }),
    });
    renderReminderSettings(data);
    toast("每日提醒已保存");
    $("reminder-dialog").close();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function toggleReminderToday() {
  const skip = $("skip-reminder-today").textContent === "今天不提醒";
  try {
    const data = await request("/api/settings/reminder/today", {
      method: "PUT",
      body: JSON.stringify({ skip }),
    });
    renderReminderSettings(data);
    toast(skip ? "今天不会再弹出记账提醒" : "今天的记账提醒已恢复");
  } catch (error) {
    toast(error.message);
  }
}

async function loadSettings() {
  try {
    await Promise.all([loadReferences(), loadBackups(), loadClassificationStatus(), loadReminderSettings()]);
  } catch (error) { toast(error.message); }
}

async function classifyPendingTransactions() {
  const button = $("classify-pending");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "分类中…";
  try {
    const data = await request("/api/transactions/classify-pending", {
      method: "POST", body: JSON.stringify({ limit: 50 }),
    });
    toast(`已分类 ${data.classified} 笔，${data.needs_review} 笔仍需确认`);
    await Promise.all([loadClassificationStatus(), loadDashboard(), loadBills(), loadLogs()]);
  } catch (error) {
    toast(error.message);
  } finally {
    button.textContent = original;
    if ($("classification-status").textContent !== "没有待分类账单") button.disabled = false;
  }
}

function openReferenceDialog(kind, name = "") {
  const item = state.references[kind].find((entry) => entry.name === name);
  $("reference-kind").value = kind;
  $("reference-original").value = name;
  $("reference-title").textContent = `${item ? "编辑" : "新增"}${referenceLabel(kind)}`;
  $("reference-name").value = item?.name || "";
  $("reference-aliases").value = item?.aliases.join("，") || "";
  $("reference-favorite").checked = Boolean(item?.is_favorite);
  $("reference-dialog").showModal();
}

async function saveReference(event) {
  event.preventDefault();
  const kind = $("reference-kind").value;
  const original = $("reference-original").value;
  const payload = {
    name: $("reference-name").value.trim(),
    aliases: $("reference-aliases").value.split(/[,，]/).map((value) => value.trim()).filter(Boolean),
    is_favorite: $("reference-favorite").checked,
  };
  try {
    if (original) {
      await request(`/api/references/${kind}/${encodeURIComponent(original)}`, {
        method: "PATCH",
        body: JSON.stringify({ new_name: payload.name, aliases: payload.aliases, is_favorite: payload.is_favorite }),
      });
    } else {
      await request(`/api/references/${kind}`, { method: "POST", body: JSON.stringify(payload) });
    }
    $("reference-dialog").close();
    toast(`${referenceLabel(kind)}已保存`);
    await Promise.all([loadReferences(), loadDashboard(), loadBudgets()]);
  } catch (error) { toast(error.message); }
}

async function toggleFavorite(kind, name) {
  const item = state.references[kind].find((entry) => entry.name === name);
  if (!item) return;
  try {
    await request(`/api/references/${kind}/${encodeURIComponent(name)}`, {
      method: "PATCH", body: JSON.stringify({ is_favorite: !item.is_favorite }),
    });
    await loadReferences();
  } catch (error) { toast(error.message); }
}

function openMergeDialog(kind, source) {
  const targets = state.references[kind].filter((item) => item.name !== source);
  if (!targets.length) return toast("没有可合并到的目标名称");
  $("merge-kind").value = kind;
  $("merge-source").value = source;
  $("merge-note").textContent = `“${source}”的历史账单、别名${kind === "category" ? "和预算" : ""}会合并到目标名称。`;
  $("merge-target").innerHTML = targets.map((item) => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`).join("");
  $("merge-dialog").showModal();
}

async function mergeReference(event) {
  event.preventDefault();
  const kind = $("merge-kind").value;
  const source = $("merge-source").value;
  const target = $("merge-target").value;
  if (!window.confirm(`确认把“${source}”合并到“${target}”？`)) return;
  try {
    const data = await request(`/api/references/${kind}/merge`, {
      method: "POST", body: JSON.stringify({ source, target }),
    });
    $("merge-dialog").close();
    toast(`已合并，更新 ${data.merged.affected} 笔账单`);
    await Promise.all([loadSettings(), loadDashboard(), loadBills(), loadBudgets()]);
  } catch (error) { toast(error.message); }
}

async function createLedgerBackup() {
  try {
    const data = await request("/api/backups", { method: "POST" });
    await loadBackups();
    toast("备份已创建，正在下载");
    window.location.assign(`/api/backups/${encodeURIComponent(data.backup.name)}/download`);
  } catch (error) { toast(error.message); }
}

async function restoreLedgerBackup(name) {
  const confirmation = window.prompt(`恢复 ${name} 会覆盖当前账本。请输入“恢复账本”继续：`);
  if (confirmation !== "恢复账本") return;
  try {
    await request(`/api/backups/${encodeURIComponent(name)}/restore`, {
      method: "POST", body: JSON.stringify({ confirmation }),
    });
    toast("账本已恢复");
    await Promise.all([loadSettings(), loadDashboard(), loadBills(), loadBudgets(), loadLogs()]);
  } catch (error) { toast(error.message); }
}

let searchTimer;
document.querySelectorAll(".nav-button").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
$("composer").addEventListener("submit", submitChat);
$("clear-chat").addEventListener("click", clearChatHistory);
$("receipt-images").addEventListener("change", selectReceiptImages);
$("record-audio").addEventListener("click", toggleAudioRecording);
$("image-previews").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-image]");
  if (button) removeSelectedImage(Number(button.dataset.removeImage));
});
$("composer").addEventListener("dragenter", handleComposerDragEnter);
$("composer").addEventListener("dragover", handleComposerDragOver);
$("composer").addEventListener("dragleave", handleComposerDragLeave);
$("composer").addEventListener("drop", handleComposerDrop);
$("subscription-form").addEventListener("submit", saveSubscription);
$("subscription-edit-form").addEventListener("submit", saveSubscriptionEdit);
$("subscription-list").addEventListener("click", (event) => {
  const charge = event.target.closest("[data-charge-subscription]");
  const skip = event.target.closest("[data-skip-subscription]");
  const edit = event.target.closest("[data-edit-subscription]");
  if (charge) chargeSubscription(charge.dataset.chargeSubscription);
  if (skip) skipSubscription(skip.dataset.skipSubscription);
  if (edit) openSubscriptionEditor(edit.dataset.editSubscription);
});
$("close-subscription").addEventListener("click", () => $("subscription-dialog").close());
$("cancel-subscription").addEventListener("click", () => $("subscription-dialog").close());
$("liability-form").addEventListener("submit", saveLiability);
$("transfer-form").addEventListener("submit", saveTransfer);
$("reconcile-form").addEventListener("submit", saveReconciliation);
$("account-form").addEventListener("submit", saveAccount);
$("account-edit-form").addEventListener("submit", saveAccountEdit);
$("account-list").addEventListener("click", (event) => {
  const edit = event.target.closest("[data-edit-account]");
  if (edit) openAccountEditor(edit.dataset.editAccount);
});
$("delete-account").addEventListener("click", deleteAccount);
$("close-account").addEventListener("click", () => $("account-dialog").close());
$("cancel-account").addEventListener("click", () => $("account-dialog").close());
$("reconcile-account").addEventListener("change", () => {
  const account = state.accounts.find((item) => item.name === $("reconcile-account").value);
  if (account) $("reconcile-balance").value = Number(account.balance).toFixed(2);
});
$("liability-existing").addEventListener("change", selectLiabilityAccount);
$("liability-statement-month").addEventListener("change", loadLiabilityStatementForForm);
$("liability-edit-statement-month").addEventListener("change", () => {
  alignDueDateWithStatementMonth("liability-edit-due-date", "liability-edit-statement-month");
});
$("liability-due-date").addEventListener("pointerdown", () => {
  prepareLiabilityDueDatePicker("liability-due-date", "liability-statement-month");
});
$("liability-edit-due-date").addEventListener("pointerdown", () => {
  prepareLiabilityDueDatePicker("liability-edit-due-date", "liability-edit-statement-month");
});
$("liability-edit-form").addEventListener("submit", saveLiabilityEdit);
$("liability-list").addEventListener("click", (event) => {
  const payment = event.target.closest("[data-pay-liability]");
  const edit = event.target.closest("[data-edit-liability]");
  const chargeEdit = event.target.closest("[data-edit-liability-charge]");
  const detail = event.target.closest("[data-toggle-liability-charges]");
  if (payment) openPaymentDialog(payment.dataset.payLiability, payment.dataset.statementMonth);
  if (edit) openLiabilityEditor(edit.dataset.editLiability, edit.dataset.statementMonth);
  if (chargeEdit) openLiabilityChargeEditor(chargeEdit.dataset.editLiabilityCharge);
  if (detail) {
    const key = detail.dataset.toggleLiabilityCharges;
    if (state.expandedLiabilityCharges.has(key)) state.expandedLiabilityCharges.delete(key);
    else state.expandedLiabilityCharges.add(key);
    loadLiabilities();
  }
});
$("liability-summary").addEventListener("click", async (event) => {
  const target = event.target.closest("[data-liability-month]");
  if (!target) return;
  $("month").value = target.dataset.liabilityMonth;
  await Promise.all([loadLiabilities(), loadDashboard()]);
});
$("close-liability").addEventListener("click", () => $("liability-dialog").close());
$("cancel-liability").addEventListener("click", () => $("liability-dialog").close());
$("payment-form").addEventListener("submit", saveLiabilityPayment);
$("delete-payment").addEventListener("click", deletePayment);
$("close-payment").addEventListener("click", () => $("payment-dialog").close());
$("cancel-payment").addEventListener("click", () => $("payment-dialog").close());
$("liability-charge-form").addEventListener("submit", saveLiabilityChargeEdit);
$("close-liability-charge").addEventListener("click", () => $("liability-charge-dialog").close());
$("cancel-liability-charge").addEventListener("click", () => $("liability-charge-dialog").close());
$("edit-capital").addEventListener("click", openCapitalEditor);
$("capital-form").addEventListener("submit", saveCapital);
$("close-capital").addEventListener("click", () => $("capital-dialog").close());
$("cancel-capital").addEventListener("click", () => $("capital-dialog").close());
$("model-settings").addEventListener("click", openModelSettings);
$("model-settings-mobile").addEventListener("click", openModelSettings);
$("model-provider").addEventListener("change", changeModelProvider);
$("model-preset").addEventListener("change", toggleCustomModel);
$("model-form").addEventListener("submit", saveModel);
$("close-model").addEventListener("click", () => $("model-dialog").close());
$("cancel-model").addEventListener("click", () => $("model-dialog").close());
$("cancel-request").addEventListener("click", () => {
  state.cancelReason = "manual";
  if (state.controller) {
    state.controller.abort();
    return;
  }
  clearTimeout(state.pendingPollTimer);
  document.querySelector(`.message.pending[data-request-id="${state.currentRequestId}"]`)?.remove();
  state.currentRequestId = "";
  setChatBusy(false);
  addMessage("已停止等待；刷新页面可以恢复后台进度。", "agent");
  processNextQueuedChat();
});
$("confirm-draft").addEventListener("click", confirmDrafts);
$("draft-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-draft]");
  const confirm = event.target.closest("[data-confirm-draft]");
  if (button) removeDraft(Number(button.dataset.removeDraft));
  if (confirm) confirmDrafts(Number(confirm.dataset.confirmDraft));
  const proposal = event.target.closest("[data-approve-proposal]");
  if (proposal) approveCategoryProposal(Number(proposal.dataset.approveProposal));
});
$("draft-list").addEventListener("change", (event) => {
  if (event.target.matches('[data-field="entry_type"]')) {
    state.drafts = collectDrafts();
    if (event.target.value === "credit" && !state.liabilityAccounts.length) {
      loadLiabilityAccounts().then(renderDrafts);
      return;
    }
    renderDrafts();
  }
});
$("management-draft-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-management-draft]");
  const confirm = event.target.closest("[data-confirm-management-draft]");
  if (button) void removeManagementDraft(Number(button.dataset.removeManagementDraft));
  if (confirm) confirmManagementDrafts(Number(confirm.dataset.confirmManagementDraft));
});
$("cancel-draft").addEventListener("click", async () => {
  if (state.draftRequestId) {
    try {
      await request(`/api/chat/requests/${encodeURIComponent(state.draftRequestId)}/dismiss`, { method: "POST" });
    } catch (error) {
      toast(error.message);
      return;
    }
  }
  state.drafts = [];
  state.managementProposals = [];
  state.draftRequestId = "";
  $("draft").hidden = true;
  addMessage("已取消这些待确认草稿。", "agent");
});
$("bill-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadBills, 250); });
$("bill-direction").addEventListener("change", loadBills);
document.querySelector(".financial-table thead").addEventListener("click", (event) => {
  const button = event.target.closest("[data-bill-sort]");
  if (!button) return;
  const key = button.dataset.billSort;
  state.billSort = {
    key,
    direction: state.billSort.key === key
      ? (state.billSort.direction === "asc" ? "desc" : "asc")
      : (key === "date" || key === "amount" ? "desc" : "asc"),
  };
  renderBills();
});
$("bill-rows").addEventListener("click", (event) => {
  const button = event.target.closest("[data-edit-id]");
  const payment = event.target.closest("[data-edit-payment]");
  const liability = event.target.closest("[data-open-liability]");
  const subscriptionCharge = event.target.closest("[data-reverse-subscription-charge]");
  if (button) openEditor(Number(button.dataset.editId));
  if (payment) openPaymentEditor(payment.dataset.editPayment);
  if (liability) openLiabilityFromRecord(liability.dataset.openLiability, liability.dataset.statementMonth);
  if (subscriptionCharge) reverseSubscriptionCharge(subscriptionCharge.dataset.reverseSubscriptionCharge);
});
$("edit-form").addEventListener("submit", saveEdit);
$("close-edit").addEventListener("click", () => $("edit-dialog").close());
$("cancel-edit").addEventListener("click", () => $("edit-dialog").close());
$("delete-transaction").addEventListener("click", deleteEditingTransaction);
$("undo").addEventListener("click", undoLastAction);
$("budget-form").addEventListener("submit", saveBudget);
$("budget-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-delete-budget]");
  if (button) deleteBudget(button.dataset.deleteBudget);
});
document.querySelectorAll("[data-log-kind]").forEach((button) => {
  button.addEventListener("click", () => switchLogKind(button.dataset.logKind));
});
$("log-filter").addEventListener("change", loadLogs);
$("refresh-logs").addEventListener("click", loadLogs);
document.querySelectorAll("[data-add-reference]").forEach((button) => {
  button.addEventListener("click", () => openReferenceDialog(button.dataset.addReference));
});
$("reference-form").addEventListener("submit", saveReference);
$("close-reference").addEventListener("click", () => $("reference-dialog").close());
$("cancel-reference").addEventListener("click", () => $("reference-dialog").close());
$("merge-form").addEventListener("submit", mergeReference);
$("close-merge").addEventListener("click", () => $("merge-dialog").close());
$("cancel-merge").addEventListener("click", () => $("merge-dialog").close());
$("create-backup").addEventListener("click", createLedgerBackup);
$("classify-pending").addEventListener("click", classifyPendingTransactions);
$("personal-memory-form").addEventListener("submit", createPersonalMemory);
$("personal-memory-list").addEventListener("click", (event) => {
  const save = event.target.closest("[data-save-personal-memory]");
  const remove = event.target.closest("[data-delete-personal-memory]");
  if (save) {
    const item = save.closest("[data-personal-memory-id]");
    void updatePersonalMemory(save.dataset.savePersonalMemory, {
      content: item.querySelector("[data-personal-memory-content]").value.trim(),
    });
  }
  if (remove) void deletePersonalMemory(remove.dataset.deletePersonalMemory);
});
$("personal-memory-list").addEventListener("change", (event) => {
  const toggle = event.target.closest("[data-toggle-personal-memory]");
  if (toggle) void updatePersonalMemory(toggle.dataset.togglePersonalMemory, { enabled: toggle.checked });
});
$("open-reminder-settings").addEventListener("click", openReminderSettings);
$("close-reminder").addEventListener("click", () => $("reminder-dialog").close());
$("reminder-form").addEventListener("submit", saveReminderSettings);
$("skip-reminder-today").addEventListener("click", toggleReminderToday);
$("view-settings").addEventListener("click", (event) => {
  const favorite = event.target.closest("[data-favorite-reference]");
  const edit = event.target.closest("[data-edit-reference]");
  const merge = event.target.closest("[data-merge-reference]");
  const restore = event.target.closest("[data-restore-backup]");
  if (favorite) toggleFavorite(favorite.dataset.favoriteReference, favorite.dataset.name);
  if (edit) openReferenceDialog(edit.dataset.editReference, edit.dataset.name);
  if (merge) openMergeDialog(merge.dataset.mergeReference, merge.dataset.name);
  if (restore) restoreLedgerBackup(restore.dataset.restoreBackup);
});
$("month").addEventListener("change", async () => {
  if (!$("liability-existing").value) $("liability-statement-month").value = $("month").value;
  await loadDashboard();
  if (state.activeView === "bills") await loadBills();
  if (state.activeView === "budgets") await loadBudgets();
  if (state.activeView === "subscriptions") await loadSubscriptions();
  if (state.activeView === "liabilities") await loadLiabilities();
});

$("month").value = new Date().toISOString().slice(0, 7);
initializeStatementDaySelectors();
$("liability-statement-month").value = $("month").value;
$("subscription-next-date").value = new Date().toISOString().slice(0, 10);
$("liability-due-date").value = "";
$("transfer-date").value = new Date().toISOString().slice(0, 10);
$("reconcile-date").value = new Date().toISOString().slice(0, 10);
$("account-date").value = new Date().toISOString().slice(0, 10);
initializeInsightsResizer();
renderLogFilter();
loadHealth().then(loadChatHistory);
loadDashboard();
loadAccounts();
loadLiabilityAccounts();
loadReferences().catch((error) => toast(error.message));
loadReminderSettings().catch((error) => toast(error.message));
const startupParams = new URLSearchParams(window.location.search);
switchView(startupParams.get("view") || "chat", false);
if (startupParams.get("reminder") === "daily") {
  $("prompt").placeholder = "今天有哪些支出或收入？";
  setTimeout(() => {
    $("prompt").focus();
    toast("每日记账时间到了");
  }, 500);
}
