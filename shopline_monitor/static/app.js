const state = {
  range: "7d",
  date: "",
  payload: null,
  busy: false,
  theme: document.documentElement.dataset.theme || "dark",
  requestSeq: 0,
  activeRequestSeq: 0,
  abortController: null,
  orderSearch: "",
  orderSource: "",
  orderStatus: "",
  autoRefreshTimer: null,
  autoRefreshMs: 0,
  lastRenderedAt: 0,
  filters: { channel: "", status: "", market: "", product: "" },
  campaignSearch: "",
  campaignCollapsed: localStorage.getItem("sosove-campaign-panel") !== "expanded",
  diagnosticTab: "missing",
  tableStates: {},
  tableConfigs: {},
  dismissedAlerts: new Set(readStoredArray("sosove-dismissed-alerts")),
  authToken: sessionStorage.getItem("sosove-dashboard-token") || "",
  authConfigured: false,
  role: "admin",
};

const ORDER_PAGE_SIZE = 10;
const DEFAULT_AUTO_REFRESH_MS = 300000;
const STALE_REFRESH_AFTER_MS = 120000;
const AUTO_REFRESH_STORAGE_KEY = "sosove-auto-refresh-ms";

const currencyFormatters = new Map();

document.addEventListener("DOMContentLoaded", async () => {
  initTheme();
  const datePicker = document.getElementById("date-picker");
  if (datePicker) {
    datePicker.max = localDateString();
    state.range = "1d";
    state.date = datePicker.max;
    datePicker.value = state.date;
  }
  bindControls();
  bindManagedTableControls();
  applyCampaignCollapse();
  updateControlState();
  syncToolbarDisclosure();
  initScrollTopButton();
  bindFreshnessRefresh();
  window.addEventListener("resize", syncToolbarDisclosure);
  await initializeAccess();
});

function bindControls() {
  document.getElementById("theme-toggle").addEventListener("change", (event) => {
    applyTheme(event.target.checked ? "light" : "dark");
  });

  document.querySelectorAll("[data-range]").forEach((button) => {
    button.addEventListener("click", () => {
      state.range = button.dataset.range;
      state.date = "";
      document.getElementById("date-picker").value = "";
      updateControlState();
      loadDashboard();
    });
  });

  document.getElementById("today-btn").addEventListener("click", () => {
    state.range = "1d";
    state.date = localDateString();
    document.getElementById("date-picker").value = state.date;
    updateControlState();
    loadDashboard();
  });

  document.getElementById("date-choice").addEventListener("click", () => {
    const picker = document.getElementById("date-picker");
    try {
      if (picker.showPicker) {
        picker.showPicker();
        return;
      }
    } catch {
      // Fallback keeps the custom control usable if the browser blocks showPicker.
    }
    if (picker.disabled) {
      return;
    }
    picker.focus();
    picker.click();
  });

  document.getElementById("date-picker").addEventListener("change", (event) => {
    if (!event.target.value) return;
    state.range = "1d";
    state.date = event.target.value;
    updateControlState();
    loadDashboard();
  });

  document.getElementById("sync-btn").addEventListener("click", async () => {
    await loadDashboard({ force: true, announce: true });
  });

  document.getElementById("test-connector-btn").addEventListener("click", async () => {
    const request = beginRequest();
    try {
      const result = await fetchJson("/api/connector/test", { method: "POST", signal: request.signal });
      if (request.id !== state.activeRequestSeq) return;
      const shoplineText = result.shopline?.ok ? "Shopline 正常" : "Shopline 异常";
      const ga4Text = result.ga4?.ok
        ? `GA4 ${formatNumber(result.ga4.purchases || 0)} Purchase`
        : `GA4 ${result.ga4?.status === "unconfigured" ? "未配置" : "异常"}`;
      showToast(`${result.ok ? "接口正常" : "接口需检查"} · ${shoplineText} · ${ga4Text}`);
    } catch (error) {
      if (error.name !== "AbortError") {
        showError(error.message);
      }
    } finally {
      endRequest(request.id);
    }
  });

  document.getElementById("auto-refresh-select").addEventListener("change", (event) => {
    scheduleAutoRefresh(Number(event.target.value) || 0, { announce: true, persist: true });
  });

  document.getElementById("order-search").addEventListener("input", (event) => {
    state.orderSearch = event.target.value.trim().toLowerCase();
    resetTablePage("order-table");
    renderOrders(state.payload?.orders || [], state.payload?.currency, state.payload?.range);
  });

  document.getElementById("order-source-filter").addEventListener("change", (event) => {
    state.orderSource = event.target.value;
    resetTablePage("order-table");
    renderOrders(state.payload?.orders || [], state.payload?.currency, state.payload?.range);
  });

  document.getElementById("order-status-filter").addEventListener("change", (event) => {
    state.orderStatus = event.target.value;
    resetTablePage("order-table");
    renderOrders(state.payload?.orders || [], state.payload?.currency, state.payload?.range);
  });

  document.getElementById("order-export-btn").addEventListener("click", () => {
    exportOrdersCsv();
  });

  document.getElementById("filter-apply-btn").addEventListener("click", () => {
    readGlobalFilters();
    loadDashboard();
  });

  document.getElementById("filter-reset-btn").addEventListener("click", () => {
    state.filters = { channel: "", status: "", market: "", product: "" };
    ["global-channel-filter", "global-status-filter", "global-market-filter", "global-product-filter"]
      .forEach((id) => { document.getElementById(id).value = ""; });
    loadDashboard();
  });

  document.getElementById("report-export-btn").addEventListener("click", exportManagementReport);
  document.getElementById("campaign-export-btn").addEventListener("click", exportCampaignsCsv);
  document.getElementById("campaign-search").addEventListener("input", (event) => {
    state.campaignSearch = event.target.value.trim().toLowerCase();
    resetTablePage("campaign-table");
    renderCampaigns(state.payload?.campaigns, state.payload?.currency);
  });
  document.getElementById("campaign-toggle-btn").addEventListener("click", toggleCampaignCollapse);
  document.querySelectorAll("[data-diagnostic-tab]").forEach((button) => {
    button.addEventListener("click", () => selectDiagnosticTab(button.dataset.diagnosticTab));
  });
  document.getElementById("copy-click-map-btn").addEventListener("click", copyClickMappingTemplate);
  document.getElementById("restore-alerts-btn").addEventListener("click", restoreDismissedAlerts);
  document.getElementById("alert-list").addEventListener("click", handleAlertAction);
  document.getElementById("channels").addEventListener("click", handleChannelPanelAction);
  document.getElementById("channel-dialog-close").addEventListener("click", closeChannelDialog);
  document.getElementById("channel-dialog-filter").addEventListener("click", filterChannelOrders);
  document.getElementById("channel-order-dialog").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeChannelDialog();
  });

  document.getElementById("auth-form").addEventListener("submit", handleAuthSubmit);
}

function initScrollTopButton() {
  const button = document.getElementById("scroll-top-btn");
  if (!button) return;

  const revealAt = 520;
  const updateVisibility = () => {
    const shouldShow = window.scrollY > revealAt;
    button.hidden = false;
    button.classList.toggle("visible", shouldShow);
    button.setAttribute("aria-hidden", shouldShow ? "false" : "true");
    button.tabIndex = shouldShow ? 0 : -1;
  };

  button.addEventListener("click", () => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
  });

  window.addEventListener("scroll", updateVisibility, { passive: true });
  window.addEventListener("resize", updateVisibility);
  updateVisibility();
}

async function loadDashboard({ force = false, announce = false } = {}) {
  const request = beginRequest();
  try {
    const payload = force
      ? await fetchJson("/api/sync", {
        method: "POST",
        body: JSON.stringify(currentQueryPayload()),
        signal: request.signal,
      })
      : await fetchJson(metricsPath(), { signal: request.signal });
    if (request.id !== state.activeRequestSeq) return;
    render(payload);
    if (announce) showToast("Shopline 与 GA4 同步完成");
  } catch (error) {
    if (error.name !== "AbortError") {
      showError(error.message);
    }
  } finally {
    endRequest(request.id);
  }
}

