const state = {
  range: "7d",
  date: "",
  payload: null,
  busy: false,
  theme: document.documentElement.dataset.theme || "dark",
  requestSeq: 0,
  activeRequestSeq: 0,
  abortController: null,
  orderPage: 1,
  orderSearch: "",
  orderSource: "",
  orderStatus: "",
  autoRefreshTimer: null,
};

const ORDER_PAGE_SIZE = 10;

const currencyFormatters = new Map();

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  const datePicker = document.getElementById("date-picker");
  if (datePicker) {
    datePicker.max = localDateString();
  }
  bindControls();
  initScrollTopButton();
  loadDashboard();
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
    const request = beginRequest();
    try {
      const payload = await fetchJson("/api/sync", {
        method: "POST",
        body: JSON.stringify(currentQueryPayload()),
        signal: request.signal,
      });
      if (request.id !== state.activeRequestSeq) return;
      render(payload);
      showToast("同步完成");
    } catch (error) {
      if (error.name !== "AbortError") {
        showError(error.message);
      }
    } finally {
      endRequest(request.id);
    }
  });

  document.getElementById("test-connector-btn").addEventListener("click", async () => {
    const request = beginRequest();
    try {
      const result = await fetchJson("/api/connector/test", { method: "POST", signal: request.signal });
      if (request.id !== state.activeRequestSeq) return;
      showToast(result.ok ? `接口状态：${result.message}` : `接口失败：${result.message}`);
    } catch (error) {
      if (error.name !== "AbortError") {
        showError(error.message);
      }
    } finally {
      endRequest(request.id);
    }
  });

  document.getElementById("auto-refresh-select").addEventListener("change", (event) => {
    scheduleAutoRefresh(Number(event.target.value) || 0);
  });

  document.getElementById("order-search").addEventListener("input", (event) => {
    state.orderSearch = event.target.value.trim().toLowerCase();
    state.orderPage = 1;
    renderOrders(state.payload?.orders || [], state.payload?.currency, state.payload?.range);
  });

  document.getElementById("order-source-filter").addEventListener("change", (event) => {
    state.orderSource = event.target.value;
    state.orderPage = 1;
    renderOrders(state.payload?.orders || [], state.payload?.currency, state.payload?.range);
  });

  document.getElementById("order-status-filter").addEventListener("change", (event) => {
    state.orderStatus = event.target.value;
    state.orderPage = 1;
    renderOrders(state.payload?.orders || [], state.payload?.currency, state.payload?.range);
  });

  document.getElementById("order-export-btn").addEventListener("click", () => {
    exportOrdersCsv();
  });

  document.getElementById("order-prev-btn").addEventListener("click", () => {
    changeOrderPage(-1);
  });

  document.getElementById("order-next-btn").addEventListener("click", () => {
    changeOrderPage(1);
  });
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

async function loadDashboard() {
  const request = beginRequest();
  try {
    const payload = await fetchJson(metricsPath(), { signal: request.signal });
    if (request.id !== state.activeRequestSeq) return;
    render(payload);
  } catch (error) {
    if (error.name !== "AbortError") {
      showError(error.message);
    }
  } finally {
    endRequest(request.id);
  }
}

