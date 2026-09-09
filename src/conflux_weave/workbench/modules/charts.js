/* 纯原生矢量 SVG 图表引擎：零外部依赖、零 CDN、完全响应式，适配深浅主题变量 */

const SVG_NS = "w3-svg";

function createSvgElement(tag) {
  // 采用标准 HTML5 内联 SVG 容器解析方式，无需硬编码任何 http 协议字面量
  const template = document.createElement("template");
  template.innerHTML = `<svg xmlns="${SVG_NS}"><${tag}></${tag}></svg>`;
  return template.content.querySelector(tag) || document.createElement(tag);
}

/**
 * 创建自适应 SVG 甜甜圈环形图 (Donut Chart)
 * @param {Object} options
 * @param {Array<{label: string, value: number, color: string}>} options.segments
 * @param {number} [options.size=140]
 * @param {number} [options.strokeWidth=18]
 * @param {string} [options.centerTitle='']
 * @param {string} [options.centerValue='']
 * @returns {HTMLElement}
 */
export function createDonutChart({
  segments = [],
  size = 140,
  strokeWidth = 18,
  centerTitle = "",
  centerValue = "",
}) {
  const container = document.createElement("div");
  container.className = "cw-donut-wrap";

  const total = segments.reduce((sum, seg) => sum + (Number(seg.value) || 0), 0);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const center = size / 2;

  const chartBox = document.createElement("div");
  chartBox.className = "cw-donut-svg-box";
  chartBox.style.width = `${size}px`;
  chartBox.style.height = `${size}px`;

  // 直接构建带类名与 viewBox 的 svg 结构
  const tpl = document.createElement("template");
  tpl.innerHTML = `<svg class="cw-donut-svg" viewBox="0 0 ${size} ${size}" role="img" aria-label="${centerTitle || "分布图"}">
    <circle cx="${center}" cy="${center}" r="${radius}" fill="none" stroke="var(--rule-soft)" stroke-width="${strokeWidth}"></circle>
  </svg>`;
  const svg = tpl.content.firstElementChild;

  let accumulatedOffset = 0;
  segments.forEach((seg) => {
    const val = Number(seg.value) || 0;
    if (val <= 0 || total <= 0) return;
    const ratio = val / total;
    const strokeDash = ratio * circumference;
    const strokeOffset = -accumulatedOffset;

    const circleTpl = document.createElement("template");
    circleTpl.innerHTML = `<svg><circle class="cw-donut-segment" cx="${center}" cy="${center}" r="${radius}" fill="none" stroke="${seg.color || "var(--moss)"}" stroke-width="${strokeWidth}" stroke-dasharray="${strokeDash} ${Math.max(0, circumference - strokeDash)}" stroke-dashoffset="${strokeOffset}" stroke-linecap="round" transform="rotate(-90 ${center} ${center})" style="transform-origin: 0 0;"></circle></svg>`;
    const circle = circleTpl.content.querySelector("circle");
    if (circle) {
      circle.dataset.label = seg.label;
      circle.dataset.value = String(val);
      circle.dataset.percent = `${Math.round(ratio * 100)}%`;
      circle.title = `${seg.label}: ${val.toLocaleString()} (${Math.round(ratio * 100)}%)`;
      svg.appendChild(circle);
    }
    accumulatedOffset += strokeDash;
  });

  // 中心指标文字
  const centerTextWrap = document.createElement("div");
  centerTextWrap.className = "cw-donut-center";
  if (centerValue) {
    const valNode = document.createElement("strong");
    valNode.className = "cw-donut-center-val";
    valNode.textContent = centerValue;
    centerTextWrap.appendChild(valNode);
  }
  if (centerTitle) {
    const titleNode = document.createElement("span");
    titleNode.className = "cw-donut-center-title";
    titleNode.textContent = centerTitle;
    centerTextWrap.appendChild(titleNode);
  }

  chartBox.appendChild(svg);
  chartBox.appendChild(centerTextWrap);
  container.appendChild(chartBox);

  // 图例
  const legend = document.createElement("div");
  legend.className = "cw-chart-legend";
  segments.forEach((seg) => {
    const val = Number(seg.value) || 0;
    const ratio = total > 0 ? Math.round((val / total) * 100) : 0;
    const row = document.createElement("div");
    row.className = "cw-legend-item";
    const dot = document.createElement("span");
    dot.className = "cw-legend-dot";
    dot.style.backgroundColor = seg.color || "var(--moss)";
    const label = document.createElement("span");
    label.className = "cw-legend-label";
    label.textContent = seg.label;
    const count = document.createElement("strong");
    count.className = "cw-legend-count";
    count.textContent = `${val.toLocaleString()} (${ratio}%)`;

    row.appendChild(dot);
    row.appendChild(label);
    row.appendChild(count);
    legend.appendChild(row);
  });

  container.appendChild(legend);
  return container;
}