async function fetchJson(path, options = {}) {
  const { skipAuth = false, ...fetchOptions } = options;
  const headers = { "Content-Type": "application/json", ...(fetchOptions.headers || {}) };
  if (!skipAuth && state.authToken) {
    headers["X-Dashboard-Token"] = state.authToken;
  }
  const response = await fetch(path, {
    ...fetchOptions,
    cache: "no-store",
    headers,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    if (response.status === 401 && state.authConfigured) {
      state.authToken = "";
      sessionStorage.removeItem("sosove-dashboard-token");
      showAuthGate();
    }
    throw new Error(data.error || response.statusText);
  }
  return data;
}

function render(payload) {
  state.payload = payload;
  state.lastRenderedAt = Date.now();
  setHidden("error-panel", true);
  setText("range-caption", formatRangeCaption(payload.range));
  setText("mobile-range-caption", formatRangeCaption(payload.range));
  const loadSeconds = Number(payload.source.loadMs || 0) / 1000;
  setText("source-badge", `${payload.source.cached ? "缓存" : "实时"} · ${loadSeconds ? `${formatNumber(loadSeconds)}s` : payload.source.label}`);
  setText("last-sync", formatDateTime(payload.source.syncedAt));
  renderStatusRail(payload);
  renderConnector(payload);
  renderKpis(payload.kpis, payload.currency);
  renderDailySummary(payload.dailySummary, payload.currency);
  renderGlobalFilters(payload.filters);
  renderDataTrust(
    payload.reconciliation,
    payload.syncQuality,
    payload.campaigns,
    payload.attributionDiagnostics,
    payload.ga4Status,
  );
  renderAttributionDiagnostics(payload.attributionDiagnostics, payload.currency);
  renderChart(payload.series, payload.currency);
  renderChannels(payload.channels, payload.currency, payload.channelAnalytics);
  renderCampaigns(payload.campaigns, payload.currency);
  renderProfit(payload.profit, payload.currency);
  renderAdPerformance(payload.adPerformance, payload.currency);
  renderAnalytics(payload.analytics, payload.currency);
  renderCustomerInsights(payload.customers, payload.currency);
  renderOrderStatus(payload.orderStatus);
  renderProducts(payload.products, payload.currency);
  populateOrderFilters(payload.orders);
  renderOrders(payload.orders, payload.currency, payload.range);
  renderAlerts(payload.alerts, payload.alertDelivery);
  renderEvents(payload.events);

  if (payload.source.errors && payload.source.errors.length) {
    showError(payload.source.errors[0]);
  }
}

function renderStatusRail(payload) {
  setText("rail-mode", payload.source.cached ? "高速缓存" : payload.source.label);
  setText("rail-range", payload.range.days === 1 ? payload.range.start : `${payload.range.days}D`);
  setText("rail-currency", payload.currency || "--");
  setText("rail-sync", formatDateTime(payload.source.syncedAt));
}

function renderConnector(payload) {
  const connector = payload.connector;
  const modeText = payload.source.label;
  document.getElementById("connector-mode").textContent = modeText;
  document.getElementById("connector-base").textContent = connector.baseUrl || "未配置";
  document.getElementById("connector-orders").textContent = connector.ordersEndpoint || "未配置";
  document.getElementById("connector-products").textContent = connector.productsEndpoint || "未配置";
  document.getElementById("connector-timezone").textContent = connector.timezoneName || "Asia/Tokyo";
  const ga4 = connector.ga4 || {};
  document.getElementById("connector-ga4").textContent = ga4.configured
    ? `已配置 ${ga4.propertyId || "--"}`
    : "未配置";
  document.getElementById("connector-ga4-metric").textContent = ga4.configured
    ? `${ga4.conversionMode || "--"} / ${ga4.metricName || "--"}`
    : "sessionKeyEventRate";
  const missingNode = document.getElementById("connector-missing");
  missingNode.textContent = connector.missing.length ? `${connector.missing.length} 项未配置` : "无";
  missingNode.title = connector.missing.join(", ");

  const pill = document.getElementById("connector-pill");
  pill.textContent = connector.configured ? "Live" : "Sample";
  pill.classList.toggle("live", connector.configured);

  const dot = document.getElementById("side-status-dot");
  dot.classList.toggle("live", connector.configured);
}

function renderKpis(kpis, currency) {
  Object.entries(kpis).forEach(([key, kpi]) => {
    const card = document.querySelector(`[data-kpi="${key}"]`);
    if (!card) return;
    card.querySelector("[data-value]").textContent = formatMetric(kpi.value, kpi.type, currency);
    const deltaNode = card.querySelector("[data-delta]");
    const hasDelta = kpi.delta !== null && kpi.delta !== undefined && Number.isFinite(Number(kpi.delta));
    deltaNode.textContent = hasDelta
      ? `${kpi.delta >= 0 ? "+" : ""}${kpi.delta}% vs 上期`
      : (kpi.note || "无对比数据");
    deltaNode.classList.toggle("negative", hasDelta && kpi.delta < 0);
    const fill = card.querySelector(".metric-track i");
    fill.style.width = hasDelta ? `${Math.max(18, Math.min(100, Math.abs(kpi.delta) + 48))}%` : "18%";
  });
}

function renderChart(series, currency) {
  const mount = document.getElementById("revenue-chart");
  if (!series.length) {
    mount.innerHTML = '<p class="empty">暂无趋势数据</p>';
    return;
  }

  const width = 820;
  const height = 320;
  const left = 54;
  const right = 28;
  const top = 28;
  const bottom = 44;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const maxRevenue = Math.max(1, ...series.map((row) => Number(row.revenue) || 0));
  const maxOrders = Math.max(1, ...series.map((row) => Number(row.orders) || 0));
  const conversionValues = series
    .map((row) => Number(row.conversion))
    .filter((value) => Number.isFinite(value) && value >= 0);
  const maxConversion = Math.max(1, ...conversionValues, 0.01);
  const step = series.length > 1 ? plotWidth / (series.length - 1) : plotWidth;
  const barWidth = Math.max(10, Math.min(34, plotWidth / series.length * 0.44));
  const baseY = top + plotHeight;

  const points = series.map((row, index) => {
    const x = series.length === 1 ? left + plotWidth / 2 : left + index * step;
    const y = top + plotHeight - (Number(row.revenue) / maxRevenue) * plotHeight;
    return { x, y, row };
  });
  const line = points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
  const area = [
    `M ${points[0].x.toFixed(1)} ${baseY}`,
    ...points.map((point) => `L ${point.x.toFixed(1)} ${point.y.toFixed(1)}`),
    `L ${points[points.length - 1].x.toFixed(1)} ${baseY}`,
    "Z",
  ].join(" ");
  const labelStep = Math.max(1, Math.ceil(series.length / 6));

  const bars = points.map((point) => {
    const orders = Number(point.row.orders) || 0;
    const barHeight = (orders / maxOrders) * (plotHeight * 0.45);
    const x = point.x - barWidth / 2;
    const y = baseY - barHeight;
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${barHeight.toFixed(1)}" rx="4" fill="#63d8ff" opacity="0.26"></rect>`;
  }).join("");

  const conversionPoints = series
    .map((row, index) => {
      const value = Number(row.conversion);
      if (!Number.isFinite(value)) return null;
      const x = series.length === 1 ? left + plotWidth / 2 : left + index * step;
      const y = top + plotHeight - (value / maxConversion) * (plotHeight * 0.86);
      return { x, y, value };
    })
    .filter(Boolean);
  const conversionLine = conversionPoints.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
  const conversionLabel = conversionPoints.length ? formatPercent(maxConversion) : "--";

  const labels = points.map((point, index) => {
    if (index % labelStep !== 0 && index !== points.length - 1) return "";
    return `<text x="${point.x.toFixed(1)}" y="${height - 14}" text-anchor="middle" class="chart-axis">${escapeHtml(point.row.label)}</text>`;
  }).join("");

  const grid = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
    const y = top + plotHeight * ratio;
    return `<line x1="${left}" x2="${width - right}" y1="${y}" y2="${y}" stroke="rgba(123,255,212,0.18)" stroke-width="1"></line>`;
  }).join("");

  const revenueLabel = formatCurrency(maxRevenue, currency);
  mount.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" aria-hidden="true">
      <defs>
        <filter id="lineGlow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="3" result="blur"></feGaussianBlur>
          <feMerge>
            <feMergeNode in="blur"></feMergeNode>
            <feMergeNode in="SourceGraphic"></feMergeNode>
          </feMerge>
        </filter>
      </defs>
      ${grid}
      ${bars}
      <path d="${area}" fill="#4cffb1" opacity="0.11"></path>
      <polyline points="${line}" fill="none" stroke="#4cffb1" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" filter="url(#lineGlow)"></polyline>
      ${conversionPoints.length ? `<polyline points="${conversionLine}" fill="none" stroke="#ffbf58" stroke-width="2.6" stroke-dasharray="8 7" stroke-linecap="round" stroke-linejoin="round"></polyline>` : ""}
      ${points.map((point) => `<circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="4.5" fill="#071011" stroke="#4cffb1" stroke-width="2"></circle>`).join("")}
      ${conversionPoints.map((point) => `<circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="3.5" fill="#1b1510" stroke="#ffbf58" stroke-width="2"></circle>`).join("")}
      ${labels}
      <text x="${left}" y="18" class="chart-axis">${escapeHtml(revenueLabel)}</text>
      <text x="${width / 2}" y="18" text-anchor="middle" class="chart-axis">转化峰值 ${escapeHtml(conversionLabel)}</text>
      <text x="${width - right}" y="18" text-anchor="end" class="chart-axis">订单峰值 ${maxOrders}</text>
    </svg>
  `;
}

function renderGlobalFilters(filterPayload = {}) {
  const options = filterPayload.options || {};
  populateSelect("global-channel-filter", "全部渠道", options.channels || [], state.filters.channel);
  populateSelect("global-status-filter", "全部状态", options.statuses || [], state.filters.status);
  populateSelect("global-market-filter", "全部市场", options.markets || [], state.filters.market);
  populateSelect("global-product-filter", "全部商品", options.products || [], state.filters.product);
}

function populateSelect(id, emptyLabel, options, selectedValue) {
  const node = document.getElementById(id);
  if (!node) return;
  node.innerHTML = [
    `<option value="">${escapeHtml(emptyLabel)}</option>`,
    ...options.map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`),
  ].join("");
  node.value = selectedValue || "";
}

function readGlobalFilters() {
  state.filters = {
    channel: document.getElementById("global-channel-filter").value,
    status: document.getElementById("global-status-filter").value,
    market: document.getElementById("global-market-filter").value,
    product: document.getElementById("global-product-filter").value,
  };
}

function renderDailySummary(summary = {}, currency) {
  const metrics = summary?.metrics || {};
  const anomalies = summary?.abnormalProducts || [];
  setText("daily-summary-date", summary?.date || "--");
  setText("daily-summary-headline", summary?.headline || "当前日期暂无可汇总数据。");
  setText("daily-orders", formatNumber(metrics.orders || 0));
  setText("daily-revenue", formatCurrency(metrics.revenue || 0, currency));
  setText("daily-profit", formatCurrency(metrics.estimatedProfit || 0, currency));
  setText("daily-profit-margin", `利润率 ${formatOptionalPercent(metrics.profitMargin)}`);
  setText("daily-anomaly-count", formatNumber(anomalies.length));
  setDeltaText("daily-orders-delta", metrics.ordersDeltaRate, "较昨日");
  setDeltaText("daily-revenue-delta", metrics.revenueDeltaRate, "较昨日");

  renderMovementList("daily-growth-channels", summary?.growthChannels, currency, "暂无明显增长渠道");
  renderMovementList("daily-decline-channels", summary?.declineChannels, currency, "暂无明显下降渠道");
  const anomalyNode = document.getElementById("daily-product-anomalies");
  anomalyNode.innerHTML = anomalies.length
    ? anomalies.map((item) => `
      <li class="tone-${escapeHtml(item.tone || "neutral")}">
        <span><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.label)} · ${escapeHtml(item.message)}</small></span>
        <strong>${item.inventory === null || item.inventory === undefined ? `${formatNumber(item.todayUnits)} 件` : `库存 ${formatNumber(item.inventory)}`}</strong>
      </li>
    `).join("")
    : '<li class="empty-brief"><span>未发现销量或库存异常</span><strong>稳定</strong></li>';
}

function renderMovementList(id, rows = [], currency, emptyText) {
  const mount = document.getElementById(id);
  const safeRows = Array.isArray(rows) ? rows : [];
  mount.innerHTML = safeRows.length
    ? safeRows.map((row) => {
      const positive = Number(row.revenueDelta) >= 0;
      return `
        <li>
          <span><b>${escapeHtml(row.channel)}</b><small>${formatNumber(row.currentOrders)} 单 · 昨日 ${formatNumber(row.previousOrders)} 单</small></span>
          <strong class="${positive ? "positive" : "negative"}">${positive ? "+" : ""}${formatCurrency(row.revenueDelta, currency)}</strong>
        </li>
      `;
    }).join("")
    : `<li class="empty-brief"><span>${escapeHtml(emptyText)}</span><strong>—</strong></li>`;
}

