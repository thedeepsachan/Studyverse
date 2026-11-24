// Lightweight crisp SVG charts for STUDYVERSE (zero external CDN dependencies)

export function renderBarChart(container, items, { valueKey = "minutes", labelKey = "date", targetValue = 360, unit = "h" } = {}) {
  if (!container) return;
  if (!items || items.length === 0) {
    container.innerHTML = `<div class="muted">No data yet.</div>`;
    return;
  }
  const maxVal = Math.max(targetValue || 60, ...items.map(i => i[valueKey] || 0), 60);
  const w = 540, h = 170, padL = 34, padB = 26, padT = 14, padR = 10;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const barW = Math.max(8, Math.floor(plotW / items.length) - 6);

  let bars = "";
  items.forEach((item, idx) => {
    const val = item[valueKey] || 0;
    const bh = Math.round((val / maxVal) * plotH);
    const x = padL + idx * (plotW / items.length) + 3;
    const y = padT + plotH - bh;
    const color = val >= targetValue ? "var(--green)" : "var(--indigo)";
    const lbl = String(item[labelKey] || "").slice(-5);
    const hrs = (val / 60).toFixed(1);
    bars += `
      <rect x="${x}" y="${y}" width="${barW}" height="${Math.max(2, bh)}" rx="4" fill="${color}" opacity="0.9">
        <title>${item[labelKey]}: ${hrs}h</title>
      </rect>
      <text x="${x + barW / 2}" y="${h - 8}" text-anchor="middle" font-size="10" fill="var(--text-muted)">${lbl}</text>
    `;
  });

  let targetLine = "";
  if (targetValue > 0) {
    const ty = padT + plotH - Math.round((targetValue / maxVal) * plotH);
    targetLine = `<line x1="${padL}" y1="${ty}" x2="${w - padR}" y2="${ty}" stroke="var(--amber)" stroke-dasharray="4 4" stroke-width="1.5" />`;
  }

  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto;display:block">
      <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="var(--border)" />
      ${targetLine}
      ${bars}
    </svg>
  `;
}

export function renderDonutChart(container, pct, label = "Completed", color = "var(--indigo)") {
  if (!container) return;
  const clamped = Math.max(0, Math.min(100, Number(pct) || 0));
  const r = 42, c = 2 * Math.PI * r;
  const offset = c - (clamped / 100) * c;
  container.innerHTML = `
    <svg viewBox="0 0 110 110" style="width:110px;height:110px;display:block;margin:auto">
      <circle cx="55" cy="55" r="${r}" fill="none" stroke="var(--bg-subtle)" stroke-width="10" />
      <circle cx="55" cy="55" r="${r}" fill="none" stroke="${color}" stroke-width="10"
        stroke-dasharray="${c}" stroke-dashoffset="${offset}" stroke-linecap="round"
        transform="rotate(-90 55 55)" />
      <text x="55" y="54" text-anchor="middle" font-size="18" font-weight="700" fill="var(--text)">${clamped}%</text>
      <text x="55" y="70" text-anchor="middle" font-size="10" fill="var(--text-muted)">${label}</text>
    </svg>
  `;
}

export function renderHorizontalBars(container, rows, { labelKey = "label", valueKey = "value", maxVal = 100, suffix = "%" } = {}) {
  if (!container) return;
  if (!rows || !rows.length) {
    container.innerHTML = `<div class="muted">No data yet.</div>`;
    return;
  }
  const m = Math.max(maxVal, ...rows.map(r => r[valueKey] || 0), 1);
  container.innerHTML = rows.map(r => {
    const w = Math.round(((r[valueKey] || 0) / m) * 100);
    return `
      <div style="margin-bottom:10px">
        <div class="between" style="font-size:13px;margin-bottom:4px">
          <span>${r[labelKey]}</span>
          <strong>${r[valueKey]}${suffix}</strong>
        </div>
        <div class="progress-track"><div class="progress-fill" style="width:${w}%"></div></div>
      </div>
    `;
  }).join("");
}