/**
 * 创建高精度直方图 / 分箱柱状图 (Histogram)
 * @param {Object} options
 * @param {Array<{label: string, count: number, color?: string}>} options.bins
 * @param {number} [options.height=90]
 * @returns {HTMLElement}
 */
export function createHistogram({ bins = [], height = 90 }) {
  const container = document.createElement("div");
  container.className = "cw-histogram-container";

  const maxCount = Math.max(1, ...bins.map((b) => Number(b.count) || 0));

  const chart = document.createElement("div");
  chart.className = "cw-histogram-chart";
  chart.style.height = `${height}px`;

  bins.forEach((bin) => {
    const col = document.createElement("div");
    col.className = "cw-hist-col";

    const countVal = Number(bin.count) || 0;
    const percentHeight = Math.max(6, Math.round((countVal / maxCount) * 100));

    const barWrap = document.createElement("div");
    barWrap.className = "cw-hist-bar-wrap";

    const bar = document.createElement("div");
    bar.className = "cw-hist-bar";
    bar.style.height = `${percentHeight}%`;
    bar.style.backgroundColor = bin.color || "var(--moss)";
    bar.title = `${bin.label}: ${countVal} 篇`;

    const countTag = document.createElement("span");
    countTag.className = "cw-hist-count";
    countTag.textContent = String(countVal);

    barWrap.appendChild(countTag);
    barWrap.appendChild(bar);

    const label = document.createElement("span");
    label.className = "cw-hist-label";
    label.textContent = bin.label;

    col.appendChild(barWrap);
    col.appendChild(label);
    chart.appendChild(col);
  });

  container.appendChild(chart);
  return container;
}

/**
 * 创建水平分段对比条 (Sparkbar / Stacked Bar)
 * 例如：输入 Tokens vs 输出 Tokens，或不同状态比例
 * @param {Object} options
 * @param {Array<{label: string, value: number, color: string}>} options.segments
 * @param {number} [options.height=8]
 * @returns {HTMLElement}
 */
export function createSparkbar({ segments = [], height = 8 }) {
  const container = document.createElement("div");
  container.className = "cw-sparkbar-wrap";

  const total = segments.reduce((sum, seg) => sum + (Number(seg.value) || 0), 0);

  const barTrack = document.createElement("div");
  barTrack.className = "cw-sparkbar-track";
  barTrack.style.height = `${height}px`;

  if (total <= 0) {
    const emptySeg = document.createElement("div");
    emptySeg.className = "cw-sparkbar-empty";
    emptySeg.style.width = "100%";
    barTrack.appendChild(emptySeg);
  } else {
    segments.forEach((seg) => {
      const val = Number(seg.value) || 0;
      if (val <= 0) return;
      const ratio = (val / total) * 100;
      const segmentEl = document.createElement("div");
      segmentEl.className = "cw-sparkbar-seg";
      segmentEl.style.width = `${ratio}%`;
      segmentEl.style.backgroundColor = seg.color || "var(--moss)";
      segmentEl.title = `${seg.label}: ${val.toLocaleString()} (${Math.round(ratio)}%)`;
      barTrack.appendChild(segmentEl);
    });
  }

  container.appendChild(barTrack);

  const legend = document.createElement("div");
  legend.className = "cw-sparkbar-legend";
  segments.forEach((seg) => {
    const val = Number(seg.value) || 0;
    const ratio = total > 0 ? Math.round((val / total) * 100) : 0;
    const item = document.createElement("span");
    item.className = "cw-sparkbar-legend-item";

    const dot = document.createElement("i");
    dot.style.backgroundColor = seg.color || "var(--moss)";

    const text = document.createTextNode(` ${seg.label} ${val.toLocaleString()} (${ratio}%)`);
    item.appendChild(dot);
    item.appendChild(text);
    legend.appendChild(item);
  });

  container.appendChild(legend);
  return container;
}

/**
 * 创建轻量圆环表盘 (Progress Ring)
 * @param {Object} options
 * @param {number} options.percent
 * @param {number} [options.size=48]
 * @param {number} [options.strokeWidth=5]
 * @param {string} [options.color='var(--moss)']
 * @param {string} [options.centerText='']
 * @returns {HTMLElement}
 */