function setDeltaText(id, value, prefix) {
  const node = document.getElementById(id);
  if (!node) return;
  const valid = value !== null && value !== undefined && Number.isFinite(Number(value));
  node.textContent = valid ? `${prefix} ${Number(value) >= 0 ? "+" : ""}${formatNumber(value)}%` : `${prefix} --`;
  node.classList.toggle("negative", valid && Number(value) < 0);
}

function renderDataTrust(reconciliation = {}, quality = {}, campaigns = {}, diagnostics = {}, ga4Status = {}) {
  setText("reconcile-shopline", formatNumber(reconciliation.shoplineOrders || 0));
  setText("reconcile-ga4", reconciliation.ga4Purchases === null || reconciliation.ga4Purchases === undefined
    ? "--"
    : formatNumber(reconciliation.ga4Purchases));
  setText("reconcile-status", reconciliation.label || "--");
  const difference = reconciliation.difference;
  const ga4Context = ga4Status.sessions === null || ga4Status.sessions === undefined
    ? ""
    : ` · GA4 ${formatNumber(ga4Status.sessions)} 会话`;
  const missingNote = ga4Status.status === "error"
    ? (ga4Status.errors?.[0] || "GA4 查询异常，请点击接口测试查看。")
    : ga4Status.status === "unconfigured"
      ? "请在 .env 中完成 GA4 Property 与凭证配置。"
      : ga4Status.status === "empty"
        ? "GA4 已连接，所选日期当前没有可返回的 Purchase。"
        : "等待 GA4 Purchase 数据完成对账。";
  setText("reconcile-note", difference === null || difference === undefined
    ? missingNote
    : `相差 ${formatNumber(Math.abs(difference))} 单 · 差异率 ${formatOptionalPercent(reconciliation.differenceRate)}${ga4Context}`);
  const statusNode = document.getElementById("reconcile-status");
  statusNode.className = `status-pill reconcile-${reconciliation.status || "missing"}`;
  statusNode.title = ga4Status.errors?.join("\n") || ga4Status.label || "GA4 Data API";

  setText("quality-grade", `${quality.grade || "--"} · ${formatNumber(quality.score || 0)}`);
  const meter = document.getElementById("quality-meter");
  meter.style.width = `${Math.max(0, Math.min(100, Number(quality.score) || 0))}%`;
  document.getElementById("quality-facts").innerHTML = [
    ["抓取页数", `${formatNumber(quality.orderPages || 0)} / ${formatNumber(quality.orderChunks || 1)} 段`],
    ["原始订单", quality.rawOrders],
    ["重复剔除", quality.duplicateOrders],
    ["分页状态", quality.pageLimitReached ? "达到上限" : "完整"],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${typeof value === "number" ? formatNumber(value) : escapeHtml(value)}</strong></div>`).join("");

  setText("campaign-coverage", formatOptionalPercent(campaigns.coverage));
  const attributionSummary = diagnostics?.summary || {};
  document.getElementById("attribution-facts").innerHTML = [
    ["官方归因", quality.officialAttributionRate],
    ["来源识别", quality.attributionRate],
    ["UTM 完整", attributionSummary.utmCoverage],
    ["Click 映射", attributionSummary.clickMappingRate],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${formatOptionalPercent(value)}</strong></div>`).join("");
}

function renderAttributionDiagnostics(diagnostics = {}, currency) {
  const summary = diagnostics?.summary || {};
  const missingOrders = Array.isArray(diagnostics?.missingOrders) ? diagnostics.missingOrders : [];
  const issues = Array.isArray(diagnostics?.issues) ? diagnostics.issues : [];
  const clickMappings = Array.isArray(diagnostics?.clickMappings) ? diagnostics.clickMappings : [];
  setText("utm-coverage-badge", `UTM ${formatOptionalPercent(summary.utmCoverage)}`);
  setText("click-map-badge", `Click ID ${formatOptionalPercent(summary.clickMappingRate)}`);
  setText("missing-utm-count", formatNumber(missingOrders.length));
  setText("utm-issue-count", formatNumber(issues.length));
  setText("click-map-count", formatNumber(clickMappings.length));
  document.getElementById("diagnostic-metrics").innerHTML = [
    ["UTM 完整订单", summary.completeUtmOrders || 0, summary.utmCoverage],
    ["命名合规订单", summary.validUtmOrders || 0, summary.validUtmCoverage],
    ["Campaign 覆盖", summary.campaignOrders || 0, summary.campaignCoverage],
    ["Click ID 已映射", summary.mappedClickOrders || 0, summary.clickMappingRate],
  ].map(([label, value, rate]) => `
    <article><span>${escapeHtml(label)}</span><strong>${formatNumber(value)}</strong><small>${formatOptionalPercent(rate)}</small></article>
  `).join("");
  document.getElementById("utm-rules").innerHTML = (diagnostics?.rules || [])
    .map((rule) => `<li>${escapeHtml(rule)}</li>`).join("") || "<li>暂无规则</li>";

  renderManagedTable({
    tableId: "missing-utm-table",
    rows: missingOrders,
    emptyText: "所选范围没有 UTM 缺失订单",
    pageSize: 8,
    defaultSort: { key: "createdAt", direction: "desc" },
    columns: [
      { key: "orderId", label: "订单", render: (row) => `<strong>${escapeHtml(row.orderId)}</strong><span class="cell-sub">${escapeHtml(row.createdAt)}</span>` },
      { key: "source", label: "当前来源", render: (row) => `<span class="source-chip">${escapeHtml(row.source)}</span>` },
      { key: "missingFields", label: "缺失字段", value: (row) => (row.missingFields || []).join(", "), render: (row) => (row.missingFields || []).map((field) => `<code>${escapeHtml(field)}</code>`).join(" ") },
      { key: "clickTypes", label: "Click ID", value: (row) => (row.clickTypes || []).join(", "), render: (row) => (row.clickTypes || []).length ? (row.clickTypes || []).map((item) => `<span class="mini-tag">${escapeHtml(item)}</span>`).join(" ") : "--" },
      { key: "reason", label: "诊断", render: (row) => `<span class="diagnostic-status ${escapeHtml(row.severity)}">${escapeHtml(row.reason)}</span>` },
      { key: "total", label: "金额", type: "number", render: (row) => formatCurrency(row.total, currency) },
    ],
  });

  renderManagedTable({
    tableId: "utm-issue-table",
    rows: issues,
    emptyText: "UTM 命名全部符合规范",
    pageSize: 8,
    defaultSort: { key: "createdAt", direction: "desc" },
    columns: [
      { key: "orderId", label: "订单", render: (row) => `<strong>${escapeHtml(row.orderId)}</strong><span class="cell-sub">${escapeHtml(row.createdAt)}</span>` },
      { key: "field", label: "字段", render: (row) => `<code>${escapeHtml(row.field)}</code>` },
      { key: "value", label: "当前值", render: (row) => `<span class="broken-value">${escapeHtml(row.value)}</span>` },
      { key: "message", label: "识别结果", render: (row) => `<span class="diagnostic-status ${escapeHtml(row.severity)}">${escapeHtml(row.message)}</span>` },
      { key: "suggestion", label: "建议值", render: (row) => `<code class="suggestion-value">${escapeHtml(row.suggestion)}</code>` },
    ],
  });

  renderManagedTable({
    tableId: "click-map-table",
    rows: clickMappings,
    emptyText: "所选范围没有捕获 Click ID",
    pageSize: 8,
    defaultSort: { key: "mapped", direction: "asc" },
    columns: [
      { key: "orderId", label: "订单", render: (row) => `<strong>${escapeHtml(row.orderId)}</strong><span class="cell-sub">${escapeHtml(row.createdAt)}</span>` },
      { key: "clickType", label: "类型", render: (row) => `<span class="mini-tag">${escapeHtml(row.clickType)}</span>` },
      { key: "clickId", label: "Click ID", render: (row) => `<code title="${escapeHtml(row.clickId)}">${escapeHtml(row.clickIdPreview || row.clickId)}</code>` },
      { key: "campaignId", label: "Campaign ID", render: (row) => row.campaignId ? `<code>${escapeHtml(row.campaignId)}</code>` : '<span class="unmapped">待映射</span>' },
      { key: "campaignName", label: "Campaign", render: (row) => escapeHtml(row.campaignName || "--") },
      { key: "mapped", label: "状态", type: "boolean", render: (row) => `<span class="mapping-state ${row.mapped ? "mapped" : "unmapped"}">${row.mapped ? "已映射" : "未映射"}</span>` },
    ],
  });
  selectDiagnosticTab(state.diagnosticTab);
}

function renderCampaigns(campaigns = {}, currency) {
  const search = state.campaignSearch;
  const rows = (campaigns?.rows || []).filter((row) => {
    if (!search) return true;
    return [row.channel, row.campaign, row.adset, row.ad].join(" ").toLowerCase().includes(search);
  });
  setText(
    "campaign-fold-summary",
    `${formatNumber(campaigns?.rows?.length || 0)} 项 · 覆盖 ${formatOptionalPercent(campaigns?.coverage)}`,
  );
  renderManagedTable({
    tableId: "campaign-table",
    rows,
    emptyText: "暂无 Campaign 归因数据",
    pageSize: 8,
    defaultSort: { key: "revenue", direction: "desc" },
    columns: [
      { key: "channel", label: "渠道", render: (row) => `<strong>${escapeHtml(row.channel)}</strong>` },
      { key: "campaign", label: "Campaign" },
      { key: "adset", label: "Adset" },
      { key: "ad", label: "Ad / Content" },
      { key: "orders", label: "订单", type: "number", render: (row) => formatNumber(row.orders) },
      { key: "customers", label: "客户", type: "number", render: (row) => formatNumber(row.customers) },
      { key: "revenue", label: "销售额", type: "number", render: (row) => formatCurrency(row.revenue, currency) },
      { key: "aov", label: "客单价", type: "number", render: (row) => formatCurrency(row.aov, currency) },
    ],
  });
}

function toggleCampaignCollapse() {
  state.campaignCollapsed = !state.campaignCollapsed;
  localStorage.setItem("sosove-campaign-panel", state.campaignCollapsed ? "collapsed" : "expanded");
  applyCampaignCollapse();
}

function applyCampaignCollapse() {
  const panel = document.getElementById("campaigns");
  const button = document.getElementById("campaign-toggle-btn");
  if (!panel || !button) return;
  panel.classList.toggle("collapsed", state.campaignCollapsed);
  button.textContent = state.campaignCollapsed ? "展开详情" : "收起详情";
  button.setAttribute("aria-expanded", state.campaignCollapsed ? "false" : "true");
}

