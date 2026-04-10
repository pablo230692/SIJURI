/**
 * SiJuri PWA — API Integration Module
 * ====================================
 * Drop this into the PWA to replace hardcoded CASES data
 * with live data from the backend API.
 *
 * Usage:
 *   1. Set API_BASE to your backend URL
 *   2. Replace the CASES array and render functions
 *      in sijuri-app.html with this module's logic
 */

// ┌─────────────────────────────────────────────────────┐
// │ SET THIS to your backend URL                        │
// │ Examples:                                           │
// │   http://localhost:3000                              │
// │   https://sijuri-backend.railway.app                 │
// │   https://your-vps-ip:3000                           │
// └─────────────────────────────────────────────────────┘
const API_BASE = "http://localhost:3000";

// ── API Client ──────────────────────────────────────────

const api = {
  async getCases({ status, q, limit = 50, offset = 0 } = {}) {
    const params = new URLSearchParams();
    if (status && status !== "todos") params.set("status", status);
    if (q) params.set("q", q);
    params.set("limit", limit);
    params.set("offset", offset);

    const resp = await fetch(`${API_BASE}/api/cases?${params}`);
    if (!resp.ok) throw new Error(`API error: ${resp.status}`);
    return resp.json();
  },

  async getCase(id) {
    const resp = await fetch(`${API_BASE}/api/cases/${id}`);
    if (!resp.ok) throw new Error(`API error: ${resp.status}`);
    return resp.json();
  },

  async getStats() {
    const resp = await fetch(`${API_BASE}/api/stats`);
    if (!resp.ok) throw new Error(`API error: ${resp.status}`);
    return resp.json();
  },

  async getStatus() {
    const resp = await fetch(`${API_BASE}/api/status`);
    if (!resp.ok) throw new Error(`API error: ${resp.status}`);
    return resp.json();
  },

  async triggerRefresh() {
    const resp = await fetch(`${API_BASE}/api/refresh`, { method: "POST" });
    return resp.json();
  },
};

// ── Replace static renderCases with API-powered version ──

let cachedCases = [];

async function loadCases() {
  try {
    const data = await api.getCases({
      status: activeFilter,
      q: searchQuery,
    });
    cachedCases = data.cases;
    renderCases(cachedCases);
    updateSyncBadge(data.last_refresh);
  } catch (err) {
    console.error("Failed to load cases:", err);
    // Fall back to cached data
    renderCases(cachedCases);
    showOfflineBanner();
  }
}

async function loadStats() {
  try {
    const stats = await api.getStats();
    renderStatsFromAPI(stats);
  } catch (err) {
    console.error("Failed to load stats:", err);
  }
}

function renderStatsFromAPI(stats) {
  const byStatus = stats.by_status || {};
  document.getElementById("statsContainer").innerHTML = `
    <div class="stat-card">
      <div class="stat-number">${byStatus.activo || 0}</div>
      <div class="stat-label">Activos</div>
    </div>
    <div class="stat-card">
      <div class="stat-number">${byStatus.urgente || 0}</div>
      <div class="stat-label">Urgentes</div>
    </div>
    <div class="stat-card">
      <div class="stat-number">${byStatus["en espera"] || 0}</div>
      <div class="stat-label">En Espera</div>
    </div>
  `;
}

function updateSyncBadge(lastRefresh) {
  const badge = document.querySelector(".sync-badge");
  if (lastRefresh) {
    const ago = timeSince(new Date(lastRefresh));
    badge.innerHTML = `<div class="sync-dot"></div> Actualizado ${ago}`;
  }
}

function timeSince(date) {
  const seconds = Math.floor((new Date() - date) / 1000);
  if (seconds < 60) return "ahora";
  if (seconds < 3600) return `hace ${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `hace ${Math.floor(seconds / 3600)}h`;
  return `hace ${Math.floor(seconds / 86400)}d`;
}

function showOfflineBanner() {
  const bar = document.getElementById("refreshBar");
  bar.textContent = "Sin conexión — mostrando datos en caché";
  bar.classList.add("visible");
  setTimeout(() => bar.classList.remove("visible"), 3000);
}

// ── Auto-refresh every 30 seconds ──
setInterval(() => {
  loadCases();
  loadStats();
}, 30000);

// ── Initial load ──
// Call these on page load:
// loadCases();
// loadStats();