async function fetchJson(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

function render(payload) {
  state.payload = payload;
  state.orderPage = 1;
  setHidden("error-panel", true);
  setText("range-caption", formatRangeCaption(payload.range));
  setText("source-badge", payload.source.label);
  setText("last-sync", formatDateTime(payload.source.syncedAt));
  renderStatusRail(payload);
  renderConnector(payload);
  renderKpis(payload.kpis, payload.currency);
  renderChart(payload.series, payload.currency);
  renderChannels(payload.channels, payload.currency);
  renderProfit(payload.profit, payload.currency);
  renderAdPerformance(payload.adPerformance, payload.currency);
  renderCustomerInsights(payload.customers, payload.currency);
  renderOrderStatus(payload.orderStatus);
  renderProducts(payload.products, payload.currency);
  populateOrderFilters(payload.orders);
  renderOrders(payload.orders, payload.currency, payload.range);
  renderAlerts(payload.alerts);
  renderEvents(payload.events);

  if (payload.source.errors && payload.source.errors.length) {
    showError(payload.source.errors[0]);
  }
}

function renderStatusRail(payload) {
  setText("rail-mode", payload.source.label);
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
      ${points.map((point) => `<circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="4.5" fill="#071011" stroke="#4cffb1" stroke-width="2"></circle>`).join("")}
      ${labels}
      <text x="${left}" y="18" class="chart-axis">${escapeHtml(revenueLabel)}</text>
      <text x="${width - right}" y="18" text-anchor="end" class="chart-axis">订单峰值 ${maxOrders}</text>
    </svg>
  `;
}

function renderChannels(channels, currency) {
  const rows = channels.map((channel) => `
    <tr>
      <td data-label="来源"><strong>${escapeHtml(channel.channel)}</strong></td>
      <td data-label="订单">${formatNumber(channel.orders)}</td>
      <td data-label="销售额">${formatCurrency(channel.revenue, currency)}</td>
      <td data-label="占比">${formatPercent(channel.share)}</td>
    </tr>
  `);
  renderTable("channel-rows", rows, "暂无渠道数据", 4);
}

function renderProfit(profit, currency) {
  if (!profit) return;
  setText("profit-main", formatCurrency(profit.estimatedProfit, currency));
  setText("profit-margin", `利润率 ${formatPercent(profit.margin)}`);
  setText("profit-adcost", formatCurrency(profit.adCost, currency));
  setText("profit-platform", formatCurrency(profit.platformCost, currency));
  setText("profit-product-cost", formatCurrency(profit.productCost, currency));
  setText("profit-cost-rate", `${formatNumber(profit.productCostRate)}%`);
  setText("profit-fee-rate", `${formatNumber(profit.paymentFeeRate)}%`);
  setText("profit-shipping", formatCurrency(profit.shippingCostPerOrder, currency));
  setText("profit-note", (profit.notes || []).join(" "));
}

function renderAdPerformance(rows, currency) {
  const tableRows = (rows || []).map((row) => `
    <tr>
      <td data-label="渠道"><strong>${escapeHtml(row.channel)}</strong><span class="empty">${formatNumber(row.orders)} 单</span></td>
      <td data-label="销售额">${formatCurrency(row.revenue, currency)}</td>
      <td data-label="花费">${formatCurrency(row.spend, currency)}</td>
      <td data-label="ROAS">${row.spend ? formatNumber(row.roas) : "待配置"}</td>
      <td data-label="CPA">${row.spend ? formatCurrency(row.cpa, currency) : "待配置"}</td>
    </tr>
  `);
  renderTable("ad-rows", tableRows, "暂无渠道数据", 5);
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
  const rows = products.map((product) => {
    const stockClass = Number(product.inventory) <= 5 ? "warn" : "good";
    return `
      <tr>
        <td data-label="商品"><strong>${escapeHtml(product.title)}</strong></td>
        <td data-label="SKU">${escapeHtml(product.sku || "-")}</td>
        <td data-label="售出">${formatNumber(product.units)}</td>
        <td data-label="销售额">${formatCurrency(product.revenue, currency)}</td>
        <td data-label="库存"><span class="pill ${stockClass}">${formatNumber(product.inventory)}</span></td>
        <td data-label="状态">${escapeHtml(product.status || "active")}</td>
      </tr>
    `;
  });
  renderTable("product-rows", rows, "暂无商品数据", 6);
}

function renderOrders(orders, currency, range) {
  const safeOrders = Array.isArray(orders) ? orders : [];
  const visibleOrders = filterOrders(safeOrders);
  const totalOrders = visibleOrders.length;
  const totalPages = totalOrders ? Math.ceil(totalOrders / ORDER_PAGE_SIZE) : 0;
  state.orderPage = totalPages ? Math.min(Math.max(1, state.orderPage || 1), totalPages) : 1;
  const startIndex = totalOrders ? (state.orderPage - 1) * ORDER_PAGE_SIZE : 0;
  const pageOrders = totalOrders ? visibleOrders.slice(startIndex, startIndex + ORDER_PAGE_SIZE) : [];

  const rows = pageOrders.map((order) => `
    <tr>
      <td data-label="订单"><strong>${escapeHtml(order.id)}</strong><span class="empty">${escapeHtml(order.createdAt)}</span></td>
      <td data-label="客户">${escapeHtml(order.customer)}</td>
      <td data-label="来源" title="${escapeHtml(order.sourceRaw || order.source)}">${escapeHtml(order.source)}</td>
      <td data-label="金额">${formatCurrency(order.total, currency)}</td>
      <td data-label="状态"><span class="pill ${statusTone(order)}">${escapeHtml(order.fulfillmentStatus)}</span><span class="empty">${escapeHtml(order.status)}</span></td>
    </tr>
  `);
  renderTable("order-rows", rows, "暂无订单数据", 5);
  renderOrderPagination(totalOrders, totalPages, range, safeOrders.length);
}

function renderOrderPagination(totalOrders, totalPages, range, allOrders = totalOrders) {
  const scopeNode = document.getElementById("order-scope");
  const summaryNode = document.getElementById("order-summary");
  const statusNode = document.getElementById("order-page-status");
  const prevBtn = document.getElementById("order-prev-btn");
  const nextBtn = document.getElementById("order-next-btn");

  if (!scopeNode || !summaryNode || !statusNode || !prevBtn || !nextBtn) return;

  const scopeDate = range?.end || range?.start || "--";
  scopeNode.textContent = scopeDate;
  summaryNode.textContent = totalOrders
    ? `显示 ${formatNumber(totalOrders)} / 共 ${formatNumber(allOrders)} 单`
    : "暂无订单";
  statusNode.textContent = totalOrders ? `${state.orderPage} / ${Math.max(1, totalPages)}` : "0 / 0";
  prevBtn.disabled = totalOrders === 0 || state.orderPage <= 1;
  nextBtn.disabled = totalOrders === 0 || state.orderPage >= Math.max(1, totalPages);
}

function changeOrderPage(delta) {
  if (!state.payload) return;
  const totalOrders = filterOrders(state.payload.orders || []).length;
  const totalPages = totalOrders ? Math.ceil(totalOrders / ORDER_PAGE_SIZE) : 0;
  if (!totalPages) return;
  const nextPage = Math.min(totalPages, Math.max(1, state.orderPage + delta));
  if (nextPage === state.orderPage) return;
  state.orderPage = nextPage;
  renderOrders(state.payload.orders, state.payload.currency, state.payload.range);
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

function renderAlerts(alerts) {
  const mount = document.getElementById("alert-list");
  if (!alerts.length) {
    mount.innerHTML = '<p class="empty">暂无预警</p>';
    return;
  }
  mount.innerHTML = alerts.map((alert) => `
    <div class="signal ${escapeHtml(alert.level)}">
      <strong>${escapeHtml(alert.title)}</strong>
      <span>${escapeHtml(alert.message)}</span>
    </div>
  `).join("");
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

function renderTable(id, rows, emptyText, colspan) {
  const node = document.getElementById(id);
  node.innerHTML = rows.length
    ? rows.join("")
    : `<tr><td colspan="${colspan}" class="empty">${escapeHtml(emptyText)}</td></tr>`;
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
  document.querySelectorAll("button, input, select").forEach((control) => {
    if (control.id === "theme-toggle") return;
    if (!isBusy && control.classList.contains("page-nav")) return;
    control.disabled = isBusy;
  });
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

function scheduleAutoRefresh(intervalMs) {
  if (state.autoRefreshTimer) {
    window.clearInterval(state.autoRefreshTimer);
    state.autoRefreshTimer = null;
  }

  if (!intervalMs) {
    showToast("自动刷新已关闭");
    return;
  }

  state.autoRefreshTimer = window.setInterval(() => {
    if (!state.busy) {
      loadDashboard();
    }
  }, intervalMs);
  showToast(`自动刷新已开启：${Math.round(intervalMs / 60000)} 分钟`);
}

function metricsPath() {
  const params = new URLSearchParams(currentQueryPayload());
  return `/api/metrics?${params.toString()}`;
}

function currentQueryPayload() {
  const payload = { range: state.range };
  if (state.range === "1d" && state.date) {
    payload.date = state.date;
  }
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