async function initializeAccess() {
  try {
    const status = await fetchJson("/api/auth/status", { skipAuth: true });
    state.authConfigured = Boolean(status.configured);
    state.role = status.role || "admin";
    if (state.authConfigured && !state.authToken) {
      showAuthGate();
      return;
    }
    applyRolePermissions();
    initializeAutoRefresh();
    loadDashboard();
  } catch (error) {
    showError(error.message);
  }
}

async function handleAuthSubmit(event) {
  event.preventDefault();
  const input = document.getElementById("auth-token");
  const token = input.value.trim();
  const errorNode = document.getElementById("auth-error");
  errorNode.textContent = "";
  try {
    const result = await fetchJson("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ token }),
      skipAuth: true,
    });
    state.authToken = token;
    state.role = result.role || "admin";
    sessionStorage.setItem("sosove-dashboard-token", token);
    document.getElementById("auth-gate").hidden = true;
    applyRolePermissions();
    initializeAutoRefresh();
    loadDashboard();
  } catch (error) {
    errorNode.textContent = error.message;
  }
}

function showAuthGate() {
  const gate = document.getElementById("auth-gate");
  gate.hidden = false;
  document.getElementById("auth-token").focus();
}

function applyRolePermissions() {
  const readOnly = state.role === "viewer";
  ["sync-btn", "test-connector-btn"].forEach((id) => {
    const node = document.getElementById(id);
    if (node) node.disabled = readOnly;
  });
}

function renderChannels(channels, currency, analytics = {}) {
  const safeChannels = Array.isArray(channels) ? channels : [];
  setText("channel-mode", analytics.label || "Shopline 订单归因");
  setText("channel-sessions", analytics.sessions ? formatNumber(analytics.sessions) : "--");
  setText("channel-order-rate", formatOptionalPercent(analytics.orderAttributionRate));
  setText("channel-official-rate", formatOptionalPercent(analytics.officialAttributionRate));
  setText("channel-smartpush-orders", `${formatNumber(analytics.smartPushOrders || 0)} 单`);
  setText("channel-smartpush-revenue", formatCurrency(analytics.smartPushRevenue || 0, currency));

  renderManagedTable({
    tableId: "channel-table",
    rows: safeChannels,
    emptyText: "暂无渠道数据",
    pageSize: 8,
    defaultSort: { key: "orders", direction: "desc" },
    columns: [
      { key: "channel", label: "来源", render: (channel) => {
    const details = (channel.sourceDetails || [])
      .map((item) => `${escapeHtml(item.label)} · ${formatNumber(item.sessions)}`)
      .join("<br>");
    const officialOrders = Number(channel.officialOrders) || 0;
    const officialDetail = channel.orders
      ? `SHOPLINE 官方归因 ${formatNumber(officialOrders)}/${formatNumber(channel.orders)} 单`
      : "仅有 GA4 流量，暂无订单";
    const detailNode = [
      channel.orders ? `<span class="channel-detail channel-official">${escapeHtml(officialDetail)}</span>` : "",
      details
        ? `<span class="channel-detail" title="${escapeHtml((channel.sourceDetails || []).map((item) => `${item.label}: ${item.sessions}`).join(" | "))}">${details}</span>`
        : "",
    ].filter(Boolean).join("");
    return `
        <strong class="channel-name"><i class="channel-dot channel-${channelClass(channel.channel)}"></i>${escapeHtml(channel.channel)}</strong>
        ${detailNode}
        <span class="channel-share"><i style="width:${Math.max(0, Math.min(100, Number(channel.share) || 0))}%"></i></span>
        ${channel.orders ? `<button type="button" class="channel-drill-button" data-channel-drill="${escapeHtml(channel.channel)}">订单明细 <b>${formatNumber(channel.orderDetailCount || channel.orders)}</b><i aria-hidden="true">↗</i></button>` : ""}
      `;
      } },
      { key: "utmCoverage", label: "UTM 明细", type: "number", render: (channel) => renderChannelUtm(channel, currency) },
      { key: "sessions", label: "会话", type: "number", render: (channel) => `${channel.sessions ? formatNumber(channel.sessions) : "--"}<span class="channel-mini">${channel.sessions ? formatPercent(channel.share) : "GA4 待同步"}</span>` },
      { key: "activeUsers", label: "用户", type: "number", render: (channel) => channel.activeUsers ? formatNumber(channel.activeUsers) : "--" },
      { key: "orders", label: "订单", type: "number", render: (channel) => formatNumber(channel.orders) },
      { key: "shoplineConversion", label: "双转化", type: "number", render: (channel) => renderChannelConversion(channel) },
      { key: "revenue", label: "销售额", type: "number", render: (channel) => formatCurrency(channel.revenue, currency) },
    ],
  });
}

function renderChannelConversion(channel) {
  if (!channel.sessions) {
    return '<span class="utm-empty">GA4 待同步</span>';
  }
  const warning = channel.conversionComparable === false
    ? `<i class="conversion-warning" title="${escapeHtml(channel.conversionNote || "渠道会话无法完全拆分")}">!</i>`
    : "";
  return `
    <div class="channel-conversion" title="SHOPLINE：订单 ÷ GA4 会话；GA4：Purchase ÷ 会话">
      <span><b>SL</b>${formatOptionalPercent(channel.shoplineConversion)}</span>
      <span><b>GA4</b>${formatOptionalPercent(channel.ga4Conversion)}</span>
      ${warning}
    </div>
  `;
}

function handleChannelPanelAction(event) {
  const button = event.target.closest("[data-channel-drill]");
  if (!button || !state.payload) return;
  const channel = (state.payload.channels || []).find((row) => row.channel === button.dataset.channelDrill);
  if (!channel) return;
  openChannelDialog(channel, state.payload.currency);
}

function openChannelDialog(channel, currency) {
  const dialog = document.getElementById("channel-order-dialog");
  dialog.dataset.channel = channel.channel;
  setText("channel-dialog-title", `${channel.channel} 订单核对`);
  setText("channel-dialog-subtitle", `${formatNumber(channel.sessions || 0)} 会话 · ${formatNumber(channel.activeUsers || 0)} 用户`);
  setText("channel-dialog-orders", `${formatNumber(channel.orders || 0)} 单`);
  setText("channel-dialog-revenue", formatCurrency(channel.revenue || 0, currency));
  setText("channel-dialog-official", `${formatNumber(channel.officialOrders || 0)}/${formatNumber(channel.orders || 0)} 单`);
  setText("channel-dialog-shopline-rate", formatOptionalPercent(channel.shoplineConversion));
  setText("channel-dialog-ga4-rate", formatOptionalPercent(channel.ga4Conversion));
  setText("channel-dialog-note", channel.conversionNote || "SHOPLINE 订单与 GA4 会话的跨系统核对");
  setText("channel-dialog-count", `显示最近 ${formatNumber((channel.orderDetails || []).length)} 笔 · 渠道共 ${formatNumber(channel.orderDetailCount || channel.orders || 0)} 单`);
  const rows = (channel.orderDetails || []).map((order) => {
    const tracking = [order.utmSource, order.utmMedium].filter(Boolean).join(" / ");
    const attribution = order.attributionMethod === "shopline_attribution" ? "SHOPLINE 官方" : (order.attributionMethod || "字段推断");
    return `
      <tr>
        <td data-label="订单"><strong title="${escapeHtml(order.id)}">${escapeHtml(compactOrderId(order.id))}</strong></td>
        <td data-label="日期">${escapeHtml(order.createdAt || "--")}</td>
        <td data-label="状态"><span class="pill ${statusTone(order)}">${escapeHtml(order.status || "--")}</span></td>
        <td data-label="UTM / Campaign"><span>${escapeHtml(tracking || "未捕获 UTM")}</span><small>${escapeHtml(order.campaign || "无 Campaign")}</small></td>
        <td data-label="归因"><span>${escapeHtml(attribution)}</span><small>${escapeHtml(order.attributionConfidence || "--")}</small></td>
        <td data-label="金额"><strong>${formatCurrency(order.total || 0, currency)}</strong></td>
      </tr>
    `;
  }).join("");
  document.getElementById("channel-dialog-order-rows").innerHTML = rows || '<tr><td colspan="6" class="empty">暂无订单明细</td></tr>';
  if (typeof dialog.showModal === "function") dialog.showModal();
}

function closeChannelDialog() {
  const dialog = document.getElementById("channel-order-dialog");
  if (dialog.open) dialog.close();
}

function filterChannelOrders() {
  const dialog = document.getElementById("channel-order-dialog");
  const channel = dialog.dataset.channel || "";
  const sourceSelect = document.getElementById("order-source-filter");
  if (sourceSelect && [...sourceSelect.options].some((option) => option.value === channel)) {
    state.orderSource = channel;
    sourceSelect.value = channel;
    resetTablePage("order-table");
    renderOrders(state.payload?.orders || [], state.payload?.currency, state.payload?.range);
  }
  closeChannelDialog();
  focusPanel("orders");
}

function compactOrderId(value) {
  const text = String(value || "");
  return text.length > 16 ? `…${text.slice(-15)}` : text || "--";
}

function renderChannelUtm(channel, currency) {
  const details = Array.isArray(channel.utmDetails) ? channel.utmDetails : [];
  if (!details.length) {
    return '<span class="utm-empty">未捕获 UTM</span>';
  }

  const combinations = Number(channel.utmCombinationCount) || details.length;
  const orders = Number(channel.utmOrders) || 0;
  const coverage = formatOptionalPercent(channel.utmCoverage);
  const rows = details.map((item) => {
    const tags = [
      ["source", item.source],
      ["medium", item.medium],
      ["campaign", item.campaign],
      ["content", item.content],
      ["term", item.term],
    ]
      .filter(([, value]) => String(value || "").trim())
      .map(([label, value]) => `<span class="utm-tag"><b>${label}</b><span class="utm-value">${escapeHtml(value)}</span></span>`)
      .join("");
    return `
      <div class="utm-entry">
        <div class="utm-tags">${tags}</div>
        <span class="utm-result"><strong>${formatNumber(item.orders)} 单</strong>${formatCurrency(item.revenue, currency)}</span>
      </div>
    `;
  }).join("");

  return `
    <details class="utm-disclosure">
      <summary>
        <span class="utm-mark">UTM</span>
        <strong>${formatNumber(combinations)} 组</strong>
        <small>${formatNumber(orders)} 单 · ${coverage}</small>
        <i aria-hidden="true"></i>
      </summary>
      <div class="utm-list">${rows}</div>
    </details>
  `;
}

