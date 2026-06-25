/* Flow-graph renderer. */
async function loadFlow() {
  const host = document.getElementById("traces");
  try {
    const data = await UI.API.json("/api/flow");
    const traces = data.traces || [];
    if (!traces.length) { host.innerHTML = '<p style="color:var(--text-dim)">No requests yet. Send a chat or run an agent.</p>'; return; }
    host.innerHTML = traces.map(renderTrace).join("");
  } catch (e) {
    host.innerHTML = `<div class="callout">⚠️ ${UI.escapeHtml(e.message)}</div>`;
  }
}

function renderTrace(t) {
  const hops = t.hops.map((h, i) => {
    const cls = h.status === "blocked" || h.status === "error" ? h.status : h.kind;
    const arrow = i < t.hops.length - 1 ? '<span class="flow-arrow">→</span>' : "";
    return `<div class="hop ${cls}" title="${UI.escapeHtml(h.detail || "")}">
        <span class="kind">${h.kind}</span>
        <strong>${UI.escapeHtml(h.node)}</strong>
        <span class="lat">${h.latency_ms} ms</span>
      </div>${arrow}`;
  }).join("");
  return `<div class="flow-trace card" style="background:var(--surface-2)">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <strong>${UI.escapeHtml(t.label)}</strong>
        <span class="pill">${t.total_ms} ms total</span>
      </div>
      <div class="flow-hops">${hops}</div>
    </div>`;
}

document.getElementById("refresh").addEventListener("click", loadFlow);
loadFlow();
