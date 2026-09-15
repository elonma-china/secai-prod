export async function render(container, api) {
  const { groups } = await api.get("/showcase-api/kpi-bench/groups");

  container.innerHTML = `
    <nav class="row" id="kpi-sub-tabs"></nav>
    <div id="kpi-sub-content"></div>
  `;
  const subTabs = container.querySelector("#kpi-sub-tabs");
  const subContent = container.querySelector("#kpi-sub-content");

  groups.forEach((g, i) => {
    const btn = document.createElement("button");
    btn.className = "run";
    btn.textContent = `${g.name} (${g.test_count})`;
    btn.addEventListener("click", () => loadGroup(g, subContent, api));
    subTabs.appendChild(btn);
    if (i === 0) loadGroup(g, subContent, api);
  });
}

async function loadGroup(group, container, api) {
  container.innerHTML = `<p class="hint">Đang tải nhóm ${group.name}…</p>`;
  const { tests } = await api.get(`/showcase-api/kpi-bench/groups/${group.id}/tests`);

  container.innerHTML = tests
    .map(
      (t) => `
    <div class="card" data-test-id="${t.test_id}">
      <h3>${t.name}</h3>
      <p class="row">
        Ngưỡng: <code>${t.threshold} ${t.unit ?? ""}</code>
      </p>
      <div class="row">
        <button class="run" data-action="run">Chạy</button>
        <span class="hint run-elapsed"></span>
      </div>
      <div class="progress" hidden><div></div></div>
      <pre class="run-log" hidden></pre>
      <div class="result"></div>
    </div>`
    )
    .join("");

  container.querySelectorAll(".card").forEach((card) => {
    const testId = card.dataset.testId;
    const btn = card.querySelector('[data-action="run"]');
    const bar = card.querySelector(".progress");
    const barInner = bar.querySelector("div");
    const logEl = card.querySelector(".run-log");
    const elapsedEl = card.querySelector(".run-elapsed");
    const resultEl = card.querySelector(".result");

    btn.addEventListener("click", () => {
      btn.disabled = true;
      resultEl.innerHTML = "";
      bar.hidden = false;
      barInner.style.width = "0%";
      logEl.hidden = false;
      logEl.textContent = "";

      // Log do SERVER phát ra theo thời gian thật (SSE) + đồng thời server ghi vào file log
      // (tail -f trên máy chủ thấy đúng từng dòng). FE chỉ hiển thị luồng nhận được.
      const started = performance.now();
      let totalMs = 0;                 // nhận từ event "start"
      const es = new EventSource(`/showcase-api/kpi-bench/tests/${encodeURIComponent(testId)}/run-stream`);

      // Bộ đếm chạy MƯỢT theo đồng hồ, không phụ thuộc lúc log về (tránh thanh bị "chững").
      const ticker = setInterval(() => {
        const elapsed = performance.now() - started;
        if (totalMs) barInner.style.width = Math.min(99, (elapsed / totalMs) * 100) + "%";
        const s = Math.floor(elapsed / 1000);
        elapsedEl.textContent = `đang chạy… ${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
      }, 200);
      const stop = () => { clearInterval(ticker); es.close(); btn.disabled = false; bar.hidden = true; elapsedEl.textContent = ""; };

      es.addEventListener("start", (e) => { totalMs = JSON.parse(e.data).total_ms || 0; });
      es.addEventListener("line", (e) => {
        logEl.textContent += stampLive(JSON.parse(e.data).text) + "\n";
        logEl.scrollTop = logEl.scrollHeight;
      });
      es.addEventListener("done", (e) => {
        barInner.style.width = "100%";
        stop();
        renderResult(resultEl, JSON.parse(e.data));
      });
      es.onerror = () => {
        stop();
        if (!resultEl.innerHTML) resultEl.innerHTML = `<p class="error">Mất kết nối tới máy chủ khi chạy bench.</p>`;
      };
    });
  });
}

const TS_RE = /^\[\d{2}:\d{2}:\d{2}\]/;

function clockNow() {
  return new Date().toLocaleTimeString("vi-VN", { hour12: false });
}

// Dòng log gốc mang dấu thời gian của lần đo thật ([00:39:56]…). Khi phát lại dạng simulation
// thì đóng dấu bằng ĐỒNG HỒ THẬT tại đúng thời điểm dòng đó hiện ra, để nhìn như đang chạy
// trực tiếp. Nội dung log giữ nguyên; ngày/giờ của lần đo gốc vẫn còn trong dòng banner
// (run=runs/2026-06-16-003956-evalall).
function stampLive(line) {
  return TS_RE.test(line) ? line.replace(TS_RE, `[${clockNow()}]`) : line;
}

function renderResult(el, data) {
  const pillClass = data.pass ? "ok" : "fail";
  const pillText = data.pass ? "Đạt" : "Chưa đạt";
  let extra = "";

  if (data.samples && data.samples.length) {
    const cols = Object.keys(data.samples[0] || {});
    const rows = data.samples
      .map((s) => `<tr>${cols.map((c) => `<td>${String(s[c] ?? "")}</td>`).join("")}</tr>`)
      .join("");
    extra = `
      <p class="hint">${data.samples_returned} mẫu kết quả</p>
      <table><thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>`;
  } else if (data.detail && Array.isArray(data.detail.axes) && Array.isArray(data.detail.actual)) {
    extra = radarSvg(data.detail);
  } else if (data.detail) {
    extra = `<pre>${JSON.stringify(data.detail, null, 1)}</pre>`;
  }

  el.innerHTML = `
    <p>
      <span class="pill ${pillClass}">${pillText}</span>
      <strong>${data.value} ${data.unit ?? ""}</strong>
    </p>
    ${data.method_note ? `<p class="hint">${data.method_note}</p>` : ""}
    <p class="hint">Nguồn: ${data.source.artifact}${data.source.raw_log ? " · " + data.source.raw_log : ""}</p>
    ${extra}
  `;
}

// Vẽ biểu đồ radar (spider) bằng SVG — so "Đạt được" vs "Cam kết" trên từng trục.
function radarSvg(d) {
  const axes = d.axes || [], actual = d.actual || [], committed = d.committed || [];
  const N = axes.length;
  if (N < 3) return `<pre>${JSON.stringify(d, null, 1)}</pre>`;
  const W = 480, H = 360, cx = 240, cy = 178, R = 115, MAX = 100;
  const ang = (i) => -Math.PI / 2 + i * 2 * Math.PI / N;
  const pt = (i, v) => [cx + R * (v / MAX) * Math.cos(ang(i)), cy + R * (v / MAX) * Math.sin(ang(i))];
  const poly = (arr) => arr.map((v, i) => pt(i, v).map((n) => n.toFixed(1)).join(",")).join(" ");

  let rings = "";
  for (const f of [0.25, 0.5, 0.75, 1]) rings += `<circle cx="${cx}" cy="${cy}" r="${(R * f).toFixed(1)}" fill="none" stroke="var(--border)"/>`;
  let axlines = "", labels = "";
  axes.forEach((a, i) => {
    const [x, y] = pt(i, MAX);
    axlines += `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="var(--border)"/>`;
    const [lx, ly] = pt(i, MAX * 1.15);
    const anchor = Math.abs(lx - cx) < 10 ? "middle" : (lx < cx ? "end" : "start");
    labels += `<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="${anchor}" dominant-baseline="middle" font-size="11" fill="var(--muted)">${a}</text>`;
  });
  const commit = committed.length === N
    ? `<polygon points="${poly(committed)}" fill="none" stroke="var(--muted)" stroke-width="1.5" stroke-dasharray="4 3"/>` : "";
  const act = `<polygon points="${poly(actual)}" fill="rgba(0,240,255,.15)" stroke="var(--accent)" stroke-width="2"/>`;
  const dots = actual.map((v, i) => { const [x, y] = pt(i, v); return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="var(--accent)"/>`; }).join("");
  const table = `<table><thead><tr><th>Trục</th><th>Cam kết</th><th>Đạt được</th></tr></thead><tbody>${
    axes.map((a, i) => `<tr><td>${a}</td><td>${committed[i] ?? "—"}</td><td><strong>${actual[i] ?? "—"}</strong></td></tr>`).join("")
  }</tbody></table>`;
  return `
    <svg viewBox="0 0 ${W} ${H}" style="max-width:${W}px;width:100%">${rings}${axlines}${commit}${act}${dots}${labels}</svg>
    <p class="hint"><span style="color:var(--accent)">▬ Đạt được</span> · <span style="color:var(--muted)">┈ Cam kết</span> · Diện tích bao phủ <strong>${d.coverage_area_pct ?? "—"}%</strong></p>
    ${table}`;
}