function formatOptionalPercent(value) {
  return value === null || value === undefined || !Number.isFinite(Number(value))
    ? "--"
    : formatPercent(value);
}

function channelClass(value) {
  return String(value || "other").toLowerCase().replaceAll(" / ", "-").replace(/[^a-z0-9-]/g, "-");
}

function renderProfit(profit, currency) {
  if (!profit) return;
  setText("profit-main", formatCurrency(profit.estimatedProfit, currency));
  setText("profit-margin", `利润率 ${formatPercent(profit.margin)}`);
  setText("profit-adcost", formatCurrency(profit.adCost, currency));
  setText("profit-platform", formatCurrency(profit.platformCost, currency));
  setText("profit-product-cost", formatCurrency(profit.productCost, currency));
  setText("profit-net-revenue", formatCurrency(profit.netRevenue, currency));
  setText("profit-refunds", formatCurrency(profit.refunds, currency));
  setText("profit-cost-coverage", formatPercent(profit.costCoverage || 0));
  setText("profit-cost-rate", `${formatNumber(profit.productCostRate)}%`);
  setText("profit-fee-rate", `${formatNumber(profit.paymentFeeRate)}%`);
  setText("profit-shipping", formatCurrency(profit.shippingCostPerOrder, currency));
  setText("profit-note", (profit.notes || []).join(" "));
}

function renderAdPerformance(rows, currency) {
  renderManagedTable({
    tableId: "ad-table",
    rows: rows || [],
    emptyText: "暂无渠道数据",
    pageSize: 6,
    defaultSort: { key: "revenue", direction: "desc" },
    columns: [
      { key: "channel", label: "渠道", render: (row) => `<strong>${escapeHtml(row.channel)}</strong><span class="cell-sub">${formatNumber(row.orders)} 单</span>` },
      { key: "revenue", label: "销售额", type: "number", render: (row) => formatCurrency(row.revenue, currency) },
      { key: "spend", label: "花费", type: "number", render: (row) => `${formatCurrency(row.spend, currency)}<span class="cell-sub">${row.spendSource === "ga4" ? "GA4" : row.spendSource === "configured" ? "配置" : "待接入"}</span>` },
      { key: "roas", label: "ROAS", type: "number", render: (row) => row.spend ? formatNumber(row.roas) : "待配置" },
      { key: "cpa", label: "CPA", type: "number", render: (row) => row.spend ? formatCurrency(row.cpa, currency) : "待配置" },
    ],
  });
}

function renderAnalytics(analytics, currency) {
  const comparisonNode = document.getElementById("chart-comparison");
  const windowsNode = document.getElementById("chart-windows");
  const funnelNode = document.getElementById("conversion-funnel");
  if (!comparisonNode || !windowsNode || !funnelNode) return;

  const comparisonRows = (analytics?.comparison || []).map((item) => {
    const deltaText = item.delta === null || item.delta === undefined
      ? "—"
      : `${item.delta >= 0 ? "+" : ""}${formatNumber(item.delta)}%`;
    return `
      <article class="mini-card ${escapeHtml(item.tone || "neutral")}">
        <span>${escapeHtml(item.label)}</span>
        <strong>${formatMetric(item.value, item.type, currency)}</strong>
        <small>${item.previous === null || item.previous === undefined ? "—" : `上期 ${formatMetric(item.previous, item.type, currency)}`}</small>
        <em>${deltaText}</em>
      </article>
    `;
  });
  comparisonNode.innerHTML = comparisonRows.length
    ? comparisonRows.join("")
    : '<div class="empty compact">暂无对比数据</div>';

  const windowRows = (analytics?.windows || []).map((item) => `
    <article class="window-card">
      <span>${escapeHtml(item.label)}</span>
      <strong>${formatCurrency(item.revenue, currency)}</strong>
      <small>${formatNumber(item.orders)} 单 · ${item.conversion === null || item.conversion === undefined ? "--" : formatPercent(item.conversion)}</small>
    </article>
  `);
  windowsNode.innerHTML = windowRows.length
    ? windowRows.join("")
    : '<div class="empty compact">暂无周期数据</div>';

  const funnelRows = (analytics?.funnel || []).map((step, index) => {
    const fill = step.baseRate === null || step.baseRate === undefined ? 18 : Math.max(18, Math.min(100, Number(step.baseRate) || 0));
    const baseRate = step.baseRate === null || step.baseRate === undefined ? "—" : `${formatNumber(step.baseRate)}%`;
    const stepRate = step.stepRate === null || step.stepRate === undefined ? "—" : `${formatNumber(step.stepRate)}%`;
    return `
      <div class="funnel-step" style="--fill:${fill}%;">
        <span>${escapeHtml(step.label)}</span>
        <strong>${formatNumber(step.value)}</strong>
        <small>${baseRate}${index > 0 ? ` · ${stepRate}` : ""}</small>
        <i></i>
      </div>
    `;
  });
  funnelNode.innerHTML = funnelRows.length
    ? funnelRows.join("")
    : '<div class="empty compact">暂无漏斗数据</div>';
}

function renderCustomers(customers) {
  if (!customers) return;
  setText("customer-unique", formatNumber(customers.uniqueCustomers));
  setText("customer-new", formatNumber(customers.newCustomers));
  setText("customer-repeat", formatNumber(customers.repeatCustomers));
  setText("customer-repeat-rate", formatPercent(customers.repeatRate));
  const list = document.getElementById("customer-list");
  const rows = (customers.topCustomers || []).map((customer) => `
    <li>
      <span>${escapeHtml(customer.name)}</span>
      <strong>${formatNumber(customer.orders)} 单</strong>
    </li>
  `);
  list.innerHTML = rows.length ? rows.join("") : '<li><span>暂无客户数据</span><strong>--</strong></li>';
}

function renderCustomerInsights(customers, currency) {
  if (!customers) return;
  setText("customer-unique", formatNumber(customers.uniqueCustomers || 0));
  setText("customer-new", formatNumber(customers.newCustomers || 0));
  setText("customer-repeat", formatNumber(customers.repeatCustomers || 0));
  setText("customer-repeat-rate", formatPercent(customers.repeatRate || 0));
  setText("customer-ltv", formatCurrency(customers.averageLtv || 0, currency));
  setText("customer-frequency", formatNumber(customers.averageOrdersPerCustomer || 0));

  const segments = document.getElementById("customer-segments");
  if (segments) {
    segments.innerHTML = (customers.segments || []).map((segment) => `
      <div><span>${escapeHtml(segment.label)}</span><strong>${formatNumber(segment.customers)}</strong><small>${formatCurrency(segment.revenue, currency)}</small></div>
    `).join("") || '<p class="empty">暂无客户分层数据</p>';
  }

  const identifiedNode = document.getElementById("customer-identified");
  if (identifiedNode) {
    identifiedNode.textContent = formatNumber(customers.identifiedOrders || 0);
  }
  const unidentifiedNode = document.getElementById("customer-unidentified");
  if (unidentifiedNode) {
    unidentifiedNode.textContent = formatNumber(customers.unidentifiedOrders || 0);
  }
  const hintNode = document.getElementById("customer-hint");
  if (hintNode) {
    const pieces = [];
    if (customers.identifiedRate !== undefined) {
      pieces.push(`识别率 ${formatPercent(customers.identifiedRate)}`);
    }
    if (customers.missingFields && customers.missingFields.length) {
      pieces.push(`缺少字段：${customers.missingFields.join("、")}`);
    }
    hintNode.textContent = pieces.length ? pieces.join(" · ") : "客户分析已加载完成。";
  }

  const list = document.getElementById("customer-list");
  if (!list) return;
  const rows = [];
  if (customers.hints && customers.hints.length) {
    rows.push(
      ...customers.hints.map((hint) => `
        <li class="signal info">
          <span>${escapeHtml(hint)}</span>
          <strong>字段提示</strong>
        </li>
      `)
    );
  }
  if (customers.requiredFields && customers.requiredFields.length) {
    rows.push(`
      <li class="signal">
        <span>${escapeHtml(customers.requiredFields.join(" · "))}</span>
        <strong>需要字段</strong>
      </li>
    `);
  }
  rows.push(
    ...(customers.topCustomers || []).map((customer) => `
      <li>
        <span>${escapeHtml(customer.name)}${customer.contact && customer.contact !== "--" ? ` · ${escapeHtml(customer.contact)}` : ""}</span>
        <strong>${formatNumber(customer.orders)} 单 · ${formatCurrency(customer.revenue || 0, currency)}</strong>
      </li>
    `)
  );
  list.innerHTML = rows.length ? rows.join("") : '<li><span>暂无客户数据</span><strong>--</strong></li>';
}

function renderOrderStatus(status) {
  const mount = document.getElementById("status-list");
  if (!status || !mount) return;
  const labels = [
    ["paid", "已支付"],
    ["unpaid", "未支付"],
    ["fulfilled", "已发货"],
    ["unfulfilled", "待发货"],
    ["refunded", "退款"],
    ["cancelled", "取消"],
  ];
  mount.innerHTML = labels.map(([key, label]) => {
    const count = status.counts?.[key] || 0;
    const rate = status.rates?.[key] || 0;
    return `
      <div class="status-meter">
        <div>
          <span>${label}</span>
          <strong>${formatNumber(count)} 单</strong>
        </div>
        <i style="width:${Math.max(4, Math.min(100, rate))}%"></i>
      </div>
    `;
  }).join("");
}

function renderProducts(products, currency) {
  renderManagedTable({
    tableId: "product-table",
    rows: products || [],
    emptyText: "暂无商品数据",
    pageSize: 8,
    defaultSort: { key: "revenue", direction: "desc" },
    columns: [
      { key: "title", label: "商品", render: (product) => `<strong>${escapeHtml(product.title)}</strong>` },
      { key: "sku", label: "SKU", render: (product) => escapeHtml(product.sku || "-") },
      { key: "units", label: "售出", type: "number", render: (product) => formatNumber(product.units) },
      { key: "revenue", label: "销售额", type: "number", render: (product) => formatCurrency(product.revenue, currency) },
      { key: "inventory", label: "库存", type: "number", render: (product) => `<span class="pill ${Number(product.inventory) <= 5 ? "warn" : "good"}">${formatNumber(product.inventory)}</span>` },
      { key: "status", label: "状态", render: (product) => escapeHtml(product.status || "active") },
    ],
  });
}