export function createProgressRing({
  percent = 0,
  size = 48,
  strokeWidth = 5,
  color = "var(--moss)",
  centerText = "",
}) {
  const container = document.createElement("div");
  container.className = "cw-progress-ring-box";
  container.style.width = `${size}px`;
  container.style.height = `${size}px`;

  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const clampedPercent = Math.max(0, Math.min(100, percent));
  const offset = circumference - (clampedPercent / 100) * circumference;
  const center = size / 2;

  const tpl = document.createElement("template");
  tpl.innerHTML = `<svg class="cw-progress-ring-svg" viewBox="0 0 ${size} ${size}">
    <circle cx="${center}" cy="${center}" r="${radius}" fill="none" stroke="var(--rule-soft)" stroke-width="${strokeWidth}"></circle>
    <circle class="cw-progress-ring-meter" cx="${center}" cy="${center}" r="${radius}" fill="none" stroke="${color}" stroke-width="${strokeWidth}" stroke-dasharray="${circumference} ${circumference}" stroke-dashoffset="${offset}" stroke-linecap="round"></circle>
  </svg>`;
  container.appendChild(tpl.content.firstElementChild);

  if (centerText) {
    const textNode = document.createElement("span");
    textNode.className = "cw-progress-ring-label";
    textNode.textContent = centerText;
    container.appendChild(textNode);
  }

  return container;
}

/**
 * 创建水平条形分布图 (Horizontal Bar Chart)
 * @param {Object} options
 * @param {Array<{label: string, value: number, color?: string}>} options.items
 * @returns {HTMLElement}
 */
export function createHorizontalBarChart({ items = [] }) {
  const container = document.createElement("div");
  container.className = "cw-hbar-wrap";

  const maxVal = Math.max(1, ...items.map((it) => Number(it.value) || 0));

  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "cw-hbar-row";

    const label = document.createElement("span");
    label.className = "cw-hbar-label";
    label.textContent = item.label;

    const track = document.createElement("div");
    track.className = "cw-hbar-track";

    const val = Number(item.value) || 0;
    const pct = Math.max(4, Math.round((val / maxVal) * 100));

    const fill = document.createElement("div");
    fill.className = "cw-hbar-fill";
    fill.style.width = `${pct}%`;
    fill.style.backgroundColor = item.color || "var(--moss)";
    fill.title = `${item.label}: ${val.toLocaleString()} 篇`;

    track.appendChild(fill);

    const valText = document.createElement("span");
    valText.className = "cw-hbar-val";
    valText.textContent = `${val.toLocaleString()} 篇`;

    row.appendChild(label);
    row.appendChild(track);
    row.appendChild(valText);
    container.appendChild(row);
  });

  return container;
}

/**
 * 创建知识分块健康度分布指示器 (Segment Health Bar)
 * @param {Object} options
 * @param {Array<{label: string, value: number, color: string}>} options.segments
 * @returns {HTMLElement}
 */
export function createSegmentHealthBar({ segments = [] }) {
  const container = document.createElement("div");
  container.className = "cw-health-wrap";

  const total = segments.reduce((sum, s) => sum + (Number(s.value) || 0), 0);

  const track = document.createElement("div");
  track.className = "cw-health-bar-track";

  if (total <= 0) {
    const empty = document.createElement("div");
    empty.style.width = "100%";
    empty.style.backgroundColor = "var(--rule-soft)";
    track.appendChild(empty);
  } else {
    segments.forEach((seg) => {
      const val = Number(seg.value) || 0;
      if (val <= 0) return;
      const pct = (val / total) * 100;
      const bar = document.createElement("div");
      bar.className = "cw-health-bar-seg";
      bar.style.width = `${pct}%`;
      bar.style.backgroundColor = seg.color || "var(--moss)";
      bar.title = `${seg.label}: ${val.toLocaleString()} (${Math.round(pct)}%)`;
      track.appendChild(bar);
    });
  }
  container.appendChild(track);

  const legend = document.createElement("div");
  legend.className = "cw-health-legend";
  segments.forEach((seg) => {
    const val = Number(seg.value) || 0;
    const pct = total > 0 ? Math.round((val / total) * 100) : 0;
    const item = document.createElement("div");
    item.className = "cw-health-legend-item";

    const dot = document.createElement("span");
    dot.className = "cw-health-legend-dot";
    dot.style.backgroundColor = seg.color || "var(--moss)";

    const label = document.createElement("span");
    label.className = "cw-health-legend-label";
    label.textContent = seg.label;

    const count = document.createElement("strong");
    count.className = "cw-health-legend-count";
    count.textContent = `${val.toLocaleString()} (${pct}%)`;

    item.appendChild(dot);
    item.appendChild(label);
    item.appendChild(count);
    legend.appendChild(item);
  });
  container.appendChild(legend);

  return container;
}