function renderOrders(orders, currency, range) {
  const safeOrders = Array.isArray(orders) ? orders : [];
  const visibleOrders = filterOrders(safeOrders);
  renderManagedTable({
    tableId: "order-table",
    rows: visibleOrders,
    emptyText: "暂无订单数据",
    pageSize: ORDER_PAGE_SIZE,
    meta: `${range?.end || range?.start || "--"} · ${formatNumber(visibleOrders.length)} / ${formatNumber(safeOrders.length)} 单`,
    defaultSort: { key: "createdAt", direction: "desc" },
    columns: [
      { key: "id", label: "订单", render: (order) => `<strong>${escapeHtml(order.id)}</strong><span class="cell-sub">${escapeHtml(order.createdAt)}</span>` },
      { key: "customer", label: "客户" },
      { key: "source", label: "来源", render: (order) => `<span title="${escapeHtml(order.sourceRaw || order.source)}">${escapeHtml(order.source)}</span>` },
      { key: "total", label: "金额", type: "number", render: (order) => formatCurrency(order.total, currency) },
      { key: "status", label: "状态", value: (order) => `${order.status} ${order.fulfillmentStatus}`, render: (order) => `<span class="pill ${statusTone(order)}">${escapeHtml(order.fulfillmentStatus)}</span><span class="cell-sub">${escapeHtml(order.status)}</span>` },
    ],
  });
}

function filterOrders(orders) {
  return (orders || []).filter((order) => {
    const haystack = [
      order.id,
      order.customer,
      order.source,
      order.sourceRaw,
      order.status,
      order.fulfillmentStatus,
    ].join(" ").toLowerCase();
    if (state.orderSearch && !haystack.includes(state.orderSearch)) return false;
    if (state.orderSource && order.source !== state.orderSource) return false;
    if (state.orderStatus && !orderMatchesStatus(order, state.orderStatus)) return false;
    return true;
  });
}

function orderMatchesStatus(order, status) {
  const text = `${order.status || ""} ${order.fulfillmentStatus || ""}`.toLowerCase();
  if (status === "paid") return text.includes("paid") && !text.includes("unpaid");
  if (status === "unpaid") return text.includes("unpaid");
  if (status === "fulfilled") return text.includes("fulfill") && !text.includes("unfulfill");
  if (status === "unfulfilled") return text.includes("unful") || text.includes("pending") || text.includes("open");
  if (status === "refunded") return text.includes("refund");
  if (status === "cancelled") return text.includes("cancel");
  return true;
}

function populateOrderFilters(orders) {
  const sourceSelect = document.getElementById("order-source-filter");
  const current = sourceSelect.value;
  const sources = [...new Set((orders || []).map((order) => order.source).filter(Boolean))].sort();
  sourceSelect.innerHTML = [
    '<option value="">全部来源</option>',
    ...sources.map((source) => `<option value="${escapeHtml(source)}">${escapeHtml(source)}</option>`),
  ].join("");
  sourceSelect.value = sources.includes(current) ? current : "";
  state.orderSource = sourceSelect.value;
}

function exportOrdersCsv() {
  if (!state.payload) return;
  const orders = filterOrders(state.payload.orders || []);
  const header = ["订单号", "日期", "客户", "来源", "原始来源", "金额", "支付状态", "发货状态"];
  const rows = orders.map((order) => [
    order.id,
    order.createdAt,
    order.customer,
    order.source,
    order.sourceRaw || order.source,
    order.total,
    order.status,
    order.fulfillmentStatus,
  ]);
  const csv = [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n");
  const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `sosove-orders-${state.payload.range?.end || localDateString()}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
  showToast(`已导出 ${formatNumber(orders.length)} 条订单`);
}

function csvCell(value) {
  const text = String(value ?? "");
  return `"${text.replaceAll('"', '""')}"`;
}

function renderAlerts(alerts = [], delivery = {}) {
  const mount = document.getElementById("alert-list");
  const visibleAlerts = (alerts || []).filter((alert) => !state.dismissedAlerts.has(alert.id));
  const dismissedCount = (alerts || []).length - visibleAlerts.length;
  const restoreButton = document.getElementById("restore-alerts-btn");
  restoreButton.hidden = dismissedCount === 0;
  restoreButton.textContent = dismissedCount ? `恢复已忽略 ${dismissedCount}` : "恢复已忽略";
  if (!visibleAlerts.length) {
    mount.innerHTML = dismissedCount
      ? '<p class="empty">当前预警已处理或忽略。</p>'
      : '<p class="empty">暂无预警</p>';
    return;
  }
  mount.innerHTML = visibleAlerts.map((alert) => `
    <div class="signal ${escapeHtml(alert.level)}" data-alert-id="${escapeHtml(alert.id)}">
      <div class="signal-copy">
        <strong>${escapeHtml(alert.title)}</strong>
        <span>${escapeHtml(alert.message)}</span>
      </div>
      <div class="signal-actions">
        <button type="button" data-alert-action="orders">查看订单</button>
        <button type="button" data-alert-action="channels">查看渠道</button>
        <button type="button" data-alert-action="dismiss">忽略</button>
        <button type="button" class="notify" data-alert-action="notify" title="${delivery?.configured ? "发送到预警 Webhook" : "发送浏览器通知"}">发送通知</button>
      </div>
    </div>
  `).join("");
}

async function handleAlertAction(event) {
  const button = event.target.closest("[data-alert-action]");
  if (!button || !state.payload) return;
  const signal = button.closest("[data-alert-id]");
  const alertId = signal?.dataset.alertId;
  const alert = (state.payload.alerts || []).find((item) => item.id === alertId);
  if (!alert) return;
  const action = button.dataset.alertAction;
  if (action === "orders") {
    if (alert.orderStatus) {
      state.orderStatus = alert.orderStatus;
      const statusSelect = document.getElementById("order-status-filter");
      if (statusSelect) statusSelect.value = alert.orderStatus;
      resetTablePage("order-table");
      renderOrders(state.payload.orders || [], state.payload.currency, state.payload.range);
    }
    focusPanel("orders");
    return;
  }
  if (action === "channels") {
    focusPanel("channels");
    return;
  }
  if (action === "dismiss") {
    state.dismissedAlerts.add(alert.id);
    writeStoredJson("sosove-dismissed-alerts", [...state.dismissedAlerts]);
    renderAlerts(state.payload.alerts || [], state.payload.alertDelivery || {});
    showToast("该预警已忽略");
    return;
  }
  if (action === "notify") {
    button.disabled = true;
    try {
      await sendAlertNotification(alert);
    } finally {
      button.disabled = false;
    }
  }
}

function focusPanel(id) {
  const panel = document.getElementById(id);
  if (!panel) return;
  panel.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  panel.classList.remove("attention-pulse");
  window.requestAnimationFrame(() => panel.classList.add("attention-pulse"));
  window.setTimeout(() => panel.classList.remove("attention-pulse"), 1700);
}

function restoreDismissedAlerts() {
  state.dismissedAlerts.clear();
  writeStoredJson("sosove-dismissed-alerts", []);
  renderAlerts(state.payload?.alerts || [], state.payload?.alertDelivery || {});
  showToast("已恢复忽略的预警");
}

async function sendAlertNotification(alert) {
  try {
    const result = await fetchJson("/api/alerts/notify", {
      method: "POST",
      body: JSON.stringify({ alert }),
    });
    if (result.delivered) {
      showToast("预警已发送到通知 Webhook");
      return;
    }
  } catch (error) {
    if (error.message && !error.message.includes("route not found")) {
      console.warn("Alert webhook delivery failed", error);
    }
  }
  if ("Notification" in window) {
    const permission = Notification.permission === "default"
      ? await Notification.requestPermission()
      : Notification.permission;
    if (permission === "granted") {
      new Notification(`SOSOVE · ${alert.title}`, { body: alert.message, tag: alert.id });
      showToast("浏览器通知已发送");
      return;
    }
  }
  await copyText(`${alert.title}\n${alert.message}`);
  showToast("预警内容已复制，可直接发送");
}

function renderEvents(events) {
  const mount = document.getElementById("event-list");
  mount.innerHTML = events.map((event) => `
    <li>
      <strong>${escapeHtml(event.title)}</strong>
      <span>${escapeHtml(formatDateTime(event.time))} · ${escapeHtml(event.detail)}</span>
    </li>
  `).join("");
}

function renderManagedTable(config) {
  const table = document.getElementById(config.tableId);
  if (!table) return;
  state.tableConfigs[config.tableId] = config;
  const tableState = getTableState(config);
  const rows = Array.isArray(config.rows) ? [...config.rows] : [];
  const columns = Array.isArray(config.columns) ? config.columns : [];
  let visibleColumns = columns.filter((column) => !tableState.hiddenColumns.includes(column.key));
  if (!visibleColumns.length && columns.length) {
    tableState.hiddenColumns = columns.slice(1).map((column) => column.key);
    visibleColumns = columns.slice(0, 1);
  }

  const sortColumn = columns.find((column) => column.key === tableState.sortKey);
  if (sortColumn) {
    rows.sort((left, right) => compareTableValues(
      tableColumnValue(sortColumn, left),
      tableColumnValue(sortColumn, right),
      sortColumn.type,
      tableState.sortDirection,
    ));
  }

  const pageSize = Math.max(1, Number(tableState.pageSize) || Number(config.pageSize) || 10);
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  tableState.page = Math.min(totalPages, Math.max(1, Number(tableState.page) || 1));
  const start = (tableState.page - 1) * pageSize;
  const pageRows = rows.slice(start, start + pageSize);
  const header = visibleColumns.map((column) => {
    const active = tableState.sortKey === column.key;
    const direction = active ? tableState.sortDirection : "none";
    return `
      <th scope="col" data-column-key="${escapeHtml(column.key)}" aria-sort="${direction === "asc" ? "ascending" : direction === "desc" ? "descending" : "none"}">
        <button type="button" class="table-sort-button ${active ? "active" : ""}" data-table-sort="${escapeHtml(config.tableId)}" data-sort-key="${escapeHtml(column.key)}">
          <span>${escapeHtml(column.label)}</span><i aria-hidden="true">${active ? (direction === "asc" ? "↑" : "↓") : "↕"}</i>
        </button>
      </th>
    `;
  }).join("");
  const body = pageRows.map((row) => `
    <tr>${visibleColumns.map((column) => {
      const rawValue = tableColumnValue(column, row);
      const rendered = typeof column.render === "function" ? column.render(row) : escapeHtml(rawValue ?? "--");
      return `<td data-label="${escapeHtml(column.label)}" data-column-key="${escapeHtml(column.key)}">${rendered || "--"}</td>`;
    }).join("")}</tr>
  `).join("");
  table.innerHTML = `
    <thead><tr>${header}</tr></thead>
    <tbody>${body || `<tr class="empty-row"><td colspan="${Math.max(1, visibleColumns.length)}" class="empty">${escapeHtml(config.emptyText || "暂无数据")}</td></tr>`}</tbody>
  `;
  table.dataset.managedTable = "true";

  const wrapper = table.closest(".table-wrap") || table.parentElement;
  wrapper.classList.add("managed-table-wrap");
  let toolbar = wrapper.querySelector(`.data-table-toolbar[data-table-for="${config.tableId}"]`);
  if (!toolbar) {
    toolbar = document.createElement("div");
    toolbar.className = "data-table-toolbar";
    toolbar.dataset.tableFor = config.tableId;
    wrapper.insertBefore(toolbar, table);
  }
  const end = Math.min(rows.length, start + pageRows.length);
  toolbar.innerHTML = `
    <div class="table-result-count">
      <strong>${rows.length ? `${start + 1}–${end}` : "0"} / ${formatNumber(rows.length)}</strong>
      <span>${escapeHtml(config.meta || "支持排序、分页和列设置")}</span>
    </div>
    <div class="table-display-actions">
      <div class="compact-sort-control">
        <label>排序
          <select data-table-sort-key="${escapeHtml(config.tableId)}">
            ${columns.map((column) => `<option value="${escapeHtml(column.key)}" ${tableState.sortKey === column.key ? "selected" : ""}>${escapeHtml(column.label)}</option>`).join("")}
          </select>
        </label>
        <button type="button" data-table-direction="${escapeHtml(config.tableId)}" aria-label="切换排序方向">${tableState.sortDirection === "asc" ? "↑" : "↓"}</button>
      </div>
      <label>每页
        <select data-table-page-size="${escapeHtml(config.tableId)}">
          ${[6, 8, 10, 20].map((size) => `<option value="${size}" ${pageSize === size ? "selected" : ""}>${size}</option>`).join("")}
        </select>
      </label>
      <div class="column-settings">
        <button type="button" data-table-columns="${escapeHtml(config.tableId)}" aria-expanded="false">列设置</button>
        <div class="column-menu" hidden>
          ${columns.map((column) => `
            <label><input type="checkbox" data-table-column="${escapeHtml(config.tableId)}" value="${escapeHtml(column.key)}" ${tableState.hiddenColumns.includes(column.key) ? "" : "checked"}><span>${escapeHtml(column.label)}</span></label>
          `).join("")}
        </div>
      </div>
    </div>
  `;

  let pager = wrapper.querySelector(`.data-table-pagination[data-table-for="${config.tableId}"]`);
  if (!pager) {
    pager = document.createElement("div");
    pager.className = "data-table-pagination";
    pager.dataset.tableFor = config.tableId;
    wrapper.appendChild(pager);
  }
  pager.innerHTML = `
    <button type="button" class="page-nav" data-table-page="${escapeHtml(config.tableId)}" data-page-delta="-1" ${tableState.page <= 1 ? "disabled" : ""} aria-label="上一页">‹</button>
    <span class="page-status">第 ${rows.length ? tableState.page : 0} / ${rows.length ? totalPages : 0} 页</span>
    <button type="button" class="page-nav" data-table-page="${escapeHtml(config.tableId)}" data-page-delta="1" ${tableState.page >= totalPages || !rows.length ? "disabled" : ""} aria-label="下一页">›</button>
  `;
}

function getTableState(config) {
  if (!state.tableStates[config.tableId]) {
    const stored = readStoredJson(`sosove-table-${config.tableId}`, {});
    const defaultSort = config.defaultSort || {};
    state.tableStates[config.tableId] = {
      page: 1,
      pageSize: Number(stored.pageSize) || Number(config.pageSize) || 10,
      sortKey: stored.sortKey || defaultSort.key || config.columns?.[0]?.key || "",
      sortDirection: ["asc", "desc"].includes(stored.sortDirection)
        ? stored.sortDirection
        : defaultSort.direction || "asc",
      hiddenColumns: Array.isArray(stored.hiddenColumns) ? stored.hiddenColumns : [],
    };
  }
  return state.tableStates[config.tableId];
}

function tableColumnValue(column, row) {
  if (typeof column.value === "function") return column.value(row);
  return row?.[column.key];
}

function compareTableValues(left, right, type, direction) {
  const multiplier = direction === "desc" ? -1 : 1;
  if (type === "number" || type === "boolean") {
    return ((Number(left) || 0) - (Number(right) || 0)) * multiplier;
  }
  const leftText = Array.isArray(left) ? left.join(" ") : String(left ?? "");
  const rightText = Array.isArray(right) ? right.join(" ") : String(right ?? "");
  return leftText.localeCompare(rightText, "zh-CN", { numeric: true, sensitivity: "base" }) * multiplier;
}

function bindManagedTableControls() {
  document.addEventListener("click", (event) => {
    const sortButton = event.target.closest("[data-table-sort]");
    if (sortButton) {
      const tableId = sortButton.dataset.tableSort;
      const tableState = state.tableStates[tableId];
      const config = state.tableConfigs[tableId];
      if (!tableState || !config) return;
      const key = sortButton.dataset.sortKey;
      if (tableState.sortKey === key) {
        tableState.sortDirection = tableState.sortDirection === "asc" ? "desc" : "asc";
      } else {
        const column = config.columns.find((item) => item.key === key);
        tableState.sortKey = key;
        tableState.sortDirection = column?.type === "number" ? "desc" : "asc";
      }
      tableState.page = 1;
      persistTableState(tableId);
      renderManagedTable(config);
      return;
    }
    const pageButton = event.target.closest("[data-table-page]");
    if (pageButton) {
      const tableId = pageButton.dataset.tablePage;
      const tableState = state.tableStates[tableId];
      const config = state.tableConfigs[tableId];
      if (!tableState || !config) return;
      tableState.page += Number(pageButton.dataset.pageDelta) || 0;
      renderManagedTable(config);
      return;
    }
    const directionButton = event.target.closest("[data-table-direction]");
    if (directionButton) {
      const tableId = directionButton.dataset.tableDirection;
      const tableState = state.tableStates[tableId];
      const config = state.tableConfigs[tableId];
      if (!tableState || !config) return;
      tableState.sortDirection = tableState.sortDirection === "asc" ? "desc" : "asc";
      tableState.page = 1;
      persistTableState(tableId);
      renderManagedTable(config);
      return;
    }
    const columnsButton = event.target.closest("[data-table-columns]");
    if (columnsButton) {
      const menu = columnsButton.parentElement.querySelector(".column-menu");
      const nextOpen = menu.hidden;
      document.querySelectorAll(".column-menu").forEach((node) => { node.hidden = true; });
      document.querySelectorAll("[data-table-columns]").forEach((node) => node.setAttribute("aria-expanded", "false"));
      menu.hidden = !nextOpen;
      columnsButton.setAttribute("aria-expanded", nextOpen ? "true" : "false");
      return;
    }
    if (!event.target.closest(".column-settings")) {
      document.querySelectorAll(".column-menu").forEach((node) => { node.hidden = true; });
      document.querySelectorAll("[data-table-columns]").forEach((node) => node.setAttribute("aria-expanded", "false"));
    }
  });
  document.addEventListener("change", (event) => {
    const sortKeySelect = event.target.closest("[data-table-sort-key]");
    if (sortKeySelect) {
      const tableId = sortKeySelect.dataset.tableSortKey;
      const tableState = state.tableStates[tableId];
      const config = state.tableConfigs[tableId];
      if (!tableState || !config) return;
      tableState.sortKey = sortKeySelect.value;
      tableState.page = 1;
      persistTableState(tableId);
      renderManagedTable(config);
      return;
    }
    const pageSizeSelect = event.target.closest("[data-table-page-size]");
    if (pageSizeSelect) {
      const tableId = pageSizeSelect.dataset.tablePageSize;
      const tableState = state.tableStates[tableId];
      const config = state.tableConfigs[tableId];
      if (!tableState || !config) return;
      tableState.pageSize = Number(pageSizeSelect.value) || config.pageSize || 10;
      tableState.page = 1;
      persistTableState(tableId);
      renderManagedTable(config);
      return;
    }
    const columnInput = event.target.closest("[data-table-column]");
    if (!columnInput) return;
    const tableId = columnInput.dataset.tableColumn;
    const tableState = state.tableStates[tableId];
    const config = state.tableConfigs[tableId];
    if (!tableState || !config) return;
    const visibleCount = config.columns.length - tableState.hiddenColumns.length;
    if (!columnInput.checked && visibleCount <= 1) {
      columnInput.checked = true;
      showToast("至少保留一列");
      return;
    }
    const key = columnInput.value;
    tableState.hiddenColumns = columnInput.checked
      ? tableState.hiddenColumns.filter((item) => item !== key)
      : [...new Set([...tableState.hiddenColumns, key])];
    persistTableState(tableId);
    renderManagedTable(config);
  });
}

function persistTableState(tableId) {
  const tableState = state.tableStates[tableId];
  if (!tableState) return;
  writeStoredJson(`sosove-table-${tableId}`, {
    pageSize: tableState.pageSize,
    sortKey: tableState.sortKey,
    sortDirection: tableState.sortDirection,
    hiddenColumns: tableState.hiddenColumns,
  });
}

function resetTablePage(tableId) {
  if (state.tableStates[tableId]) state.tableStates[tableId].page = 1;
}

function formatMetric(value, type, currency) {
  if (value === null || value === undefined || value === "") return "--";
  if (type === "currency") return formatCompactCurrency(value, currency);
  if (type === "percent") return formatPercent(value);
  return formatNumber(value);
}

function formatCompactCurrency(value, currency) {
  const amount = Number(value) || 0;
  const symbol = currencySymbol(currency || "USD");
  const abs = Math.abs(amount);
  if (abs >= 1000000) return `${symbol}${trimNumber(amount / 1000000)}M`;
  if (abs >= 1000) return `${symbol}${trimNumber(amount / 1000)}K`;
  return `${symbol}${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(amount)}`;
}

function formatCurrency(value, currency) {
  const code = currency || "USD";
  if (!currencyFormatters.has(code)) {
    try {
      currencyFormatters.set(code, new Intl.NumberFormat("zh-CN", {
        style: "currency",
        currency: code,
        maximumFractionDigits: code === "JPY" ? 0 : 2,
      }));
    } catch {
      currencyFormatters.set(code, null);
    }
  }
  const formatter = currencyFormatters.get(code);
  if (!formatter) return `${code} ${formatNumber(value)}`;
  return formatter.format(Number(value) || 0);
}

function currencySymbol(currency) {
  try {
    const parts = new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency,
      currencyDisplay: "narrowSymbol",
      maximumFractionDigits: 0,
    }).formatToParts(0);
    return parts.find((part) => part.type === "currency")?.value || `${currency} `;
  } catch {
    return `${currency} `;
  }
}

function trimNumber(value) {
  return Number(value).toFixed(1).replace(/\\.0$/, "");
}

function formatNumber(value) {
  if (value === null || value === undefined || value === "") return "--";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(Number(value) || 0);
}

function formatPercent(value) {
  if (value === null || value === undefined || value === "") return "--";
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(Number(value) || 0)}%`;
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "--";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function statusTone(order) {
  const status = `${order.fulfillmentStatus || ""} ${order.status || ""}`.toLowerCase();
  if (status.includes("fulfill") || status.includes("paid")) return "good";
  if (status.includes("refund") || status.includes("cancel")) return "bad";
  return "warn";
}

function setBusy(isBusy) {
  state.busy = isBusy;
  const progress = document.getElementById("load-progress");
  if (progress) {
    progress.hidden = !isBusy;
    progress.classList.toggle("active", isBusy);
  }
  document.querySelectorAll("button, input, select").forEach((control) => {
    if (control.id === "theme-toggle" || control.closest("#auth-gate")) return;
    if (isBusy) {
      if (!Object.hasOwn(control.dataset, "busyWasDisabled")) {
        control.dataset.busyWasDisabled = control.disabled ? "1" : "0";
      }
      control.disabled = true;
      return;
    }
    if (Object.hasOwn(control.dataset, "busyWasDisabled")) {
      control.disabled = control.dataset.busyWasDisabled === "1";
      delete control.dataset.busyWasDisabled;
    }
  });
  if (!isBusy) applyRolePermissions();
}

function selectDiagnosticTab(tabName = "missing") {
  state.diagnosticTab = ["missing", "issues", "clicks"].includes(tabName) ? tabName : "missing";
  document.querySelectorAll("[data-diagnostic-tab]").forEach((button) => {
    const active = button.dataset.diagnosticTab === state.diagnosticTab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll("[data-diagnostic-pane]").forEach((pane) => {
    const active = pane.dataset.diagnosticPane === state.diagnosticTab;
    pane.hidden = !active;
    pane.classList.toggle("active", active);
  });
}

async function copyClickMappingTemplate() {
  const template = state.payload?.attributionDiagnostics?.mappingTemplate || {};
  if (!Object.keys(template).length) {
    showToast("当前没有待映射 Click ID");
    return;
  }
  await copyText(JSON.stringify(template, null, 2));
  showToast("Click ID 映射模板已复制");
}

function exportManagementReport() {
  const payload = state.payload;
  if (!payload) return;
  const rows = [["模块", "指标", "数值", "备注"]];
  Object.values(payload.kpis || {}).forEach((kpi) => {
    rows.push(["核心指标", kpi.label, kpi.value ?? "", kpi.delta === null ? "" : `环比 ${kpi.delta}%`]);
  });
  const profit = payload.profit || {};
  [
    ["净销售额", profit.netRevenue], ["预计利润", profit.estimatedProfit], ["利润率", `${profit.margin || 0}%`],
    ["商品成本", profit.productCost], ["广告花费", profit.adCost], ["退款", profit.refunds],
  ].forEach(([label, value]) => rows.push(["利润", label, value ?? "", payload.currency || ""]));
  (payload.channels || []).forEach((row) => {
    rows.push(["渠道", row.channel, row.revenue, `${row.orders} 单 / ${row.sessions || 0} 会话`]);
  });
  const quality = payload.syncQuality || {};
  rows.push(["数据质量", "质量评分", quality.score ?? "", quality.grade || ""]);
  rows.push(["数据质量", "归因识别率", `${quality.attributionRate || 0}%`, `重复 ${quality.duplicateOrders || 0} 单`]);
  const reconciliation = payload.reconciliation || {};
  rows.push(["数据对账", "Shopline / GA4", `${reconciliation.shoplineOrders || 0} / ${reconciliation.ga4Purchases ?? "--"}`, reconciliation.label || ""]);
  downloadCsv(rows, `sosove-management-${payload.range?.end || localDateString()}.csv`);
  showToast("经营报表已导出");
}

function exportCampaignsCsv() {
  const campaigns = state.payload?.campaigns?.rows || [];
  const rows = [["渠道", "Campaign", "Adset", "Ad / Content", "订单", "客户", "销售额", "客单价"]];
  campaigns.forEach((row) => rows.push([
    row.channel, row.campaign, row.adset, row.ad, row.orders, row.customers, row.revenue, row.aov,
  ]));
  downloadCsv(rows, `sosove-campaigns-${state.payload?.range?.end || localDateString()}.csv`);
  showToast(`已导出 ${formatNumber(campaigns.length)} 条广告归因`);
}

function downloadCsv(rows, filename) {
  const csv = rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
  const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

function initTheme() {
  const stored = getStoredTheme();
  const initialTheme = stored || state.theme || "dark";
  applyTheme(initialTheme, false);
}

function applyTheme(theme, persist = true) {
  state.theme = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = state.theme;
  const toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.checked = state.theme === "light";
  }
  if (persist) {
    try {
      localStorage.setItem("shopline-monitor-theme", state.theme);
    } catch {
      // Ignore storage failures; theme still applies for this session.
    }
  }
}

function getStoredTheme() {
  try {
    const theme = localStorage.getItem("shopline-monitor-theme");
    return theme === "light" || theme === "dark" ? theme : null;
  } catch {
    return null;
  }
}

function beginRequest() {
  const id = state.requestSeq + 1;
  state.requestSeq = id;
  state.activeRequestSeq = id;
  if (state.abortController) {
    state.abortController.abort();
  }
  state.abortController = new AbortController();
  setBusy(true);
  return { id, signal: state.abortController.signal };
}

function endRequest(id) {
  if (state.activeRequestSeq !== id) return;
  state.abortController = null;
  setBusy(false);
}

function initializeAutoRefresh() {
  const select = document.getElementById("auto-refresh-select");
  if (!select) return;
  let intervalMs = DEFAULT_AUTO_REFRESH_MS;
  try {
    const stored = localStorage.getItem(AUTO_REFRESH_STORAGE_KEY);
    if (stored !== null && [...select.options].some((option) => option.value === stored)) {
      intervalMs = Number(stored) || 0;
    }
  } catch {
    // Use the recommended default when browser storage is unavailable.
  }
  select.value = String(intervalMs);
  scheduleAutoRefresh(intervalMs, { announce: false, persist: false });
}

function scheduleAutoRefresh(intervalMs, { announce = true, persist = false } = {}) {
  if (state.autoRefreshTimer) {
    window.clearInterval(state.autoRefreshTimer);
    state.autoRefreshTimer = null;
  }

  state.autoRefreshMs = intervalMs;
  if (persist) {
    try {
      localStorage.setItem(AUTO_REFRESH_STORAGE_KEY, String(intervalMs));
    } catch {
      // Refresh remains active for this session when storage is unavailable.
    }
  }

  if (!intervalMs) {
    if (announce) showToast("自动刷新已关闭");
    return;
  }

  state.autoRefreshTimer = window.setInterval(() => {
    if (!state.busy && !document.hidden) {
      loadDashboard({ force: state.role !== "viewer" && isLiveRange() });
    }
  }, intervalMs);
  if (announce) showToast(`自动刷新已开启：${Math.round(intervalMs / 60000)} 分钟`);
}

function bindFreshnessRefresh() {
  const refreshWhenVisible = () => {
    if (document.hidden || state.busy || !state.payload) return;
    const syncedAt = new Date(state.payload.source?.syncedAt || 0).getTime();
    const baseline = Number.isFinite(syncedAt) && syncedAt > 0 ? syncedAt : state.lastRenderedAt;
    if (Date.now() - baseline < STALE_REFRESH_AFTER_MS) return;
    loadDashboard({ force: state.role !== "viewer" && isLiveRange() });
  };
  document.addEventListener("visibilitychange", refreshWhenVisible);
  window.addEventListener("focus", refreshWhenVisible);
}

function isLiveRange() {
  return !state.date || state.date === localDateString();
}

function metricsPath() {
  const query = currentQueryPayload();
  const params = new URLSearchParams({ range: query.range });
  if (query.date) params.set("date", query.date);
  Object.entries(query.filters || {}).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return `/api/metrics?${params.toString()}`;
}

function currentQueryPayload() {
  const payload = { range: state.range };
  if (state.range === "1d" && state.date) {
    payload.date = state.date;
  }
  payload.filters = { ...state.filters };
  return payload;
}

function updateControlState() {
  document.querySelectorAll("[data-range]").forEach((button) => {
    button.classList.toggle("active", state.range === button.dataset.range && !state.date);
  });
  const today = localDateString();
  document.getElementById("today-btn").classList.toggle(
    "active",
    state.range === "1d" && state.date === today
  );
  document.getElementById("date-picker").classList.toggle(
    "active",
    state.range === "1d" && Boolean(state.date)
  );
  const dateChoice = document.getElementById("date-choice");
  dateChoice.textContent = state.date || "选择日期";
  dateChoice.classList.toggle("active", state.range === "1d" && Boolean(state.date));
}

function formatRangeCaption(range) {
  if (range.days === 1) {
    return `${range.start} 单日`;
  }
  return `${range.start} 至 ${range.end}`;
}

function localDateString(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function syncToolbarDisclosure() {
  const disclosure = document.getElementById("toolbar-disclosure");
  if (!disclosure) return;
  const mode = window.matchMedia("(max-width: 640px)").matches ? "mobile" : "desktop";
  if (disclosure.dataset.responsiveMode === mode) return;
  disclosure.dataset.responsiveMode = mode;
  disclosure.open = mode === "desktop";
}

function readStoredJson(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    return value === null || value === undefined ? fallback : value;
  } catch {
    return fallback;
  }
}

function readStoredArray(key) {
  const value = readStoredJson(key, []);
  return Array.isArray(value) ? value : [];
}

function writeStoredJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // The interface remains usable when storage is unavailable.
  }
}

async function copyText(value) {
  const text = String(value ?? "");
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const input = document.createElement("textarea");
  input.value = text;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.appendChild(input);
  input.select();
  document.execCommand("copy");
  input.remove();
}

function showError(message) {
  setText("error-panel", message || "请求失败");
  setHidden("error-panel", false);
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 2400);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}

function setHidden(id, hidden) {
  const node = document.getElementById(id);
  if (node) node.hidden = hidden;
}
