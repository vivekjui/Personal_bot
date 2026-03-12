/* ══════════════════════════════════════════════════
   Noting Bot Dashboard — app.js
   Complete frontend logic for all 8 modules
   ══════════════════════════════════════════════════ */

"use strict";

const API = "";  // Same origin
const APP_ZOOM_STORAGE_KEY = "app_zoom_level";
const APP_ZOOM_MIN = 0.7;
const APP_ZOOM_MAX = 1.8;
const APP_ZOOM_STEP = 0.1;

// ─── GLOBAL STATE ─────────────────────────────────
let allCases = [];
let currentTecJobId = null; // TEC Execution Job State
let currentBidJobId = null; // Bid Downloader Job State
let selectedMergeFiles = []; // PDF Merge Sequencer State
const richEditorSelections = {};
let activeRichEditorId = null;
let currentAppZoom = 1;

// Email drafting globals
let EMAIL_CATEGORIES = [];
let emailLibraryData = [];  // cached templates when library tab active


// ─── INIT ──────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  initializeAccessibilityZoom();
  updateClock();
  setInterval(updateClock, 1000);
  initializeRichEditorToolbars();
  bindRichTextEditors();
  loadDashboard();
  loadNotingTypes();
});


function updateClock() {
  const now = new Date();
  document.getElementById("sidebarTime").textContent =
    now.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  document.getElementById("sidebarDate").textContent =
    now.toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
}

// Sidebar toggle for users who prefer it hidden
function toggleSidebar() {
  const sb = document.querySelector('.sidebar');
  const main = document.querySelector('.main-content');
  if (!sb || !main) return;
  const hidden = sb.classList.toggle('hidden');
  main.classList.toggle('sidebar-hidden', hidden);
}

function initializeAccessibilityZoom() {
  const saved = parseFloat(localStorage.getItem(APP_ZOOM_STORAGE_KEY) || "1");
  applyAppZoom(Number.isFinite(saved) ? saved : 1, false);

  document.addEventListener("wheel", (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    adjustAppZoom(e.deltaY < 0 ? APP_ZOOM_STEP : -APP_ZOOM_STEP);
  }, { passive: false });

  document.addEventListener("keydown", (e) => {
    if (!e.ctrlKey) return;
    if (["=", "+", "Add"].includes(e.key) || ["NumpadAdd"].includes(e.code)) {
      e.preventDefault();
      adjustAppZoom(APP_ZOOM_STEP);
      return;
    }
    if (["-", "_", "Subtract"].includes(e.key) || ["NumpadSubtract"].includes(e.code)) {
      e.preventDefault();
      adjustAppZoom(-APP_ZOOM_STEP);
      return;
    }
    if (e.key === "0" || e.code === "Digit0" || e.code === "Numpad0") {
      e.preventDefault();
      resetAppZoom();
    }
  });
}

function applyAppZoom(level, persist = true) {
  const normalized = Math.min(APP_ZOOM_MAX, Math.max(APP_ZOOM_MIN, Math.round(level * 100) / 100));
  currentAppZoom = normalized;
  document.documentElement.style.zoom = String(normalized);
  const zoomBtn = document.getElementById("zoomLevelBtn");
  if (zoomBtn) zoomBtn.textContent = `${Math.round(normalized * 100)}%`;
  if (persist) {
    localStorage.setItem(APP_ZOOM_STORAGE_KEY, String(normalized));
  }
}

function adjustAppZoom(delta) {
  applyAppZoom(currentAppZoom + delta);
}

function resetAppZoom() {
  applyAppZoom(1);
}

// ─── PAGE NAVIGATION ──────────────────────────────
function showPage(pageId, el) {
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
  document.getElementById("page-" + pageId).classList.add("active");

  // If el is not provided (e.g. from a dashboard card), find the sidebar item
  if (!el) {
    el = document.querySelector(`.nav-item[data-page="${pageId}"]`);
  }
  // Back to Dashboard button visibility
  const backBtn = document.getElementById("back-to-dashboard");
  const navDropdown = document.getElementById("nav-dropdown-container");
  if (backBtn) {
    backBtn.style.display = (pageId === "dashboard") ? "none" : "inline-block";
  }
  if (navDropdown) {
    navDropdown.style.display = (pageId === "dashboard") ? "none" : "block";
  }

  const titles = {
    dashboard: "Dashboard", cases: "Case Registry", noting: "e-Office Noting",
    documents: "Document Downloader", bid: "Bid Downloader", tender: "Tender Scrutiny",
    kb: "🧠 Knowledge Base", ai: "⚙️ AI Settings"
  };
  document.getElementById("pageTitle").textContent = titles[pageId] || pageId;

  // Lazy-load page data
  if (pageId === "kb") { loadKBStats(); loadKBDocs(); loadKBCategories(); }
  if (pageId === "knowhow") { loadKnowHowHistory(); }
  if (pageId === "noting") switchNotingTab("library");
  if (pageId === "email") switchEmailTab("library");
  if (pageId === "ai") loadLLMStatus();
  return false;
}

function toggleNavDropdown() {
  const menu = document.getElementById("nav-dropdown-menu");
  if (menu) menu.classList.toggle("show");
}

window.addEventListener("click", (e) => {
  if (!e.target.closest(".dropdown")) {
    document.querySelectorAll(".dropdown-menu").forEach(m => m.classList.remove("show"));
  }
});

function toast(msg, type = "info") {
  const t = document.getElementById("toast");
  if (!t) return;
  t.textContent = msg;
  t.style.borderColor = type === "success" ? "var(--success)" : type === "error" ? "var(--danger)" : "var(--accent)";
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 3000);
}

// ─── MODAL HELPERS ──────────────────────────────────
function openModal(id) { document.getElementById(id).classList.add("open"); }
function closeModal(id) { document.getElementById(id).classList.remove("open"); }
function closeModalOutside(e, id) { if (e.target.id === id) closeModal(id); }

// ─── API HELPER ────────────────────────────────────
async function apiFetch(path, method = "GET", body = null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(API + path, opts);

  // Handle common non-JSON responses (like 413 or server errors)
  const contentType = r.headers.get("content-type");
  if (!contentType || !contentType.includes("application/json")) {
    if (r.status === 413) {
      throw new Error("File too large. Please try a smaller file or compress it first.");
    }
    const text = await r.text();
    console.error("Non-JSON response:", text);
    throw new Error(`Server Error (${r.status}). The server returned an invalid response format.`);
  }

  return r.json();
}

function loading(el) { el.innerHTML = `<div style="padding:20px;text-align:center"><span class="spinner"></span></div>`; }

// ─── CASES REMOVED ───

// ─── DASHBOARD SUMMARY ─────────────────────────────
async function loadDashboard() {
  const data = await apiFetch("/api/dashboard/summary");
  document.getElementById("stat-cases").textContent = data.active_cases ?? "—";
}

// ─── MODULE 1: NOTING ──────────────────────────────
async function loadNotingTypes() {
  const types = await apiFetch("/api/noting/types");
  const sel = document.getElementById("noting-type");
  if (!sel) return;
  sel.innerHTML = types.map(t => `<option>${esc(t)}</option>`).join("");
}

function fillNotingCaseName() {
  const sel = document.getElementById("noting-case");
  const opt = sel.options[sel.selectedIndex];
  document.getElementById("noting-cost").value = opt?.dataset?.cost ? `Rs. ${opt.dataset.cost} Lakhs` : "";
  document.getElementById("noting-dept").value = opt?.dataset?.dept || "";
}

// ─── STANDARD NOTING LIBRARY ───
let standardLibraryData = [];

function switchNotingTab(tab) {
  document.getElementById("noting-tab-draft").style.display = tab === "draft" ? "block" : "none";
  document.getElementById("noting-tab-library").style.display = tab === "library" ? "block" : "none";
  if (tab === "library") fetchStandardLibrary();
  document.querySelectorAll("#page-noting .tab-pill").forEach(p => p.classList.remove("active"));
  const activeBtn = Array.from(document.querySelectorAll("#page-noting .tab-pill")).find(b => b.textContent.toLowerCase().includes(tab));
  if (activeBtn) activeBtn.classList.add("active");
}

let OFFICIAL_STAGES = [];

async function fetchStages() {
  try {
    const stages = await apiFetch("/api/noting/stages");
    if (Array.isArray(stages)) {
      OFFICIAL_STAGES = stages;
      return stages;
    }
  } catch (e) {
    logger.error("Failed to fetch stages:", e);
  }
  return [];
}

async function showManageStagesModal() {
  const listEl = document.getElementById("manage-stages-list");
  if (!listEl) return;

  await fetchStages();

  listEl.innerHTML = OFFICIAL_STAGES.map((s, idx) => `
    <div style="display:flex; align-items:center; justify-content:space-between; padding:10px; border-bottom:1px solid var(--border)">
      <span style="font-size:13px">${esc(s)}</span>
      <div style="display:flex; gap:5px">
        <button class="btn btn-ghost btn-xs" onclick="moveStage(${idx}, -1)" ${idx === 0 ? 'disabled' : ''}>↑</button>
        <button class="btn btn-ghost btn-xs" onclick="moveStage(${idx}, 1)" ${idx === OFFICIAL_STAGES.length - 1 ? 'disabled' : ''}>↓</button>
        <button class="btn btn-danger btn-xs" onclick="removeStage(${idx})">🗑</button>
      </div>
    </div>
  `).join("");

  openModal("modal-manage-stages");
}

async function addNewStage() {
  const name = v("new-stage-name").trim();
  if (!name) return toast("Stage name required", "error");
  if (OFFICIAL_STAGES.includes(name)) return toast("Stage already exists", "error");

  const newList = [...OFFICIAL_STAGES, name];
  const res = await apiFetch("/api/noting/stages/update", "POST", newList);
  if (res.success) {
    document.getElementById("new-stage-name").value = "";
    OFFICIAL_STAGES = newList;
    await showManageStagesModal();
    await fetchStandardLibrary(); // Refresh sidebar
  }
}

async function moveStage(idx, dir) {
  const newList = [...OFFICIAL_STAGES];
  const target = idx + dir;
  [newList[idx], newList[target]] = [newList[target], newList[idx]];

  const res = await apiFetch("/api/noting/stages/update", "POST", newList);
  if (res.success) {
    OFFICIAL_STAGES = newList;
    await showManageStagesModal();
    await fetchStandardLibrary();
  }
}

async function removeStage(idx) {
  if (!confirm(`Delete stage "${OFFICIAL_STAGES[idx]}"? Templates already in this stage will remain but won't be browsable until re-categorized.`)) return;

  const newList = OFFICIAL_STAGES.filter((_, i) => i !== idx);
  const res = await apiFetch("/api/noting/stages/update", "POST", newList);
  if (res.success) {
    OFFICIAL_STAGES = newList;
    await showManageStagesModal();
    await fetchStandardLibrary();
  }
}

let libraryAutoSaveTimer = null;

async function fetchStandardLibrary(initialStage = null) {
  const stageListEl = document.getElementById("library-stage-list");
  const resultsContainer = document.getElementById("library-results-container");
  if (!stageListEl || !resultsContainer) return;

  try {
    // Force refresh official stages from backend
    await fetchStages();

    const data = await apiFetch("/api/noting/standard");
    standardLibraryData = data || [];

    stageListEl.innerHTML = `<button class="btn btn-ghost btn-sm stage-filter-btn" id="stage-btn-ALL" onclick="renderLibraryStage('ALL')">📦 All Notings</button>` +
      OFFICIAL_STAGES.map(s => `
        <button class="btn btn-ghost btn-sm stage-filter-btn" 
                id="stage-btn-${s.replace(/\s+/g, '-')}"
                onclick="renderLibraryStage('${esc(s)}')">
          📁 ${esc(s)}
        </button>
      `).join("");

    if (initialStage) {
      renderLibraryStage(initialStage);
    } else {
      renderLibraryStage('ALL');
    }
  } catch (e) {
    logger.error("Error fetching library:", e);
    resultsContainer.innerHTML = `<div class="error">Failed to load library data.</div>`;
  }
}

async function renderLibraryStage(stage) {
  const resultsContainer = document.getElementById("library-results-container");
  if (!resultsContainer) return;

  window.currentLibraryStage = stage;
  document.querySelectorAll(".stage-filter-btn").forEach(btn => btn.classList.remove("active"));
  const activeBtnId = stage === 'ALL' ? 'stage-btn-ALL' : `stage-btn-${stage.replace(/\s+/g, '-')}`;
  const activeBtn = document.getElementById(activeBtnId);
  if (activeBtn) activeBtn.classList.add("active");

  let filtered = stage === 'ALL' ? [...standardLibraryData] : standardLibraryData.filter(item => item.stage === stage);

  // Apply Search Filter using backend for smarter/bi-lingual matching
  const query = (document.getElementById("library-search-input")?.value || "").trim();
  if (query) {
    try {
      const res = await apiFetch(`/api/noting/standard?query=${encodeURIComponent(query)}`);
      if (Array.isArray(res)) {
        const matchedIds = new Set(res.map(i => i.id));
        filtered = filtered.filter(item => matchedIds.has(item.id));
      }
    } catch (e) {
      console.error("Library search failed", e);
      // fallback to simple substring filter if backend call fails
      const qlc = query.toLowerCase();
      filtered = filtered.filter(item =>
        item.keyword.toLowerCase().includes(qlc) ||
        item.text.toLowerCase().includes(qlc) ||
        item.stage.toLowerCase().includes(qlc)
      );
    }
  }

  // Apply Sorting
  const sortSelector = document.getElementById("library-sort-selector");
  const sortMode = sortSelector ? sortSelector.value : "date";
  filtered.sort((a, b) => {
    // Primary Rule: Custom entries on top if sorting by 'custom' or on second-level for others
    if (sortMode === 'custom') {
      if (a.is_custom !== b.is_custom) return b.is_custom ? 1 : -1;
    }

    if (sortMode === 'date') {
      const dateA = a.updated_at || "";
      const dateB = b.updated_at || "";
      return dateB.localeCompare(dateA);
    } else if (sortMode === 'stage') {
      return a.stage.localeCompare(b.stage);
    } else {
      // Default: Custom first, then date
      if (a.is_custom !== b.is_custom) return b.is_custom ? 1 : -1;
      const dateA = a.updated_at || "";
      const dateB = b.updated_at || "";
      return dateB.localeCompare(dateA);
    }
  });

  if (!filtered.length) {
    resultsContainer.innerHTML = `<div class="result-box info" style="margin:0; text-align:center; padding:40px"> No notings found for "${esc(stage)}". </div>`;
    return;
  }

  // Populate Add Modal's Stage dropdown if it's empty
  const addStageSel = document.getElementById("add-noting-stage");
  if (addStageSel && (addStageSel.options.length === 0 || addStageSel.options.length < OFFICIAL_STAGES.length)) {
    addStageSel.innerHTML = OFFICIAL_STAGES.map(s => `<option value="${esc(s)}" ${s === stage ? 'selected' : ''}>${esc(s)}</option>`).join("");
  }

  resultsContainer.innerHTML = filtered.map(item => {
    const stageOptions = OFFICIAL_STAGES.map(s => `<option value="${esc(s)}" ${s === item.stage ? 'selected' : ''}>${esc(s)}</option>`).join("");
    const isCustomBadge = item.is_custom ? `<span class="badge badge-success" style="font-size:8px; margin-left:5px">AI REFINED</span>` : '';
    const bodyHtml = looksLikeHtml(item.text) ? normalizeRichEditorHtml(item.text) : textToEditorHtml(item.text);

    return `
    <div class="library-item-card" style="position:relative; background:var(--bg-dark); border:1px solid var(--border); border-radius:10px; padding:15px; margin-bottom:15px">
      <div style="font-size:10px; font-weight:700; color:var(--accent); margin-bottom:10px; display:flex; justify-content:space-between; align-items:center">
        <div style="display:flex; align-items:center; gap:5px">
           <span style="font-weight:700">#${item.id}</span>
           ${isCustomBadge}
           <input type="text" class="form-control btn-xs library-keyword-editor" 
                  value="${esc(item.keyword)}" 
                  placeholder="Keyword..."
                  oninput="handleLibraryUpdate(this, ${item.id}, 'keyword')"
                  style="display:inline-block; width:180px; height:22px; font-size:10px; padding:0 5px; background:rgba(255,255,255,0.05); color:var(--accent); border:1px solid var(--border)" />
        </div>
        <div style="display:flex; gap:10px; align-items:center">
           <span id="library-save-status-${item.id}" style="color:var(--success); display:none; font-size:9px">✓ Saved</span>
           <select class="form-control btn-xs" style="width:auto; height:24px; padding:0 5px; font-size:10px" onchange="moveNoting(${item.id}, this.value)">
              <option disabled selected>Move to stage...</option>
              ${stageOptions}
           </select>
           <button class="btn btn-danger btn-xs" style="height:24px; padding:0 8px; font-size:10px" onclick="deleteNoting(${item.id})">🗑 Delete</button>
        </div>
      </div>
      <div class="library-textarea-wrapper" style="position:relative">
         <div class="form-control library-editor editable-placeholder noting-rich-text" contenteditable="true" data-rich-editor="true"
          oninput="handleLibraryUpdate(this, ${item.id}, 'text')"
          style="font-family:'Tahoma', sans-serif; font-size:11pt; line-height:1.4; background:transparent; border:none; padding:0; height:auto; min-height:60px; resize:none">${bodyHtml}</div>
         <div style="margin-top:10px; display:flex; gap:8px; align-items:center; border-top:1px solid var(--border); padding-top:10px">
            <input type="text" class="form-control btn-xs" id="refine-context-${item.id}" 
                   placeholder="Refinement Context (firm name, GeM contract etc.)" 
                   style="flex:1; font-size:11pt; padding:6px 10px; height:34px; background:rgba(255,255,255,0.03)" />
            <button class="btn btn-ghost btn-sm" onclick="editLibraryNoting(${item.id})" style="height:34px; white-space:nowrap">✏️ Edit</button>
            <button class="btn btn-warning btn-sm" onclick="refineLibraryTemplate(${item.id}, this)" style="height:34px; white-space:nowrap">✨ Refine AI</button>
            <button class="btn btn-primary btn-sm" onclick="copyTextDirect(this)" style="height:34px; white-space:nowrap">📋 Copy</button>
         </div>
      </div>
    </div>
  `}).join("");

  setTimeout(() => {
    bindRichTextEditors(resultsContainer);
    document.querySelectorAll(".library-editor").forEach(ta => autoGrowNotingTextarea(ta));
  }, 10);
}

async function submitNewNoting() {
  const keyword = v("add-noting-keyword").trim();
  const stage = document.getElementById("add-noting-stage").value;
  const addTextEl = document.getElementById("add-noting-text");
  const text = getRichEditorHtml(addTextEl);
  const plainText = getRichEditorText(addTextEl);

  if (!keyword || !plainText) return toast("Keyword and Text are required", "error");

  const res = await apiFetch("/api/noting/library/add", "POST", { stage, keyword, text });
  if (res.success) {
    toast("Noting added successfully!", "success");
    closeModal("modal-add-noting");
    // Clear form
    document.getElementById("add-noting-keyword").value = "";
    if (addTextEl.isContentEditable) addTextEl.innerHTML = ""; else addTextEl.value = "";
    // Refresh library and switch to that stage
    await fetchStandardLibrary(stage);
  } else {
    toast("Error: " + (res.error || "Failed to add"), "error");
  }
}

async function moveNoting(id, newStage) {
  if (!confirm(`Move this noting to "${newStage}"?`)) return fetchStandardLibrary(); // reset dropdown if cancel

  const res = await apiFetch("/api/noting/library/move", "POST", { id, stage: newStage });
  if (res.success) {
    toast("Noting moved successfully!", "success");
    // Refresh current view (it will filter out the moved item).  Use global
    // tracking to avoid the 'All Notings' emoji issue.
    const currentStageName = window.currentLibraryStage || (() => {
      const activeBtn = document.querySelector(".stage-filter-btn.active");
      return activeBtn ? activeBtn.textContent.replace('📁 ', '').trim() : OFFICIAL_STAGES[0];
    })();
    await fetchStandardLibrary(currentStageName);
  } else {
    toast("Error: " + (res.error || "Failed to move"), "error");
  }
}

async function deleteNoting(id) {
  if (!confirm("Really delete this noting template? This cannot be undone.")) return;

  const r = await fetch(`/api/noting/library/delete/${id}`, { method: 'DELETE' });
  const res = await r.json();
  if (res.success) {
    toast("Noting deleted", "info");
    // use the globally tracked stage so that the special "ALL" value is
    // respected; button text for "All Notings" contains an emoji and would
    // otherwise be misinterpreted.
    const currentStageName = window.currentLibraryStage || (() => {
      const activeBtn = document.querySelector(".stage-filter-btn.active");
      return activeBtn ? activeBtn.textContent.replace('📁 ', '').trim() : OFFICIAL_STAGES[0];
    })();
    await fetchStandardLibrary(currentStageName);
  } else {
    toast("Error: " + res.error, "error");
  }
}

function handleLibraryUpdate(el, id, field) {
  if (field === 'text') autoGrowNotingTextarea(el);
  const status = document.getElementById(`library-save-status-${id}`);
  if (status) status.style.display = 'none';

  clearTimeout(el.saveTimeout);
  el.saveTimeout = setTimeout(async () => {
    const payload = { id };
    payload[field] = field === "text" && el.isContentEditable ? getRichEditorHtml(el) : el.value;
    const res = await apiFetch("/api/noting/library/update", "POST", payload);
    if (res.success) {
      if (status) {
        status.style.display = 'inline';
        setTimeout(() => { if (status) status.style.display = 'none'; }, 2000);
      }
      // Update local data if found
      if (window.standardLibraryData) {
        const item = window.standardLibraryData.find(x => x.id === id);
        if (item) item[field] = payload[field];
      }
    }
  }, 1000);
}

function copyTextDirect(btn) {
  const container = btn.closest(".library-textarea-wrapper");
  const editor = container.querySelector(".library-editor");
  copyRichEditorToClipboard(editor).then(() => {
    const original = btn.innerHTML;
    btn.innerHTML = "✅ Copied";
    setTimeout(() => { btn.innerHTML = original; }, 2000);
  });
}

function editLibraryNoting(id) {
  const item = standardLibraryData.find(x => x.id === id);
  if (!item) return toast("Noting not found", "error");

  switchNotingTab("draft");

  const templateEditor = document.getElementById("noting-template-editor");
  const step1 = document.getElementById("noting-step-1");
  const step2 = document.getElementById("noting-step-2");
  const status = document.getElementById("noting-status");
  const resultsList = document.getElementById("noting-results-list");
  const finalSection = document.getElementById("noting-suggestion-section");

  setRichEditorContent(templateEditor, item.text || "", looksLikeHtml(item.text || ""));
  document.getElementById("noting-refine-context").value = "";
  document.getElementById("noting-context").value = item.keyword || "";
  step1.style.display = "none";
  step2.style.display = "block";
  status.style.display = "none";
  resultsList.style.display = "none";
  finalSection.style.display = "none";
  autoGrowNotingTextarea(templateEditor);
  templateEditor.focus();
  templateEditor.scrollIntoView({ behavior: "smooth", block: "center" });
}

function copyNotingSimple(btn, elId) {
  let text = "";
  if (elId.startsWith('std-hi-')) {
    const hidden = document.getElementById(`std-hi-text-${elId.split('-')[2]}`);
    text = hidden ? hidden.textContent : "";
  } else {
    text = document.getElementById(elId).textContent;
  }
  navigator.clipboard.writeText(text);
  const oldText = btn.innerHTML;
  btn.innerHTML = "✅ Copied!";
  setTimeout(() => { btn.innerHTML = oldText; }, 2000);
}

async function refineLibraryTemplate(id, btn) {
  const card = btn.closest(".library-item-card");
  const editor = card.querySelector(".library-editor");
  const contextInput = document.getElementById(`refine-context-${id}`);
  const context = contextInput.value.trim();
  const originalText = getRichEditorText(editor);
  const originalHtml = getRichEditorHtml(editor);

  // No context check required here as backend handles missing context now

  const originalBtnHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Refining...`;

  try {
    const res = await apiFetch('/api/noting/refine', 'POST', {
      text: originalText,
      html: originalHtml,
      modifications: context,
      target_lang: "hindi" // Defaulting to Hindi as per previous refinement logic
    });

    if (res.success) {
      // Instead of updating current, ADD AS NEW entries as per user request
      const addRes = await apiFetch("/api/noting/library/add", "POST", {
        stage: card.querySelector("select").value || "General",
        keyword: card.querySelector(".library-keyword-editor").value + " (Refined)",
        text: res.refined_html || res.refined_text
      });

      if (addRes.success) {
        toast("Verified & Added refined version to library!", "success");
        contextInput.value = "";
        await fetchStandardLibrary(window.currentLibraryStage);
      } else {
        toast("Refined successfully, but failed to save to library.", "warning");
      }
    } else {
      toast(res.error || "Refinement failed", "error");
    }
  } catch (e) {
    toast("Error: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalBtnHtml;
  }
}

async function checkForUpdates() {
  const btn = document.getElementById("btn-check-updates");
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `⏳ Checking...`;

  try {
    const res = await apiFetch("/api/admin/check-updates");
    if (res.success) {
      if (res.has_update) {
        let msg = `New repository update available: ${res.latest}\n`;
        if (res.branch) msg += `Branch: ${res.branch}\n`;
        if (res.latest_sha) msg += `Commit: ${res.latest_sha}\n`;
        msg += `\n${res.notes || 'No commit message provided.'}\n\n`;
        msg += `Would you like to INSTALL it locally now? (The bot will fetch the latest files from GitHub and extract them over the app files)`;

        if (confirm(msg)) {
          installBotUpdate();
        } else if (confirm("Would you like to open the GitHub repository instead?")) {
          window.open(res.url, "_blank");
        }
      } else {
        toast(`You are on the latest repository snapshot (${res.current})`, "success");
      }
    } else {
      toast("Update check failed: " + res.error, "error");
    }
  } catch (e) {
    toast("Network Error: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}

async function installBotUpdate() {
  const btn = document.getElementById("btn-check-updates");
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.classList.add("loading");
  btn.innerHTML = `⏳ Installing...`;

  toast("Fetching latest files from GitHub... please wait.", "info");

  try {
    const res = await apiFetch("/api/admin/install-update", "POST");
    if (res.success) {
      alert("SUCCESS: " + res.message);
      location.reload(); // Refresh to show new version if metadata changed
    } else {
      alert("UPDATE FAILED: " + res.error);
    }
  } catch (e) {
    alert("Installation Error: " + e.message);
  } finally {
    btn.disabled = false;
    btn.classList.remove("loading");
    btn.innerHTML = originalHtml;
  }
}

async function directAIDraft() {
  const context = document.getElementById("noting-context").value.trim();
  if (!context) return toast("Please enter context/subject first", "error");

  const status = document.getElementById("noting-status");
  status.style.display = "block";
  status.className = "result-box info";
  status.innerHTML = `<span class="spinner"></span> Generating fresh AI Draft directly...`;

  try {
    const res = await apiFetch("/api/noting/draft", "POST", { context });
    status.style.display = "none";
    if (res.success) {
      setRichEditorContent(document.getElementById("noting-template-editor"), res.text);
      document.getElementById("noting-step-1").style.display = "none";
      document.getElementById("noting-step-2").style.display = "block";
      autoGrowNotingTextarea(document.getElementById("noting-template-editor")); // still works on div
      toast("Direct AI draft generated!", "success");
    } else {
      toast(res.error || "Generation failed", "error");
    }
  } catch (e) {
    status.innerHTML = `<span style="color:var(--danger)">Error: ${e.message}</span>`;
  }
}

async function searchDraftLibrary() {
  const query = document.getElementById("draft-search-q").value.trim();
  if (!query) return toast("Please enter a keyword or content to search", "error");

  const status = document.getElementById("noting-status");
  const resultsContainer = document.getElementById("noting-results-container");
  const resultsList = document.getElementById("noting-results-list");

  status.style.display = "block";
  status.className = "result-box info";
  status.innerHTML = `<span class="spinner"></span> Searching library for "${esc(query)}"...`;
  resultsList.style.display = "none";

  try {
    const res = await apiFetch("/api/noting/retrieve", "POST", { context: query });
    status.style.display = "none";

    if (res.success && res.notings && res.notings.length > 0) {
      resultsContainer.innerHTML = res.notings.map((n, idx) => {
        const rawHtml = encodeURIComponent(n.text || "");
        return `
        <div class="noting-result-item" data-raw="${rawHtml}" onclick="selectNotingTemplate(this)" style="padding:12px; border-bottom:1px solid var(--border); cursor:pointer; transition:background 0.2s">
            <div style="display:flex; justify-content:space-between; margin-bottom:5px">
                <span class="badge badge-info">${esc(n.source || n.category || 'Library')}</span>
                ${n.score ? `<span style="font-size:10px; color:var(--text-muted)">Match: ${n.score}%</span>` : ''}
                ${n.keyword ? `<span style="font-size:10px; font-weight:700; color:var(--accent)">${esc(n.keyword)}</span>` : ''}
            </div>
            <div style="font-size:12px; color:var(--text); white-space:pre-wrap; max-height:80px; overflow:hidden;">
                ${esc(n.text.substring(0, 200))}...
            </div>
        </div>
      `}).join("");

      resultsList.style.display = "block";
      toast(`Found ${res.notings.length} matches in library`, "success");
    } else {
      toast("No exact matches found. Try brain retrieval.", "warning");
    }
  } catch (e) {
    status.innerHTML = `<span style="color:var(--danger)">Error: ${e.message}</span>`;
  }
}

async function retrieveNotingTemplate() {
  const context = document.getElementById("noting-context").value.trim();
  if (!context) return toast("Please enter context first", "error");

  const status = document.getElementById("noting-status");
  const resultsContainer = document.getElementById("noting-results-container");
  const resultsList = document.getElementById("noting-results-list");

  status.style.display = "block";
  status.className = "result-box info";
  status.innerHTML = `<span class="spinner"></span> Finding matching templates from library...`;
  resultsList.style.display = "none";

  try {
    const res = await apiFetch("/api/noting/retrieve", "POST", { context });
    status.style.display = "none";

    if (res.success && res.notings && res.notings.length > 0) {
      resultsContainer.innerHTML = res.notings.map((n, idx) => {
        const rawHtml = encodeURIComponent(n.text || "");
        return `
        <div class="noting-result-item" data-raw="${rawHtml}" onclick="selectNotingTemplate(this)" style="padding:12px; border-bottom:1px solid var(--border); cursor:pointer; transition:background 0.2s">
            <div style="display:flex; justify-content:space-between; margin-bottom:5px">
                <span class="badge badge-info">${esc(n.category)}</span>
                ${n.score ? `<span style="font-size:10px; color:var(--text-muted)">Match: ${n.score}%</span>` : ''}
            </div>
            <div style="font-size:12px; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                ${esc(n.text.substring(0, 150))}...
            </div>
        </div>
      `}).join("");

      resultsList.style.display = "block";
      toast(`Found ${res.notings.length} matching templates`, "success");
    } else {
      toast("No matching templates found. Starting with blank editor.", "warning");
      selectNotingTemplate(-1); // Open blank editor
    }
  } catch (e) {
    status.innerHTML = `<span style="color:var(--danger)">Error: ${e.message}</span>`;
  }
}

let currentNotingTemplates = [];

function selectNotingTemplate(elOrIdx) {
  const editor = document.getElementById("noting-template-editor");
  const step1 = document.getElementById("noting-step-1");
  const step2 = document.getElementById("noting-step-2");

  let rawText = "";
  if (typeof elOrIdx === 'number') {
    // old style call, not used anymore
    return selectNotingTemplate(document.getElementById(`noting-template-raw-${elOrIdx}`));
  } else {
    const el = elOrIdx;
    rawText = decodeURIComponent(el.dataset.raw || "");
  }

  setRichEditorContent(editor, rawText, looksLikeHtml(rawText));

  step1.style.display = "none";
  step2.style.display = "block";

  // Trigger auto-grow
  setTimeout(() => autoGrowNotingTextarea(editor), 10);
  editor.focus();
}

function autoGrowNotingTextarea(el) {
  if (!el) return;
  el.style.height = "auto";
  // for contenteditable divs we still use scrollHeight
  const h = el.scrollHeight || el.offsetHeight;
  el.style.height = h + "px";
}

async function refineNotingAI() {
  const templateEditor = document.getElementById("noting-template-editor");
  const templateHtml = getRichEditorHtml(templateEditor);
  const templateText = getRichEditorText(templateEditor).replace(/\n{2,}/g, '\n');
  const extraContext = document.getElementById("noting-refine-context").value.trim();
  const langEl = document.getElementById("noting-lang-selector");
  const targetLang = langEl ? langEl.value : "hindi";

  if (!templateText) return toast("Please provide some text in the editor", "error");

  const status = document.getElementById("noting-status");
  const refineBtn = document.getElementById("noting-refine-btn");

  status.style.display = "block";
  status.className = "result-box info";
  status.innerHTML = `<span class="spinner"></span> Running Official GSI Refinement & Translation...`;
  refineBtn.disabled = true;

  try {
    const res = await apiFetch('/api/noting/refine', 'POST', {
      text: templateText,
      html: templateHtml,
      modifications: extraContext,
      target_lang: targetLang
    });
    status.style.display = "none";
    refineBtn.disabled = false;

    if (res.success) {
      const finalEditor = document.getElementById("noting-editor");
      const refinedContent = res.refined_html || res.refined_text;
      setRichEditorContent(finalEditor, refinedContent, Boolean(res.refined_html));
      finalEditor.dataset.aiOriginal = res.refined_text;
      document.getElementById("noting-suggestion-section").style.display = "block";
      autoGrowNotingTextarea(finalEditor);
      toast("Refined successfully!", "success");
      document.getElementById("noting-suggestion-section").scrollIntoView({ behavior: 'smooth' });
    } else {
      toast(res.error || "Refinement failed", "error");
    }
  } catch (e) {
    status.innerHTML = `<span style="color:var(--danger)">Error: ${e.message}</span>`;
    refineBtn.disabled = false;
  }
}

function saveRefinedToLibrary() {
  const editor = document.getElementById("noting-editor");
  const html = getRichEditorHtml(editor);
  const text = getRichEditorText(editor).replace(/\n{2,}/g, '\n');

  if (!text) return toast("Nothing to save", "error");

  // Populate the "Add New" modal with the refined text
  const addTextEl = document.getElementById("add-noting-text");
  setRichEditorContent(addTextEl, html || text, Boolean(html));
  document.getElementById("add-noting-keyword").value = "AI Refined - " + (document.getElementById("noting-context").value.substring(0, 30) || "Untilted");

  // Open the modal
  openModal('modal-add-noting');
}

async function finalizeNoting() {
  const editor = document.getElementById("noting-editor");
  const text = getRichEditorText(editor);
  const html = getRichEditorHtml(editor);
  const originalText = (editor.dataset.aiOriginal || "").trim(); // original stored as html or text
  if (!text) { toast("नोटिंग खाली है।", "error"); return; }

  const el = document.getElementById("noting-save-result");
  el.style.display = "block"; el.className = "result-box";
  el.innerHTML = `<span class="spinner"></span> इतिहास में सहेजा जा रहा है…`;

  const res = await apiFetch("/api/noting/finalize", "POST", {
    text,
    html,
    original_text: originalText
  });

  if (res.success) {
    el.className = "result-box success";
    const learnedMsg = res.learned_patterns ? `<br><span style="font-size:12px; color:var(--success)">🧠 Learned ${res.learned_patterns} preference(s) from your edits for future noting.</span>` : "";
    el.innerHTML = `✅ <strong>इतिहास में सहेजा गया!</strong>${learnedMsg}<br><br>
      <button class="btn btn-ghost btn-sm" onclick="resetNoting()">↩ नई नोटिंग बनाएं</button>`;
    toast("सहेजा गया!", "success");
  } else {
    el.className = "result-box error";
    el.innerHTML = `❌ ${esc(res.error)}`;
  }
}

async function viewNotingHistory() {

  openModal("modal-noting-history");
  const listEl = document.getElementById("noting-history-list");
  listEl.innerHTML = `<div class="empty-state"><span class="spinner"></span> इतिहास खोजा जा रहा है…</div>`;

  const history = await apiFetch(`/api/noting/history/General`);

  if (!history || !history.length) {
    listEl.innerHTML = `<div class="empty-state">इस केस के लिए कोई नोटिंग इतिहास नहीं मिला।</div>`;
    return;
  }

  listEl.innerHTML = history.map(h => `
    <div style="border: 1px solid var(--border); border-radius: 8px; margin-bottom: 12px; background: var(--bg-hover);">
      <div style="padding: 8px 12px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: var(--bg-dark); border-radius: 8px 8px 0 0;">
        <strong>${esc(h.noting_type || 'Unknown')}</strong>
        <span style="font-size: 11px; color: var(--text-muted);">${h.created_at}</span>
      </div>
      <div class="noting-rich-text" style="padding: 12px; font-size: 12px; white-space:normal; max-height: 200px; overflow-y: auto;">${h.content}</div>
      <div style="padding: 8px 12px; border-top: 1px solid var(--border); display: flex; justify-content: flex-end; gap: 10px;">
        <button class="btn btn-ghost btn-sm" onclick="copyHistoryText(this)">📋 Copy</button>
        <button class="btn btn-danger btn-sm" onclick="deleteHistoryItem(${h.id})">🗑 Delete</button>
      </div>
    </div>
  `).join("");
}

// ─── EMAIL LIBRARY & DRAFTING ───

function switchEmailTab(tab) {
  document.getElementById("email-tab-draft").style.display = tab === "draft" ? "block" : "none";
  document.getElementById("email-tab-library").style.display = tab === "library" ? "block" : "none";
  if (tab === "library") fetchEmailLibrary();
  document.querySelectorAll("#page-email .tab-pill").forEach(p => p.classList.remove("active"));
  const activeBtn = Array.from(document.querySelectorAll("#page-email .tab-pill")).find(b => b.textContent.toLowerCase().includes(tab));
  if (activeBtn) activeBtn.classList.add("active");
}

async function viewEmailHistory() {
  openModal("modal-noting-history");
  const listEl = document.getElementById("noting-history-list");
  listEl.innerHTML = `<div class="empty-state"><span class="spinner"></span> Loading history...</div>`;
  const history = await apiFetch(`/api/noting/history/General`);
  if (!history || !history.length) {
    listEl.innerHTML = `<div class="empty-state">No email history found for this case.</div>`;
    return;
  }
  listEl.innerHTML = history.filter(h => h.noting_type === 'Email').map(h => `
    <div style="border: 1px solid var(--border); border-radius: 8px; margin-bottom: 12px; background: var(--bg-hover);">
      <div style="padding: 8px 12px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: var(--bg-dark); border-radius: 8px 8px 0 0;">
        <strong>Email</strong>
        <span style="font-size: 11px; color: var(--text-muted);">${h.created_at}</span>
      </div>
      <div class="noting-rich-text" style="padding: 12px; font-size: 12px; white-space:normal; max-height: 200px; overflow-y: auto;">${h.content}</div>
      <div style="padding: 8px 12px; border-top: 1px solid var(--border); display: flex; justify-content: flex-end; gap: 10px;">
        <button class="btn btn-ghost btn-sm" onclick="copyHistoryText(this)">📋 Copy</button>
        <button class="btn btn-danger btn-sm" onclick="deleteHistoryItem(${h.id})">🗑 Delete</button>
      </div>
    </div>
  `).join("");
}

async function fetchEmailCategories() {
  try {
    const cats = await apiFetch("/api/email/categories");
    if (Array.isArray(cats)) {
      EMAIL_CATEGORIES = cats;
      return cats;
    }
  } catch (e) {
    console.error("Failed to fetch email categories:", e);
  }
  return [];
}

async function showManageEmailCategoriesModal() {
  const listEl = document.getElementById("manage-email-cats-list");
  if (!listEl) return;
  await fetchEmailCategories();
  listEl.innerHTML = EMAIL_CATEGORIES.map((s, idx) => `
    <div style="display:flex; align-items:center; justify-content:space-between; padding:10px; border-bottom:1px solid var(--border)">
      <span style="font-size:13px">${esc(s)}</span>
      <div style="display:flex; gap:5px">
        <button class="btn btn-ghost btn-xs" onclick="moveEmailCategory(${idx}, -1)" ${idx === 0 ? 'disabled' : ''}>↑</button>
        <button class="btn btn-ghost btn-xs" onclick="moveEmailCategory(${idx}, 1)" ${idx === EMAIL_CATEGORIES.length - 1 ? 'disabled' : ''}>↓</button>
        <button class="btn btn-danger btn-xs" onclick="removeEmailCategory(${idx})">🗑</button>
        <button class="btn btn-warning btn-xs" onclick="clearEmailCategoryTemplates('${esc(s)}')">🗑 Templates</button>
      </div>
    </div>
  `).join("");
  openModal("modal-manage-email-cats");
}

async function clearEmailCategoryTemplates(category) {
  if (!confirm(`Delete all library templates in category "${category}"? This cannot be undone.`)) return;
  try {
    const res = await apiFetch('/api/email/library/delete-stages', 'POST', [category]);
    if (res.success) {
      toast(`Removed ${res.removed} templates`, 'success');
      await fetchEmailLibrary(window.currentEmailCategory);
    }
  } catch(e){
    console.error('clearEmailCategoryTemplates error',e);
    toast('Failed to clear templates','error');
  }
}

async function addNewEmailCategory() {
  const name = v("new-email-cat-name").trim();
  if (!name) return toast("Category name required", "error");
  if (EMAIL_CATEGORIES.includes(name)) return toast("Category already exists", "error");
  const newList = [...EMAIL_CATEGORIES, name];
  const res = await apiFetch("/api/email/categories/update", "POST", newList);
  if (res.success) {
    document.getElementById("new-email-cat-name").value = "";
    EMAIL_CATEGORIES = newList;
    await showManageEmailCategoriesModal();
    await fetchEmailLibrary();
  }
}

async function moveEmailCategory(idx, dir) {
  const newList = [...EMAIL_CATEGORIES];
  const target = idx + dir;
  [newList[idx], newList[target]] = [newList[target], newList[idx]];
  const res = await apiFetch("/api/email/categories/update", "POST", newList);
  if (res.success) {
    EMAIL_CATEGORIES = newList;
    await showManageEmailCategoriesModal();
    await fetchEmailLibrary();
  }
}

async function submitNewEmail() {
  const keyword = v("add-email-keyword").trim();
  const stage = document.getElementById("add-email-category").value;
  const addTextEl = document.getElementById("add-email-text");
  const text = getRichEditorHtml(addTextEl);
  const plainText = getRichEditorText(addTextEl);
  if (!keyword || !plainText) return toast("Keyword and Text are required", "error");
  const res = await apiFetch("/api/email/library/add", "POST", { stage, keyword, text });
  if (res.success) {
    toast("Email template added successfully!", "success");
    closeModal("modal-add-email");
    document.getElementById("add-email-keyword").value = "";
    if (addTextEl.isContentEditable) addTextEl.innerHTML = ""; else addTextEl.value = "";
    await fetchEmailLibrary(stage);
  } else {
    toast("Error: " + (res.error || "Failed to add"), "error");
  }
}

async function moveEmail(id, newCate) {
  if (!confirm(`Move this email to "${newCate}"?`)) return fetchEmailLibrary();
  const res = await apiFetch("/api/email/library/move", "POST", { id, stage: newCate });
  if (res.success) {
    toast("Email moved successfully!", "success");
    const currentCat = window.currentEmailCategory || (() => {
      const activeBtn = document.querySelector("#email-category-list .stage-filter-btn.active");
      return activeBtn ? activeBtn.textContent.replace('📁 ', '').trim() : EMAIL_CATEGORIES[0];
    })();
    await fetchEmailLibrary(currentCat);
  } else {
    toast("Error: " + (res.error || "Failed to move"), "error");
  }
}

async function deleteEmail(id) {
  if (!confirm("Really delete this email template? This cannot be undone.")) return;
  const r = await fetch(`/api/email/library/delete/${id}`, { method: 'DELETE' });
  const res = await r.json();
  if (res.success) {
    toast("Email deleted", "info");
    const currentCat = window.currentEmailCategory || (() => {
      const activeBtn = document.querySelector("#email-category-list .stage-filter-btn.active");
      return activeBtn ? activeBtn.textContent.replace('📁 ', '').trim() : EMAIL_CATEGORIES[0];
    })();
    await fetchEmailLibrary(currentCat);
  } else {
    toast("Error: " + res.error, "error");
  }
}

function handleEmailLibraryUpdate(el, id, field) {
  if (field === 'text') autoGrowNotingTextarea(el);
  const status = document.getElementById(`email-library-save-status-${id}`);
  if (status) status.style.display = 'none';
  clearTimeout(el.saveTimeout);
  el.saveTimeout = setTimeout(async () => {
    const payload = { id };
    payload[field] = field === "text" && el.isContentEditable ? getRichEditorHtml(el) : el.value;
    const res = await apiFetch("/api/email/library/update", "POST", payload);
    if (res.success) {
      if (status) {
        status.style.display = 'inline';
        setTimeout(() => { if (status) status.style.display = 'none'; }, 2000);
      }
      if (window.emailLibraryData) {
        const item = window.emailLibraryData.find(x => x.id === id);
        if (item) item[field] = payload[field];
      }
    }
  }, 1000);
}

function copyEmailTextDirect(btn) {
  const container = btn.closest(".library-textarea-wrapper");
  const editor = container.querySelector(".library-editor");
  const html = getRichEditorHtml(editor);
  const text = getRichEditorText(editor);
  const original = btn.innerHTML;
  if (navigator.clipboard && navigator.clipboard.write) {
    const blobInput = new Blob([`<div style="font-family:'Tahoma'; font-size:12pt;">${html}</div>`], { type: 'text/html' });
    const blobText = new Blob([text], { type: 'text/plain' });
    navigator.clipboard.write([new ClipboardItem({ 'text/html': blobInput, 'text/plain': blobText })]).then(() => {
      btn.innerHTML = "✅ Copied";
      setTimeout(() => { btn.innerHTML = original; }, 2000);
    }).catch(() => {
      navigator.clipboard.writeText(text);
      btn.innerHTML = "✅ Copied";
      setTimeout(() => { btn.innerHTML = original; }, 2000);
    });
    return;
  }
  navigator.clipboard.writeText(text).then(() => {
    btn.innerHTML = "✅ Copied";
    setTimeout(() => { btn.innerHTML = original; }, 2000);
  });
}

async function refineEmailLibraryTemplate(id, btn) {
  const card = btn.closest(".library-item-card");
  const editor = card.querySelector(".library-editor");
  const contextInput = document.getElementById(`email-refine-context-${id}`);
  const context = contextInput.value.trim();
  const originalText = getRichEditorText(editor);
  const originalHtml = getRichEditorHtml(editor);
  const originalBtnHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Refining...`;
  try {
    const res = await apiFetch('/api/noting/refine', 'POST', {
      text: originalText,
      html: originalHtml,
      modifications: context,
      target_lang: "hindi"
    });
    if (res.success) {
      const addRes = await apiFetch("/api/email/library/add", "POST", {
        stage: card.querySelector("select").value || "General",
        keyword: card.querySelector(".library-keyword-editor").value + " (Refined)",
        text: res.refined_html || res.refined_text
      });
      if (addRes.success) {
        toast("Refined template saved to library", "success");
        await fetchEmailLibrary(window.currentEmailCategory);
      } else {
        toast("Refined template saved but failed to add to library", "warning");
      }
    } else {
      toast("Refinement failed: " + (res.error || "Unknown"), "error");
    }
  } catch (e) {
    console.error("refineEmailLibraryTemplate error", e);
    toast("Refinement error", "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalBtnHtml;
  }
}

async function searchEmailDraftLibrary() {
  const q = v("email-draft-search-q").trim();
  if (!q) return;
  try {
    const res = await apiFetch(`/api/email/standard?query=${encodeURIComponent(q)}`);
    if (Array.isArray(res)) {
      const container = document.getElementById("email-results-container");
      if (container) {
        container.innerHTML = res.slice(0, 10).map(item => `<div class="result-item" onclick="selectEmailTemplate(${item.id})" style="padding:10px; border-bottom:1px solid var(--border); cursor:pointer">${esc(item.keyword)}<div style="font-size:11px;color:var(--text-muted)>${esc(item.stage)}</div></div>`).join("");
        document.getElementById("email-results-list").style.display = "block";
      }
    }
  } catch (e) {
    console.error("searchEmailDraftLibrary error", e);
  }
}

async function fetchEmailLibrary(initialCat = null) {
  const listEl = document.getElementById("email-category-list");
  const resultsContainer = document.getElementById("email-library-results-container");
  if (!listEl || !resultsContainer) return;
  try {
    await fetchEmailCategories();
    const data = await apiFetch("/api/email/library");
    emailLibraryData = data || [];
    const allLabel = EMAIL_CATEGORIES.find(c => c.toLowerCase().includes('all')) || 'All Emails';
    listEl.innerHTML = `<button class="btn btn-ghost btn-sm stage-filter-btn" id="email-cat-btn-ALL" onclick="renderEmailLibraryStage('ALL')">📦 ${esc(allLabel)}</button>` +
      EMAIL_CATEGORIES.map(s => `
        <button class="btn btn-ghost btn-sm stage-filter-btn" 
                id="email-cat-btn-${s.replace(/\s+/g, '-') }"
                onclick="renderEmailLibraryStage('${esc(s)}')">
          📁 ${esc(s)}
        </button>
      `).join("" );
    if (initialCat) {
      renderEmailLibraryStage(initialCat);
    } else {
      renderEmailLibraryStage('ALL');
    }
  } catch (e) {
    console.error("Error fetching email library:", e);
    resultsContainer.innerHTML = `<div class="error">Failed to load email library data.</div>`;
  }
}

async function renderEmailLibraryStage(cat) {
  const resultsContainer = document.getElementById("email-library-results-container");
  if (!resultsContainer) return;

  window.currentEmailCategory = cat;
  document.querySelectorAll("#email-category-list .stage-filter-btn").forEach(btn => btn.classList.remove("active"));
  const activeBtnId = cat === 'ALL' ? 'email-cat-btn-ALL' : `email-cat-btn-${cat.replace(/\s+/g, '-')}`;
  const activeBtn = document.getElementById(activeBtnId);
  if (activeBtn) activeBtn.classList.add("active");

  let filtered = cat === 'ALL' ? [...emailLibraryData] : emailLibraryData.filter(item => item.stage === cat);

  const query = (document.getElementById("email-library-search-input")?.value || "").trim();
  if (query) {
    try {
      const res = await apiFetch(`/api/email/standard?query=${encodeURIComponent(query)}`);
      if (Array.isArray(res)) {
        const ids = new Set(res.map(i => i.id));
        filtered = filtered.filter(item => ids.has(item.id));
      }
    } catch (e) {
      console.error("Email library search failed", e);
      const qlc = query.toLowerCase();
      filtered = filtered.filter(item =>
        item.keyword.toLowerCase().includes(qlc) ||
        item.text.toLowerCase().includes(qlc) ||
        item.stage.toLowerCase().includes(qlc)
      );
    }
  }

  const sortSelector = document.getElementById("email-library-sort-selector");
  const sortMode = sortSelector ? sortSelector.value : "date";
  filtered.sort((a,b) => {
    if (sortMode === 'custom') {
      if (a.is_custom !== b.is_custom) return b.is_custom ? 1 : -1;
    }
    if (sortMode === 'date') {
      return (b.updated_at||"").localeCompare(a.updated_at||"");
    } else if (sortMode === 'category') {
      return a.stage.localeCompare(b.stage);
    } else {
      if (a.is_custom !== b.is_custom) return b.is_custom ? 1 : -1;
      return (b.updated_at||"").localeCompare(a.updated_at||"");
    }
  });

  if (!filtered.length) {
    resultsContainer.innerHTML = `<div class="result-box info" style="margin:0; text-align:center; padding:40px"> No emails found for "${esc(cat)}". </div>`;
    return;
  }

  const addCatSel = document.getElementById("add-email-category");
  if (addCatSel && (addCatSel.options.length === 0 || addCatSel.options.length < EMAIL_CATEGORIES.length)) {
    addCatSel.innerHTML = EMAIL_CATEGORIES.map(s => `<option value="${esc(s)}" ${s === cat ? 'selected' : ''}>${esc(s)}</option>`).join("");
  }

  resultsContainer.innerHTML = filtered.map(item => {
    const catOptions = EMAIL_CATEGORIES.map(s => `<option value="${esc(s)}" ${s === item.stage ? 'selected' : ''}>${esc(s)}</option>`).join("");
    const isCustomBadge = item.is_custom ? `<span class="badge badge-success" style="font-size:8px; margin-left:5px">AI REFINED</span>` : '';
    const bodyHtml = looksLikeHtml(item.text) ? normalizeRichEditorHtml(item.text) : textToEditorHtml(item.text);
    return `
    <div class="library-item-card" style="position:relative; background:var(--bg-dark); border:1px solid var(--border); border-radius:10px; padding:15px; margin-bottom:15px">
      <div style="font-size:10px; font-weight:700; color:var(--accent); margin-bottom:10px; display:flex; justify-content:space-between; align-items:center">
        <div style="display:flex; align-items:center; gap:5px">
           <span style="font-weight:700">#${item.id}</span>
           ${isCustomBadge}
           <input type="text" class="form-control btn-xs library-keyword-editor" 
                  value="${esc(item.keyword)}" 
                  placeholder="Keyword..."
                  oninput="handleEmailLibraryUpdate(this, ${item.id}, 'keyword')"
                  style="display:inline-block; width:180px; height:22px; font-size:10px; padding:0 5px; background:rgba(255,255,255,0.05); color:var(--accent); border:1px solid var(--border)" />
        </div>
      </div>
      <div class="library-textarea-wrapper" style="position:relative">
         <div class="form-control library-editor editable-placeholder noting-rich-text" contenteditable="true" data-rich-editor="true"
          oninput="handleEmailLibraryUpdate(this, ${item.id}, 'text')"
          style="font-family:'Tahoma', sans-serif; font-size:11pt; line-height:1.4; background:transparent; border:none; padding:0; height:auto; min-height:60px; resize:none">${bodyHtml}</div>
         <div style="margin-top:10px; display:flex; gap:8px; align-items:center; border-top:1px solid var(--border); padding-top:10px">
            <input type="text" class="form-control btn-xs" id="email-refine-context-${item.id}" 
                   placeholder="Refinement Context (firm name, etc.)" 
                   style="flex:1; font-size:11pt; padding:6px 10px; height:34px; background:rgba(255,255,255,0.03)" />
            <button class="btn btn-warning btn-sm" onclick="refineEmailLibraryTemplate(${item.id}, this)" style="height:34px; white-space:nowrap">✨ Refine AI</button>
            <button class="btn btn-primary btn-sm" onclick="copyEmailTextDirect(this)" style="height:34px; white-space:nowrap">📋 Copy</button>
         </div>
      </div>
    </div>
  `;
  }).join("");
}

function copyHistoryText(btn) {
  const textDiv = btn.parentElement.previousElementSibling;
  let html = textDiv.innerHTML || textDiv.textContent || "";
  html = html.replace(/(<br\s*\/?>\s*){2,}/g, '<br>');
  let txt = textDiv.textContent || "";
  txt = txt.replace(/\n{2,}/g, '\n');
  const styled = `<div style="font-family:'Tahoma'; font-size:12pt;">${html}</div>`;
  if (navigator.clipboard && navigator.clipboard.write) {
    const blobInput = new Blob([styled], { type: 'text/html' });
    const blobText = new Blob([txt], { type: 'text/plain' });
    navigator.clipboard.write([new ClipboardItem({ 'text/html': blobInput, 'text/plain': blobText })]).then(() => {
      toast("Text copied to clipboard!", "success");
    }).catch(() => {
      navigator.clipboard.writeText(txt);
      toast("Copied as plain text", "warning");
    });
  } else {
    navigator.clipboard.writeText(txt);
    toast("Text copied to clipboard!", "success");
  }
}

async function deleteHistoryItem(id) {
  if (!confirm("क्या आप इस नोटिंग को इतिहास से हटाना चाहते हैं?")) return;

  const res = await apiFetch(`/api/noting/history/${id}`, "DELETE");
  if (res.success) {
    toast("इतिहास से हटाया गया", "success");
    viewNotingHistory(); // Refresh
  } else {
    toast("हटाना विफल: " + (res.error || "Unknown error"), "error");
  }
}

function resetNoting() {
  document.getElementById("noting-step-1").style.display = "block";
  document.getElementById("noting-step-2").style.display = "none";
  document.getElementById("noting-suggestion-section").style.display = "none";
  document.getElementById("noting-status").style.display = "none";
  document.getElementById("noting-results-list").style.display = "none";
  document.getElementById("noting-context").value = "";
  const templ = document.getElementById("noting-template-editor");
  if (templ.isContentEditable) templ.innerHTML = ""; else templ.value = "";
  document.getElementById("noting-editor").dataset.aiOriginal = "";
}

function copyNotingText() {
  const editor = document.getElementById("noting-editor");
  copyRichEditorToClipboard(editor);
}

function copyTemplateEditorText() {
  const editor = document.getElementById("noting-template-editor");
  copyRichEditorToClipboard(editor);
}

async function processZip() {
  const fileInput = document.getElementById("zip-file");
  const el = document.getElementById("zip-process-result");

  if (!fileInput.files.length) { toast("कम से कम एक ZIP फ़ाइल चुनें", "error"); return; }

  el.style.display = "block"; el.className = "result-box";
  el.innerHTML = `<span class="spinner"></span> ZIP फ़ाइलें अपलोड और प्रोसेस हो रही हैं...`;

  const fd = new FormData();
  for (const file of fileInput.files) {
    fd.append("files", file);
  }

  try {
    const r = await fetch("/api/documents/process-zip", { method: "POST", body: fd });
    if (!r.ok) {
      const text = await r.text();
      throw new Error(`Upload failed (${r.status}). ${text.substring(0, 100)}`);
    }
    const res = await r.json();
    handleZipProcessResponse(res, el);
  } catch (e) {
    el.className = "result-box error";
    el.innerHTML = `❌ Network Error: ${esc(String(e))}`;
  }
}

async function processLocalFolder() {
  const path = v("zip-folder-path").trim();
  const el = document.getElementById("zip-process-result");

  if (!path) { toast("कृपया फोल्डर का पूर्ण पाथ (Full Path) दर्ज करें", "error"); return; }

  el.style.display = "block"; el.className = "result-box";
  el.innerHTML = `<span class="spinner"></span> लोकल फोल्डर में ZIP फाइलें खोजी और प्रोसेस की जा रही हैं: <code>${esc(path)}</code>...`;

  try {
    // We send JSON with folder_path
    const res = await apiFetch("/api/documents/process-zip-local", "POST", { folder_path: path });
    handleZipProcessResponse(res, el);
  } catch (e) {
    el.className = "result-box error";
    el.innerHTML = `❌ API Error: ${esc(String(e))}`;
  }
}

function handleZipProcessResponse(res, el) {
  if (res.success) {
    el.className = "result-box success";
    let html = `✅ <strong>Processing Complete!</strong><br><br>`;
    if (res.results && res.results.length) {
      res.results.forEach(item => {
        html += `<div style="margin-bottom:10px; border-bottom:1px solid var(--border); padding-bottom:5px">
          <strong>Original:</strong> ${esc(item.original_zip)}<br>
          ${item.error ? `<span style="color:var(--danger)">❌ ${esc(item.error)}</span>` :
            `<strong>Output:</strong> ${item.output_files.map(f => `<code>${esc(f)}</code>`).join(", ")}`}
        </div>`;
      });
    } else {
      html += `No matching ZIP files were processed.`;
    }
    el.innerHTML = html;
    if (res.output_dir) {
      el.innerHTML += `<div style="margin-top:15px">
        <button class="btn btn-primary btn-sm" onclick="openFolder('${res.output_dir.replace(/\\/g, '\\\\')}')">📂 Open Output Folder</button>
      </div>`;
    }
    toast("Zip processing successful!", "success");
  } else {
    el.className = "result-box error";
    el.innerHTML = `❌ ${esc(res.error || "Processing failed")}`;
    toast("Error: " + (res.error || "Failed"), "error");
  }
}

function handleMergeFileSelect(input) {
  if (!input.files.length) return;
  // Convert FileList to array and add to our tracking
  for (let i = 0; i < input.files.length; i++) {
    selectedMergeFiles.push(input.files[i]);
  }
  // Clear the input so same files can be re-selected if removed
  input.value = "";
  renderMergeQueue();
}

function renderMergeQueue() {
  const container = document.getElementById("merge-queue-container");
  const list = document.getElementById("merge-file-list");

  if (selectedMergeFiles.length === 0) {
    container.style.display = "none";
    return;
  }

  container.style.display = "block";
  list.innerHTML = "";

  selectedMergeFiles.forEach((file, index) => {
    const item = document.createElement("div");
    item.className = "dropdown-item"; // Reuse existing style for consistency
    item.style.justifyContent = "space-between";
    item.style.padding = "8px 12px";
    item.style.borderBottom = "1px solid var(--border)";

    item.innerHTML = `
      <div style="display:flex; align-items:center; gap:10px; overflow:hidden;">
        <span style="color:var(--accent); font-size:12px; font-weight:bold;">${index + 1}.</span>
        <span style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:12px;">${file.name}</span>
      </div>
      <div style="display:flex; gap:5px; flex-shrink:0;">
        <button class="btn btn-ghost btn-sm" onclick="moveMergeFile(${index}, -1)" ${index === 0 ? 'disabled' : ''} title="Move Up">↑</button>
        <button class="btn btn-ghost btn-sm" onclick="moveMergeFile(${index}, 1)" ${index === selectedMergeFiles.length - 1 ? 'disabled' : ''} title="Move Down">↓</button>
        <button class="btn btn-ghost btn-sm" onclick="removeMergeFile(${index})" title="Remove" style="color:var(--danger)">×</button>
      </div>
    `;
    list.appendChild(item);
  });
}

function moveMergeFile(index, delta) {
  const newIndex = index + delta;
  if (newIndex < 0 || newIndex >= selectedMergeFiles.length) return;
  const temp = selectedMergeFiles[index];
  selectedMergeFiles[index] = selectedMergeFiles[newIndex];
  selectedMergeFiles[newIndex] = temp;
  renderMergeQueue();
}

function removeMergeFile(index) {
  selectedMergeFiles.splice(index, 1);
  renderMergeQueue();
}

async function mergePdfsUI() {
  const el = document.getElementById("pdf-tools-result");
  if (!selectedMergeFiles.length) { toast("कम से कम एक PDF फ़ाइल चुनें", "error"); return; }

  el.style.display = "block"; el.className = "result-box";
  el.innerHTML = `<span class="spinner"></span> PDF मर्ज हो रहे हैं...`;

  const fd = new FormData();
  // IMPORTANT: We use the ordered array, NOT the file input directly
  for (const file of selectedMergeFiles) {
    fd.append("files", file);
  }

  try {
    const r = await fetch("/api/documents/merge-pdf", { method: "POST", body: fd });
    if (!r.ok) {
      if (r.status === 413) throw new Error("Total file size exceeds the 500MB limit.");
      const text = await r.text();
      throw new Error(`Merge failed (${r.status}).`);
    }
    const res = await r.json();
    if (res.success) {
      el.className = "result-box success";
      el.innerHTML = `✅ ${esc(res.message)}`;
      if (res.output_dir) {
        el.innerHTML += `<div style="margin-top:10px">
          <button class="btn btn-primary btn-sm" onclick="openFolder('${res.output_dir.replace(/\\/g, '\\\\')}')">📂 Open Output Folder</button>
        </div>`;
      }
      toast("PDF मर्ज सफल!", "success");
    } else {
      el.className = "result-box error";
      el.innerHTML = `❌ ${esc(res.error || "Merge failed")}`;
      toast("PDF मर्ज विफल", "error");
    }
  } catch (e) {
    el.className = "result-box error";
    el.innerHTML = `❌ API Error: ${esc(String(e))}`;
  }
}

async function compressPdfUI() {
  const fileInput = document.getElementById("compress-pdf-file");
  const mode = v("compress-mode");
  const el = document.getElementById("pdf-tools-result");

  if (!fileInput.files.length) { toast("एक PDF फ़ाइल चुनें", "error"); return; }

  el.style.display = "block"; el.className = "result-box";
  el.innerHTML = `<span class="spinner"></span> PDF कंप्रेस हो रहा है...`;

  const fd = new FormData();
  const file = fileInput.files[0];
  fd.append("file", file);
  fd.append("mode", mode);

  try {
    const r = await fetch("/api/documents/compress-pdf", { method: "POST", body: fd });
    if (!r.ok) {
      if (r.status === 413) throw new Error("File is too large to upload (exceeds 500MB).");
      throw new Error(`Compression failed (${r.status}).`);
    }
    const res = await r.json();
    if (res.success) {
      if (res.needs_split) {
        el.className = "result-box warning";
        el.innerHTML = `
          <div style="display:flex; flex-direction:column; gap:12px;">
            <span>⚠️ <strong>फाइल अभी भी 20MB से बड़ी है:</strong> ${esc(res.message)}</span>
            <div style="display:flex; align-items:center; gap:10px; background:rgba(255,193,7,0.1); padding:10px; border-radius:4px; border:1px dashed var(--warning);">
              <label style="font-size:12px; margin-bottom:0; font-weight:bold;">Pages per part:</label>
              <input type="number" id="manual-split-pages" class="form-control" style="width:80px; height:30px; font-size:12px;" placeholder="Auto" min="1" />
              <span style="font-size:11px; color:var(--text-muted)">(खाली छोड़ें 'Auto' के लिए)</span>
            </div>
            <div style="display:flex; gap:10px;">
              <button class="btn btn-primary btn-sm" onclick="executeSplit('${res.temp_path.replace(/\\/g, '\\\\')}', '${esc(file.name)}')">✂️ Split Now</button>
              <button class="btn btn-ghost btn-sm" onclick="this.parentElement.parentElement.parentElement.innerHTML='✅ Compressed file kept on Desktop.'">Keep Large File</button>
            </div>
          </div>
        `;
        toast("File still exceeds 20MB", "warning");
      } else {
        el.className = "result-box success";
        el.innerHTML = `✅ ${esc(res.message)}`;
        if (res.output_dir) {
          el.innerHTML += `<div style="margin-top:10px">
             <button class="btn btn-primary btn-sm" onclick="openFolder('${res.output_dir.replace(/\\/g, '\\\\')}')">📂 Open Output Folder</button>
           </div>`;
        }
        toast("PDF कंप्रेस सफल!", "success");
      }
    } else {
      el.className = "result-box error";
      el.innerHTML = `❌ ${esc(res.error || "Compression failed")}`;
      toast("PDF कंप्रेस विफल", "error");
    }
  } catch (e) {
    el.className = "result-box error";
    el.innerHTML = `❌ API Error: ${esc(String(e))}`;
  }
}

async function executeSplit(path, originalName) {
  const el = document.getElementById("pdf-tools-result");
  const pagesInput = document.getElementById("manual-split-pages");
  const pagesPerPart = pagesInput ? pagesInput.value : null;

  el.innerHTML = `<span class="spinner"></span> ✂️ फाइल को विभाजित (Splitting) किया जा रहा है... ${pagesPerPart ? `(${pagesPerPart} pages/part)` : '(Auto-calculating)'}`;

  try {
    const res = await apiFetch("/api/documents/split-pdf", "POST", {
      file_path: path,
      original_name: originalName,
      pages_per_part: pagesPerPart
    });
    if (res.success) {
      el.className = "result-box success";
      el.innerHTML = `✅ <strong>Success!</strong> ${esc(res.message)}<br><br>
                      <strong>Generated Parts:</strong><br>
                      ${res.parts.map(p => `<code>${esc(p)}</code>`).join("<br>")}`;

      if (res.output_dir) {
        el.innerHTML += `<div style="margin-top:15px">
          <button class="btn btn-primary btn-sm" onclick="openFolder('${res.output_dir.replace(/\\/g, '\\\\')}')">📂 Open Output Folder</button>
        </div>`;
      }
      toast("Split successful!", "success");
    } else {
      el.className = "result-box error";
      el.innerHTML = `❌ Split failed: ${esc(res.error)}`;
      toast("Split failed", "error");
    }
  } catch (e) {
    el.className = "result-box error";
    el.innerHTML = `❌ API Error: ${esc(String(e))}`;
  }
}

// ─── MODULE: BID DOWNLOADER ────────────────────────
async function launchBidChrome() {
  const el = document.getElementById("bid-execute-status");
  el.style.display = "block";
  el.className = "result-box";
  el.innerHTML = `<span class="spinner"></span> Launching Chrome in debug mode...`;

  try {
    const res = await apiFetch("/api/bid/launch-chrome", "POST");
    if (res.success) {
      el.className = "result-box success";
      el.innerHTML = `🌐 Chrome launched successfully! Open the GeM bid page, then start the downloader.`;
      toast("Chrome launched!", "success");
    } else {
      el.className = "result-box error";
      el.innerHTML = `❌ ${esc(res.error || "Failed to launch Chrome.")}`;
    }
  } catch (e) {
    el.className = "result-box error";
    el.innerHTML = `❌ Network Error: ${esc(String(e))}`;
  }
}

async function startBidDownload() {
  const docTypesRaw = v("bid-doc-types").trim();
  const downloadAllToggle = document.getElementById("bid-download-all").checked;
  const gemUrl = v("bid-gem-url").trim();
  const siFromRaw = v("bid-si-from").trim();
  const siToRaw = v("bid-si-to").trim();

  const docTypes = docTypesRaw
    ? docTypesRaw.split(",").map(s => s.trim()).filter(Boolean)
    : [];

  const downloadAll = downloadAllToggle || docTypes.some(t => t.toLowerCase() === "all");

  if (!downloadAll && docTypes.length === 0) {
    toast("Enter document types or choose Download All.", "error");
    return;
  }

  const progressContainer = document.getElementById("bid-progress-container");
  const progressText = document.getElementById("bid-progress-text");
  const progressPct = document.getElementById("bid-progress-pct");
  const progressBar = document.getElementById("bid-progress-bar");
  const liveLog = document.getElementById("bid-live-log");
  const statsEl = document.getElementById("bid-stats-summary");
  const el = document.getElementById("bid-execute-status");
  const stopBtn = document.getElementById("bid-stop-btn");
  const startBtn = document.getElementById("bid-start-btn");

  el.style.display = "none";
  progressContainer.style.display = "block";
  progressText.innerHTML = "Submitting Download Job...";
  progressBar.style.width = "0%";
  progressPct.innerHTML = "0%";
  liveLog.innerHTML = "";
  statsEl.innerHTML = "";

  const payload = {
    gem_url: gemUrl,
    doc_types: docTypes,
    download_all: downloadAll
  };
  if (siFromRaw) payload.si_from = parseInt(siFromRaw, 10);
  if (siToRaw) payload.si_to = parseInt(siToRaw, 10);

  try {
    const res = await apiFetch("/api/bid/execute", "POST", payload);
    if (!res.success) {
      el.style.display = "block";
      el.className = "result-box error";
      el.innerHTML = `❌ ${esc(res.error || "Execution submission failed.")}`;
      progressContainer.style.display = "none";
      return;
    }

    const jobId = res.job_id;
    currentBidJobId = jobId;

    stopBtn.style.display = "inline-block";
    startBtn.style.display = "none";

    progressText.innerHTML = "Job Queued. Connecting to Chrome...";

    const eventSource = new EventSource(`/api/bid/stream/${jobId}`);

    const cleanup = () => {
      eventSource.close();
      currentBidJobId = null;
      stopBtn.style.display = "none";
      startBtn.style.display = "inline-block";
    };

    const updateStats = (s) => {
      if (!s) return;
      statsEl.innerHTML = `
        <div>Total Firms: <strong>${s.total_firms ?? 0}</strong></div>
        <div>Processed: <strong>${s.processed ?? 0}</strong></div>
        <div>Downloaded: <strong style="color:var(--success)">${s.downloaded ?? 0}</strong></div>
        <div>Skipped: <strong>${s.skipped ?? 0}</strong></div>
        <div>Failed: <strong style="color:var(--danger)">${s.failed ?? 0}</strong></div>
      `;

      const total = s.total_firms || 0;
      const processed = s.processed || 0;
      const pct = total ? Math.round((processed / total) * 100) : 0;
      progressBar.style.width = `${pct}%`;
      progressPct.innerHTML = `${pct}%`;
    };

    eventSource.onmessage = function (e) {
      const data = JSON.parse(e.data);

      if (data.type === "info") {
        liveLog.innerHTML = `<div>&gt; ${esc(data.message)}</div>` + liveLog.innerHTML;
        progressText.innerHTML = esc(data.message);
        updateStats(data.stats);
      }
      else if (data.type === "progress") {
        liveLog.innerHTML = `<div>&gt; ${data.status.toUpperCase()}: ${esc(data.message)}</div>` + liveLog.innerHTML;
        progressText.innerHTML = `Processing: ${esc(data.firm)}`;
        updateStats(data.stats);
      }
      else if (data.type === "error") {
        cleanup();
        el.style.display = "block";
        el.className = "result-box error";
        el.innerHTML = `❌ ${esc(data.message || "Error")}`;
        progressContainer.style.display = "none";
      }
      else if (data.type === "complete") {
        updateStats(data.stats);
        progressBar.style.width = "100%";
        progressPct.innerHTML = "100%";
        progressText.innerHTML = "Download Complete!";

        if (data.output_dir) {
          statsEl.innerHTML += `
            <div style="margin-top:15px; text-align:center;">
              <button class="btn btn-secondary" onclick="openFolder('${esc(data.output_dir)}')">
                📂 Open Output Folder
              </button>
            </div>
          `;
        }

        cleanup();
      }
    };

    eventSource.onerror = function () {
      cleanup();
      el.style.display = "block";
      el.className = "result-box error";
      el.innerHTML = `❌ Connection lost while streaming progress.`;
      progressContainer.style.display = "none";
    };
  } catch (e) {
    el.style.display = "block";
    el.className = "result-box error";
    el.innerHTML = `❌ Network Error: ${esc(String(e))}`;
    progressContainer.style.display = "none";
  }
}

async function stopBidDownload() {
  if (!currentBidJobId) return;
  const btn = document.getElementById("bid-stop-btn");
  btn.disabled = true;
  btn.innerHTML = "Stopping...";

  try {
    const res = await apiFetch("/api/bid/stop", "POST", { job_id: currentBidJobId });
    if (res.success) {
      toast("Stop signal sent...", "info");
    } else {
      toast("Failed to stop: " + res.error, "error");
      btn.disabled = false;
      btn.innerHTML = "🛑 Stop";
    }
  } catch (e) {
    toast("Stop request failed: " + e, "error");
    btn.disabled = false;
    btn.innerHTML = "🛑 Stop";
  }
}

// ─── TENDER SCRUTINY REMOVED ───

// ─── MODULE 9: KNOWLEDGE BASE ──────────────────────
function showKBTab(tabId, el) {
  document.querySelectorAll(".kb-tab-panel").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".kb-tab").forEach(t => t.classList.remove("active"));
  document.getElementById("kbtab-" + tabId).classList.add("active");
  el.classList.add("active");
  if (tabId === "rag-status") loadRAGStatus();
  if (tabId === "docs") loadKBDocs();
}

async function loadRAGStatus() {
  const el = document.getElementById("rag-status-content");
  if (!el) return;

  try {
    const [docs, stats, jobs] = await Promise.all([
      apiFetch("/api/kb/documents"),
      apiFetch("/api/kb/stats"),
      apiFetch("/api/kb/ingest/jobs").catch(() => [])
    ]);

    const activeJobs = jobs.filter(j => j.status === "queued" || j.status === "running");
    let activeJobsHtml = "";
    if (activeJobs.length > 0) {
      activeJobsHtml = `<div style="margin-bottom: 24px;">
        <div class="rag-category-title" style="color:var(--accent)">
          ⏳ Active Ingestion Jobs 
          <span class="badge badge-warning" style="margin-left:8px">${activeJobs.length} running</span>
        </div>
        ${activeJobs.map(job => {
        const cardId = `ragtab-job-${job.job_id}`;
        return `<div id="${cardId}" class="ingest-progress-card">
            <div class="ingest-progress-header">
              <span class="ingest-progress-filename">📄 ${esc(job.filename)}</span>
              <span id="${cardId}-status" class="badge badge-warning">Running</span>
            </div>
            <div class="ingest-progress-bar-wrap">
              <div id="${cardId}-bar" class="ingest-progress-bar" style="width:${job.pct}%"></div>
            </div>
            <div id="${cardId}-label" class="ingest-progress-label">${esc(job.pct_label || "Processing…")}</div>
          </div>`;
      }).join("")}
      </div>`;

      setTimeout(() => {
        activeJobs.forEach(job => pollJobProgress(`ragtab-job-${job.job_id}`, job.job_id));
      }, 100);
    }

    if (!docs.length && !activeJobs.length) {
      el.innerHTML = `<div class="rag-empty-fed">
        <div class="rag-empty-icon">🧠</div>
        <h3>The bot's Knowledge Base is empty</h3>
        <p>Go to <strong>📤 Upload &amp; Feed</strong> and upload your department documents to get started.</p>
      </div>`;
      return;
    }

    const groups = {};
    docs.forEach(d => {
      const cat = d.category || "Other Reference";
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(d);
    });

    const catColors = {
      "Manual / Handbook": "#4CAF50", "Government Circular / OM": "#2196F3",
      "Standard Guidelines / SOP": "#9C27B0", "Draft Noting (Template)": "#FF9800",
      "Previous Noting (Reference)": "#FF5722", "Tender / NIT Document": "#00BCD4",
      "Work Order / Contract": "#8BC34A", "Bill / Payment Document": "#FFC107",
      "Court Judgment / Legal": "#F44336", "Other Reference": "#607D8B"
    };

    let html = activeJobsHtml + `<div style="margin-bottom:16px">
      <strong style="color:var(--text)">${docs.length}</strong> documents &middot;
      <strong style="color:var(--accent)">${stats.total_chunks}</strong> knowledge chunks
    </div>`;

    for (const [cat, catDocs] of Object.entries(groups)) {
      const color = catColors[cat] || "#607D8B";
      html += `<div class="rag-category-section">
        <div class="rag-category-title" style="color:${color}">${esc(cat)} (${catDocs.length})</div>
        <div class="rag-materials-grid">
          ${catDocs.map(d => `<div class="rag-material-card" style="border-left-color:${color}">
            <div class="rag-material-name">📄 ${esc(d.filename)}</div>
          </div>`).join("")}
        </div>
      </div>`;
    }
    el.innerHTML = html;
  } catch (e) { console.error(e); }
}

async function loadKBCategories() {
  const cats = await apiFetch("/api/kb/categories");
  const sel = document.getElementById("kb-category");
  if (sel) sel.innerHTML = cats.map(c => `<option>${esc(c)}</option>`).join("");
}

async function loadKBStats() {
  const s = await apiFetch("/api/kb/stats");
  const el = d => document.getElementById(d);
  if (el("kb-stat-docs")) el("kb-stat-docs").textContent = s.total_documents ?? "0";
  if (el("kb-stat-chunks")) el("kb-stat-chunks").textContent = s.total_chunks ?? "0";
}

async function loadKBDocs() {
  const tbody = document.getElementById("kb-docs-tbody");
  if (!tbody) return;
  loading(tbody);
  const [docs, categories] = await Promise.all([
    apiFetch("/api/kb/documents"),
    apiFetch("/api/kb/categories")
  ]);

  if (!docs.length) { tbody.innerHTML = `<tr><td colspan="5" class="empty-state">No documents ingested yet. Upload PDFs to get started.</td></tr>`; return; }

  tbody.innerHTML = docs.map(d => {
    const opts = categories.map(c =>
      `<option value="${esc(c)}" ${c === d.category ? 'selected' : ''}>${esc(c)}</option>`
    ).join("");

    return `<tr>
      <td><strong>${esc(d.filename)}</strong>${d.description ? `<br><small style="color:var(--text-muted)">${esc(d.description)}</small>` : ""}</td>
      <td>
        <select class="form-control" style="padding:4px 8px; font-size:12px; height:auto" onchange="changeKBCategory('${esc(d.id)}', this)">
          ${opts}
        </select>
      </td>
      <td>${d.chunk_count}</td>
      <td style="font-size:11px;color:var(--text-muted)">${d.ingested_at ? d.ingested_at.slice(0, 16) : ""}</td>
      <td><button class="btn btn-danger btn-sm" onclick="deleteKBDoc('${esc(d.id)}','${esc(d.filename)}')">🗑</button></td>
    </tr>`;
  }).join("");
}

async function uploadToKB() {
  const fileInput = document.getElementById("kb-files");
  const category = v("kb-category");
  const desc = v("kb-desc");
  const container = document.getElementById("kb-upload-result");

  if (!fileInput.files.length) { toast("कम से कम एक फ़ाइल चुनें", "error"); return; }
  container.style.display = "block";
  container.innerHTML = "";   // clear old results

  // Submit each file individually and show a live progress card
  for (const file of fileInput.files) {
    const cardId = "job-" + Math.random().toString(36).slice(2, 8);

    // Inject a progress card immediately
    container.insertAdjacentHTML("beforeend", `
      <div id="${cardId}" class="ingest-progress-card">
        <div class="ingest-progress-header">
          <span class="ingest-progress-filename">📄 ${esc(file.name)}</span>
          <span id="${cardId}-status" class="badge badge-info">Queuing…</span>
        </div>
        <div class="ingest-progress-bar-wrap">
          <div id="${cardId}-bar" class="ingest-progress-bar" style="width:0%"></div>
        </div>
        <div id="${cardId}-label" class="ingest-progress-label">अपलोड हो रही है…</div>
      </div>`);

    // Upload the file
    const fd = new FormData();
    fd.append("file", file);
    fd.append("category", category);
    fd.append("description", desc);

    let job_id = null;
    try {
      const r = await fetch("/api/kb/ingest", { method: "POST", body: fd });
      const res = await r.json();
      job_id = res.job_id;
      setCard(cardId, 5, "Queued", "info", "सर्वर पर प्राप्त हुई…");
    } catch (e) {
      setCard(cardId, 0, "Error", "error", String(e));
      continue;
    }

    if (job_id) pollJobProgress(cardId, job_id);
  }
}

// Update a progress card
function setCard(id, pct, statusText, badgeCls, label) {
  const bar = document.getElementById(id + "-bar");
  const lbl = document.getElementById(id + "-label");
  const stat = document.getElementById(id + "-status");
  if (bar) bar.style.width = pct + "%";
  if (bar) bar.className = "ingest-progress-bar" + (pct >= 100 ? " done" : pct === 0 && statusText === "Error" ? " error" : "");
  if (lbl) lbl.textContent = label;
  if (stat) {
    stat.innerHTML = `<span class="badge badge-${badgeCls}">${statusText}</span>`;
  }
}

// Poll job status every 1.5 seconds and update progress card
function pollJobProgress(cardId, job_id) {
  const interval = setInterval(async () => {
    try {
      const job = await apiFetch(`/api/kb/ingest/status/${job_id}`);
      const pct = job.pct ?? 0;
      const lbl = job.pct_label ?? "";

      if (job.status === "queued") setCard(cardId, pct, "Queued", "info", lbl || "प्रतीक्षा में…");
      if (job.status === "running") setCard(cardId, pct, "Running", "warning", lbl);
      if (job.status === "done") {
        clearInterval(interval);
        const res = job.result || {};
        if (res.skipped) {
          setCard(cardId, 100, "Skipped", "muted", "⚡ पहले से Knowledge Base में मौजूद है");
        } else {
          setCard(cardId, 100, "Done ✅", "success",
            `✅ ${res.chunk_count ?? ""} chunks — Knowledge Base में जोड़ा गया`);
          loadKBStats(); loadRAGStatus();
          toast(`${res.filename || "File"} ingested!`, "success");
        }
      }
      if (job.status === "error") {
        clearInterval(interval);
        const err = job.result?.error ?? "Unknown error";
        setCard(cardId, 0, "Error ❌", "danger", `❌ ${err}`);
        toast("Ingest failed: " + err, "error");
      }
    } catch (e) { /* ignore transient poll errors */ }
  }, 1500);
}


async function deleteKBDoc(docId, filename) {
  if (!confirm(`Remove "${filename}" from Knowledge Base? This cannot be undone.`)) return;
  const res = await fetch(`/api/kb/documents/${encodeURIComponent(docId)}`, { method: "DELETE" });
  const r = await res.json();
  if (r.success) { toast("Document removed from KB", "info"); loadKBDocs(); loadKBStats(); }
  else toast("Delete failed", "error");
}

async function changeKBCategory(docId, selectEl) {
  const newCat = selectEl.value;
  selectEl.disabled = true;
  toast("Updating category...", "info");

  const res = await apiFetch(`/api/kb/documents/${encodeURIComponent(docId)}`, "PUT", { category: newCat });
  selectEl.disabled = false;

  if (res.success) {
    toast("Category updated successfully", "success");
    loadKBStats(); // Refresh the categories bubble count at the top
  } else {
    toast("Failed to update category", "error");
    loadKBDocs();  // Reset dropdown on error
  }
}

async function searchKB() {
  const q = v("kb-search-q");
  const el = document.getElementById("kb-search-results");
  if (!q) { toast("Enter a search query", "error"); return; }
  el.innerHTML = `<span class="spinner"></span> Searching...`;
  const results = await apiFetch("/api/kb/search", "POST", { query: q, n: 6 });
  if (!results.length) { el.innerHTML = `<div class="empty-state">No matching passages found.</div>`; return; }
  el.innerHTML = results.map(r => `
    <div class="kb-result-item">
      <div class="kb-result-meta">
        <span class="badge badge-info">${esc(r.category)}</span>
        <span style="color:var(--text-muted);font-size:11px">${esc(r.filename)} · Chunk ${esc(r.chunk)} · Relevance: <strong>${r.relevance}%</strong></span>
      </div>
      <div class="kb-result-text">${esc(r.text)}</div>
    </div>
  `).join("");
}

// ─── LLM SETTINGS ──────────────────────────────────
async function loadLLMStatus() {
  const grid = document.getElementById("llm-status-grid");
  if (!grid) return;
  grid.innerHTML = `<span class="spinner"></span> Checking...`;
  const s = await apiFetch("/api/llm/status");

  const geminiOk = s.gemini_key_set;
  const activeClr = geminiOk ? "var(--success)" : "var(--danger)";

  grid.innerHTML = `
    <div class="llm-status-item" style="border-color:${activeClr}">
      <div class="llm-status-label">🔋 Active Backend</div>
      <div class="llm-status-val" style="color:${activeClr};font-size:18px;font-weight:700">${esc(s.active_backend)}</div>
    </div>
    <div class="llm-status-item" style="border-color:${geminiOk ? 'var(--success)' : 'var(--border)'}">
      <div class="llm-status-label">✨ Gemini Cloud</div>
      <div class="llm-status-val">${geminiOk ? '✅ API Key Set' : '⚠️ No API Key'}</div>
    </div>
  `;
  // pre-fill config form
  const se = id => document.getElementById(id);
  // LLM provider and other fields
  if (se("llm-provider")) se("llm-provider").value = s.provider || "gemma3_27b";
  if (se("llm-gemini-model")) se("llm-gemini-model").value = (s.llm_config && s.llm_config.gemini_model) || "";
  if (se("llm-temp")) se("llm-temp").value = (s.llm_config && s.llm_config.temperature) || "";
  if (se("llm-context")) se("llm-context").value = (s.llm_config && s.llm_config.context_length) || "";
  if (se("llm-noting-master-prompt")) se("llm-noting-master-prompt").value = (s.llm_config && s.llm_config.noting_master_prompt) || "";
  if (se("llm-qa-system-prompt")) se("llm-qa-system-prompt").value = (s.llm_config && s.llm_config.qa_system_prompt) || "";
  if (se("llm-gemini-key") && s.gemini_key_set) se("llm-gemini-key").placeholder = "•••••••••••••••• (Key Set)";

  // Proxy settings pre-fill
  const nw = s.network || {};
  if (se("network-proxy-mode")) {
    se("network-proxy-mode").value = nw.proxy_mode || "off";
    toggleProxyFields();
  }
  if (se("network-proxy-server")) se("network-proxy-server").value = nw.proxy_server || "http://10.6.0.9";
  if (se("network-proxy-port")) se("network-proxy-port").value = nw.proxy_port || "3128";
  if (se("network-proxy-user")) se("network-proxy-user").value = nw.proxy_username || "";
}

function toggleProxyFields() {
  const mode = v("network-proxy-mode");
  const fields = document.querySelectorAll(".manual-proxy-field");
  fields.forEach(f => f.style.display = (mode === "manual") ? "block" : "none");
}

async function saveNetworkConfig() {
  const payload = {
    proxy_mode: v("network-proxy-mode"),
    proxy_server: v("network-proxy-server"),
    proxy_port: v("network-proxy-port"),
    proxy_username: v("network-proxy-user"),
    proxy_password: v("network-proxy-pass")
  };

  const res = await apiFetch("/api/network/config", "POST", payload);
  const el = document.getElementById("network-config-status");
  el.style.display = "block";
  if (res.success) {
    el.className = "result-box success";
    el.innerHTML = `✅ Network settings saved. Mode: <strong>${esc(payload.proxy_mode)}</strong>`;
    toast("Network settings saved!", "success");
    // refresh form values in case the backend normalised anything
    loadLLMStatus();
  } else {
    el.className = "result-box error";
    el.innerHTML = `❌ Error: ${esc(res.error)}`;
    toast("Failed to save network settings", "error");
  }
}

async function saveLLMConfig() {
  let ctxInput = v("llm-context") || v("llm-ctx") || "8192";

  const payload = {
    provider: v("llm-provider"),
    gemini_model: v("llm-gemini-model"),
    temperature: parseFloat(v("llm-temp")),
    context_length: parseInt(ctxInput),
    noting_master_prompt: v("llm-noting-master-prompt"),
    qa_system_prompt: v("llm-qa-system-prompt"),
    enable_widget: document.getElementById("llm-enable-widget") ? document.getElementById("llm-enable-widget").checked : false
  };

  const geminiKey = v("llm-gemini-key").trim();
  if (geminiKey) {
    payload.gemini_api_key = geminiKey;
  }

  const res = await apiFetch("/api/llm/config", "POST", payload);
  const el = document.getElementById("llm-config-status");
  el.style.display = "block";
  if (res.success) {
    el.className = "result-box success";
    el.innerHTML = `✅ LLM config saved. Provider: <strong>${esc(payload.provider)}</strong>, Model: <strong>${esc(payload.gemini_model)}</strong>`;
    toast("LLM settings saved!", "success");
    loadLLMStatus();
  } else {
    el.className = "result-box error";
    el.innerHTML = `❌ Error: ${esc(res.error || "Failed to save settings")}`;
    toast("Failed to save LLM settings", "error");
  }
}

async function savePromptConfig(kind) {
  const payload = {};
  const el = document.getElementById("llm-prompt-config-status");
  let label = "Prompt";

  if (kind === "noting") {
    payload.noting_master_prompt = v("llm-noting-master-prompt");
    label = "Noting prompt";
  } else if (kind === "qa") {
    payload.qa_system_prompt = v("llm-qa-system-prompt");
    label = "Q&A prompt";
  } else {
    return;
  }

  const res = await apiFetch("/api/llm/config", "POST", payload);
  el.style.display = "block";
  if (res.success) {
    el.className = "result-box success";
    el.innerHTML = `✅ ${esc(label)} saved.`;
    toast(`${label} saved!`, "success");
    loadLLMStatus();
  } else {
    el.className = "result-box error";
    el.innerHTML = `❌ Error: ${esc(res.error || "Failed to save prompt")}`;
    toast(`Failed to save ${label.toLowerCase()}`, "error");
  }
}

async function testLLM() {
  const prompt = v("llm-test-prompt") || "Say hello and tell me your model name in one sentence.";
  const el = document.getElementById("llm-test-result");
  el.style.display = "block"; el.className = "result-box";
  el.innerHTML = `<span class="spinner"></span> Querying AI...`;
  const res = await apiFetch("/api/llm/test", "POST", { prompt });
  if (res.success) {
    el.className = "result-box success";
    el.innerHTML = `<strong>Backend: ${esc(res.backend)}</strong><br><br>${esc(res.response)}`;
  } else {
    el.className = "result-box error";
    el.innerHTML = `❌ ${esc(res.error)}`;
  }
}

// ─── UTILITIES ────────────────────────────────────
function v(id) {
  const el = document.getElementById(id);
  if (!el) return "";
  if (el.isContentEditable) {
    return el.innerHTML || ""; // return HTML for editing elements
  }
  return el.value || "";
}
function initializeRichEditorToolbars(root = document) {
  root.querySelectorAll("[data-editor-toolbar]").forEach(toolbar => {
    if (toolbar.dataset.initialized === "true") return;
    toolbar.innerHTML = buildRichEditorToolbarHtml();
    toolbar.dataset.initialized = "true";
    toolbar.addEventListener("mousedown", (e) => {
      if (e.target.closest("button")) e.preventDefault();
    });
    toolbar.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-editor-action]");
      if (!btn) return;
      executeRichEditorAction(toolbar.dataset.editorTarget, btn.dataset.editorAction, btn.dataset.command || "");
    });
    toolbar.addEventListener("change", (e) => {
      const select = e.target.closest("[data-editor-select]");
      if (!select || !select.value) return;
      executeRichEditorAction(toolbar.dataset.editorTarget, select.dataset.editorSelect, select.value);
      select.value = "";
    });
  });
}
function buildRichEditorToolbarHtml() {
  return `
    <div class="rich-tool-group">
      <button type="button" class="rich-tool-btn" data-editor-action="command" data-command="undo" title="Undo">↶</button>
      <button type="button" class="rich-tool-btn" data-editor-action="command" data-command="redo" title="Redo">↷</button>
      <button type="button" class="rich-tool-btn" data-editor-action="command" data-command="removeFormat" title="Clear formatting">Tx</button>
    </div>
    <div class="rich-tool-group">
      <button type="button" class="rich-tool-btn" data-editor-action="command" data-command="bold" title="Bold"><strong>B</strong></button>
      <button type="button" class="rich-tool-btn" data-editor-action="command" data-command="italic" title="Italic"><em>I</em></button>
      <button type="button" class="rich-tool-btn" data-editor-action="command" data-command="underline" title="Underline"><u>U</u></button>
    </div>
    <div class="rich-tool-group">
      <button type="button" class="rich-tool-btn" data-editor-action="command" data-command="insertUnorderedList" title="Bullet list">• List</button>
      <button type="button" class="rich-tool-btn" data-editor-action="command" data-command="insertOrderedList" title="Numbered list">1. List</button>
    </div>
    <div class="rich-tool-group">
      <button type="button" class="rich-tool-btn" data-editor-action="command" data-command="justifyLeft" title="Align left">L</button>
      <button type="button" class="rich-tool-btn" data-editor-action="command" data-command="justifyCenter" title="Align center">C</button>
      <button type="button" class="rich-tool-btn" data-editor-action="command" data-command="justifyRight" title="Align right">R</button>
    </div>
    <div class="rich-tool-group">
      <select class="rich-tool-select" data-editor-select="formatBlock" title="Block format">
        <option value="">Paragraph</option>
        <option value="P">Paragraph</option>
        <option value="H3">Heading</option>
        <option value="BLOCKQUOTE">Quote</option>
      </select>
    </div>
    <div class="rich-tool-group">
      <button type="button" class="rich-tool-btn table-action" data-editor-action="tableInsert" title="Insert table">Table</button>
      <button type="button" class="rich-tool-btn table-action" data-editor-action="rowAdd" title="Add row">Row+</button>
      <button type="button" class="rich-tool-btn table-action" data-editor-action="colAdd" title="Add column">Col+</button>
      <button type="button" class="rich-tool-btn table-action" data-editor-action="rowDelete" title="Delete row">Row-</button>
      <button type="button" class="rich-tool-btn table-action" data-editor-action="colDelete" title="Delete column">Col-</button>
    </div>
  `;
}
function bindRichTextEditors(root = document) {
  root.querySelectorAll("[data-rich-editor='true']").forEach(editor => {
    if (editor.dataset.richBound === "true") return;
    editor.dataset.richBound = "true";
    ["focus", "mouseup", "keyup", "input"].forEach(eventName => {
      editor.addEventListener(eventName, () => {
        activeRichEditorId = editor.id;
        rememberRichEditorSelection(editor);
        autoGrowNotingTextarea(editor);
      });
    });
    editor.addEventListener("paste", () => {
      activeRichEditorId = editor.id;
      setTimeout(() => {
        rememberRichEditorSelection(editor);
        autoGrowNotingTextarea(editor);
      }, 0);
    });
  });
}
function executeRichEditorAction(editorId, action, value = "") {
  switch (action) {
    case "command":
      return runRichEditorCommand(editorId, value);
    case "formatBlock":
      return runRichEditorCommand(editorId, "formatBlock", value);
    case "tableInsert":
      return promptInsertTable(editorId);
    case "rowAdd":
      return addTableRow(editorId);
    case "colAdd":
      return addTableColumn(editorId);
    case "rowDelete":
      return deleteTableRow(editorId);
    case "colDelete":
      return deleteTableColumn(editorId);
    default:
      return;
  }
}
function getTargetRichEditor(editorId = "") {
  const effectiveId = editorId || activeRichEditorId;
  if (!effectiveId) return null;
  const editor = document.getElementById(effectiveId);
  return editor && editor.isContentEditable ? editor : null;
}
function rememberRichEditorSelection(editor) {
  if (!editor) return;
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount) return;
  const range = sel.getRangeAt(0);
  if (!editor.contains(range.commonAncestorContainer)) return;
  richEditorSelections[editor.id] = range.cloneRange();
}
function restoreRichEditorSelection(editor) {
  const savedRange = richEditorSelections[editor.id];
  if (!savedRange) return false;
  const sel = window.getSelection();
  if (!sel) return false;
  sel.removeAllRanges();
  sel.addRange(savedRange);
  return true;
}
function focusRichEditor(editor) {
  if (!editor) return false;
  activeRichEditorId = editor.id;
  editor.focus();
  if (!restoreRichEditorSelection(editor)) {
    placeCaretAtEnd(editor);
  }
  return true;
}
function placeCaretAtEnd(el) {
  if (!el) return;
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  rememberRichEditorSelection(el);
}
function runRichEditorCommand(editorId, command, value = null) {
  const editor = getTargetRichEditor(editorId);
  if (!editor) return toast("Open a noting editor first", "warning");
  focusRichEditor(editor);
  document.execCommand("styleWithCSS", false, false);
  document.execCommand(command, false, value);
  rememberRichEditorSelection(editor);
  autoGrowNotingTextarea(editor);
}
function promptInsertTable(editorId) {
  const rows = Math.min(12, Math.max(1, parseInt(window.prompt("Number of rows", "2"), 10) || 0));
  if (!rows) return;
  const cols = Math.min(8, Math.max(1, parseInt(window.prompt("Number of columns", "2"), 10) || 0));
  if (!cols) return;
  const editor = getTargetRichEditor(editorId);
  if (!editor) return toast("Open a noting editor first", "warning");
  insertHtmlAtSelection(editor, buildTableHtml(rows, cols));
}
function buildTableHtml(rows, cols) {
  const bodyRows = Array.from({ length: rows }, () => {
    const cells = Array.from({ length: cols }, () => `<td>&nbsp;</td>`).join("");
    return `<tr>${cells}</tr>`;
  }).join("");
  return `<table><tbody>${bodyRows}</tbody></table><p><br></p>`;
}
function insertHtmlAtSelection(editor, html) {
  focusRichEditor(editor);
  if (document.queryCommandSupported && document.queryCommandSupported("insertHTML")) {
    document.execCommand("insertHTML", false, html);
  } else {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    range.deleteContents();
    const tmp = document.createElement("div");
    tmp.innerHTML = html;
    const frag = document.createDocumentFragment();
    while (tmp.firstChild) frag.appendChild(tmp.firstChild);
    range.insertNode(frag);
  }
  rememberRichEditorSelection(editor);
  autoGrowNotingTextarea(editor);
}
function getTableContext(editorId) {
  const editor = getTargetRichEditor(editorId);
  if (!editor) {
    toast("Open a noting editor first", "warning");
    return null;
  }
  focusRichEditor(editor);
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount) {
    toast("Place the cursor inside a table cell first", "warning");
    return null;
  }
  const anchor = sel.anchorNode;
  const cell = closestWithinEditor(anchor, "td,th", editor);
  if (!cell) {
    toast("Place the cursor inside a table cell first", "warning");
    return null;
  }
  const row = closestWithinEditor(cell, "tr", editor);
  const table = closestWithinEditor(cell, "table", editor);
  const cellIndex = Array.from(row.children).indexOf(cell);
  return { editor, table, row, cell, cellIndex };
}
function closestWithinEditor(node, selector, editor) {
  let current = node && node.nodeType === 1 ? node : node?.parentElement;
  while (current && current !== editor) {
    if (current.matches && current.matches(selector)) return current;
    current = current.parentElement;
  }
  return null;
}
function placeCaretInside(el) {
  if (!el) return;
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(true);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  const editor = closestWithinEditor(el, "[data-rich-editor='true']", document.body) || el.closest?.("[data-rich-editor='true']");
  if (editor) rememberRichEditorSelection(editor);
}
function addTableRow(editorId) {
  const ctx = getTableContext(editorId);
  if (!ctx) return;
  const newRow = ctx.row.cloneNode(true);
  Array.from(newRow.children).forEach(cell => { cell.innerHTML = "&nbsp;"; });
  ctx.row.insertAdjacentElement("afterend", newRow);
  placeCaretInside(newRow.children[0]);
  autoGrowNotingTextarea(ctx.editor);
}
function addTableColumn(editorId) {
  const ctx = getTableContext(editorId);
  if (!ctx) return;
  let targetCell = null;
  Array.from(ctx.table.rows).forEach(row => {
    const refCell = row.children[Math.min(ctx.cellIndex, row.children.length - 1)];
    const newCell = document.createElement(refCell?.tagName?.toLowerCase() === "th" ? "th" : "td");
    newCell.innerHTML = "&nbsp;";
    if (refCell) {
      refCell.insertAdjacentElement("afterend", newCell);
    } else {
      row.appendChild(newCell);
    }
    if (!targetCell) targetCell = newCell;
  });
  placeCaretInside(targetCell);
  autoGrowNotingTextarea(ctx.editor);
}
function deleteTableRow(editorId) {
  const ctx = getTableContext(editorId);
  if (!ctx) return;
  if (ctx.table.rows.length <= 1) return toast("The last table row cannot be deleted", "warning");
  const nextRow = ctx.row.nextElementSibling || ctx.row.previousElementSibling;
  ctx.row.remove();
  placeCaretInside(nextRow?.children?.[0] || ctx.table);
  autoGrowNotingTextarea(ctx.editor);
}
function deleteTableColumn(editorId) {
  const ctx = getTableContext(editorId);
  if (!ctx) return;
  const firstRowCells = ctx.table.rows[0]?.children?.length || 0;
  if (firstRowCells <= 1) return toast("The last table column cannot be deleted", "warning");
  Array.from(ctx.table.rows).forEach(row => {
    if (row.children[ctx.cellIndex]) row.children[ctx.cellIndex].remove();
  });
  const targetRow = ctx.table.rows[0];
  const targetCell = targetRow?.children?.[Math.max(0, ctx.cellIndex - 1)] || targetRow?.children?.[0];
  placeCaretInside(targetCell || ctx.table);
  autoGrowNotingTextarea(ctx.editor);
}
function looksLikeHtml(text) {
  return /<\s*\/?\s*[a-z][^>]*>/i.test(String(text || ""));
}
function textToEditorHtml(text) {
  return esc(String(text || "")).replace(/\r?\n/g, "<br>");
}
function normalizeRichEditorHtml(html) {
  return String(html || "")
    .replace(/\r?\n/g, "")
    .replace(/<div><br><\/div>/gi, "<br>")
    .replace(/<div>/gi, "")
    .replace(/<\/div>/gi, "<br>")
    .replace(/(<br\s*\/?>\s*){3,}/gi, "<br><br>")
    .trim();
}
function richHtmlToText(html) {
  const tmp = document.createElement("div");
  tmp.innerHTML = normalizeRichEditorHtml(html);
  return (tmp.innerText || tmp.textContent || "").replace(/\u00A0/g, " ").trim();
}
function setRichEditorContent(el, content, isHtml = false) {
  if (!el) return;
  if (el.isContentEditable) {
    el.innerHTML = isHtml ? normalizeRichEditorHtml(content) : textToEditorHtml(content);
    return;
  }
  el.value = isHtml ? richHtmlToText(content) : String(content || "");
}
function getRichEditorHtml(el) {
  if (!el) return "";
  if (el.isContentEditable) {
    return normalizeRichEditorHtml(el.innerHTML || "");
  }
  return textToEditorHtml(el.value || "");
}
function getRichEditorText(el) {
  if (!el) return "";
  if (el.isContentEditable) {
    return richHtmlToText(el.innerHTML || "").replace(/\n{3,}/g, "\n\n");
  }
  return String(el.value || "").trim();
}
function copyRichEditorToClipboard(editor) {
  let html = getRichEditorHtml(editor);
  if (!html.trim()) {
    toast("No text to copy", "error");
    return Promise.resolve(false);
  }
  html = html.replace(/(<br\s*\/?>\s*){2,}/g, "<br>");
  let plain = getRichEditorText(editor);
  plain = plain.replace(/\n{2,}/g, "\n");
  const styled = `<div style="font-family:'Tahoma'; font-size:12pt;">${html}</div>`;

  if (navigator.clipboard && navigator.clipboard.write) {
    const blobInput = new Blob([styled], { type: "text/html" });
    const blobText = new Blob([plain], { type: "text/plain" });
    const data = [new ClipboardItem({ "text/html": blobInput, "text/plain": blobText })];
    return navigator.clipboard.write(data).then(() => {
      toast("Text copied to clipboard!", "success");
      return true;
    }).catch(() => {
      return navigator.clipboard.writeText(plain).then(() => {
        toast("Copied as plain text", "warning");
        return true;
      });
    });
  }

  return new Promise((resolve) => {
    const tmp = document.createElement("div");
    tmp.style.position = "absolute";
    tmp.style.left = "-9999px";
    tmp.style.fontFamily = "'Tahoma'";
    tmp.style.fontSize = "12pt";
    tmp.innerHTML = html;
    document.body.appendChild(tmp);
    const range = document.createRange();
    range.selectNodeContents(tmp);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand("copy");
    sel.removeAllRanges();
    document.body.removeChild(tmp);
    toast("Text copied to clipboard!", "success");
    resolve(true);
  });
}
function esc(s) { return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
function fmt(n) { return n ? Number(n).toLocaleString("en-IN") : "0"; }
function formatDate(d) { if (!d) return "—"; try { return new Date(d).toLocaleDateString("en-IN"); } catch { return d; } }
function copyToClipboard(t) { navigator.clipboard.writeText(t); toast("Copied!", "success"); }
function openFile(p) { fetch(`/api/documents/serve?path=${encodeURIComponent(p)}`); }

// ─── MODULE 10: TEC EVALUATION ────────────────────────────────────────────────
let tecSession = { file_id: null, extension: null };

async function extractTecData() {
  const fileInput = document.getElementById("tec-file");
  const el = document.getElementById("tec-status");
  const tableSec = document.getElementById("tec-table-section");
  const mappingSec = document.getElementById("tec-mapping-section");

  if (!fileInput.files.length) { toast("Please select a PDF or DOCX file.", "error"); return; }

  el.style.display = "block"; el.className = "result-box";
  el.innerHTML = `<span class="spinner"></span> Extracting and analyzing tables...`;
  tableSec.style.display = "none";
  mappingSec.style.display = "none";

  const fd = new FormData();
  fd.append("file", fileInput.files[0]);

  try {
    const r = await fetch("/api/tec/analyze", { method: "POST", body: fd });
    const res = await r.json();

    if (res.success) {
      el.style.display = "none";
      tecSession = { file_id: res.file_id, extension: res.extension };

      renderMappingTable(res.parameters);
      mappingSec.style.display = "block";
      toast("Parameters detected! Please define your criteria.", "success");
    } else {
      el.className = "result-box error";
      el.innerHTML = `❌ ${esc(res.error || "Failed to analyze document.")}`;
      toast("Error: " + (res.error || "Failed"), "error");
    }
  } catch (e) {
    el.className = "result-box error";
    el.innerHTML = `❌ Network Error: ${esc(String(e))}`;
  }
}

function renderMappingTable(parameters) {
  const tbody = document.getElementById("tec-mapping-tbody");
  tbody.innerHTML = parameters.map(p => `
    <tr>
      <td style="font-weight:600">${esc(p.parameter)}</td>
      <td>
        ${p.values.map(v => `<span class="badge" style="margin:2px; display:inline-block">${esc(v)}</span>`).join("")}
      </td>
      <td>
        <input type="text" class="form-control mapping-qualify" data-param="${esc(p.parameter)}" placeholder="e.g. Yes, Y, Compliant, Submitted, Eligible, Exempted" />
      </td>
      <td>
        <input type="text" class="form-control mapping-disqualify" data-param="${esc(p.parameter)}" placeholder="e.g. No, N, Not Eligible, No Valid Document Submitted, Certificate not submitted by OEM, Not-Compliant" />
      </td>
    </tr>
  `).join("");
}

async function generateFinalTecResults() {
  const mappingSec = document.getElementById("tec-mapping-section");
  const el = document.getElementById("tec-status");
  const tableSec = document.getElementById("tec-table-section");
  const tbody = document.getElementById("tec-tbody");

  const criteria = {};
  document.querySelectorAll("#tec-mapping-tbody tr").forEach(row => {
    const pInput = row.querySelector(".mapping-qualify");
    if (!pInput) return;
    const param = pInput.dataset.param;
    const qualify = row.querySelector(".mapping-qualify").value.split(",").map(v => v.trim()).filter(v => v);
    const disqualify = row.querySelector(".mapping-disqualify").value.split(",").map(v => v.trim()).filter(v => v);
    if (qualify.length || disqualify.length) criteria[param] = { qualify, disqualify };
  });

  el.style.display = "block"; el.className = "result-box";
  el.innerHTML = `<span class="spinner"></span> Generating final evaluations...`;
  mappingSec.style.display = "none";

  try {
    const res = await apiFetch("/api/tec/extract", "POST", {
      file_id: tecSession.file_id,
      extension: tecSession.extension,
      criteria: criteria
    });

    if (res.success) {
      el.style.display = "none";
      tableSec.style.display = "block";
      const s = res.stats;
      const statsEl = document.getElementById("tec-stats-summary");
      if (statsEl) {
        statsEl.innerHTML = `
          <div>Total: ${s.total_detected}</div>
          <div>Qualified: ${s.total_qualified}</div>
          <div>Disqualified: ${s.total_disqualified}</div>
          <div>IP-similarity rejections: ${s.ip_rejected}</div>
        `;
      }
      tbody.innerHTML = res.results.map((item, idx) => `
        <tr data-idx="${idx}" data-firm="${esc(item.firm_name)}">
          <td>${idx + 1}</td>
          <td><textarea class="form-control tec-firm">${esc(item.firm_name)}</textarea></td>
          <td>
            <select class="form-control tec-status">
              <option value="true" ${item.is_qualified ? "selected" : ""}>Qualified</option>
              <option value="false" ${!item.is_qualified ? "selected" : ""}>Not Qualified</option>
            </select>
          </td>
          <td><textarea class="form-control tec-comment">${esc(item.comment)}</textarea></td>
        </tr>`).join("");
      toast("Results generated!", "success");
    } else {
      el.className = "result-box error";
      el.innerHTML = `❌ ${esc(res.error)}`;
    }
  } catch (e) { el.innerHTML = `❌ Error: ${e.message}`; }
}

async function launchChrome() {
  const el = document.getElementById("tec-execute-status");
  el.style.display = "block";
  el.className = "result-box";
  el.innerHTML = `<span class="spinner"></span> Launching Chrome in debug mode...`;

  try {
    const res = await apiFetch("/api/tec/launch-chrome", "POST");
    if (res.success) {
      el.className = "result-box success";
      el.innerHTML = `🌐 Chrome launched successfully! Log into the GeM Technical Evaluation page, then return here to Execute.`;
      toast("Chrome launched!", "success");
    } else {
      el.className = "result-box error";
      el.innerHTML = `❌ ${esc(res.error || "Failed to launch Chrome.")}`;
    }
  } catch (e) {
    el.className = "result-box error";
    el.innerHTML = `❌ Network Error: ${esc(String(e))}`;
  }
}

async function executeTecBot() {
  const tbody = document.getElementById("tec-tbody");
  const rows = tbody.querySelectorAll("tr");
  const el = document.getElementById("tec-execute-status");
  const stopBtn = document.getElementById("tec-stop-btn");
  const executeBtn = document.getElementById("tec-execute-btn");
  const gemUrlInput = document.getElementById("gem-url")?.value.trim() || "";

  if (rows.length === 0) { toast("No firms.", "error"); return; }

  const results = [];
  rows.forEach(row => {
    results.push({
      firm_name: row.querySelector(".tec-firm").value,
      is_qualified: row.querySelector(".tec-status").value === "true",
      comment: row.querySelector(".tec-comment").value
    });
  });

  const progressContainer = document.getElementById("tec-progress-container");
  const progressText = document.getElementById("tec-progress-text");
  const progressBar = document.getElementById("tec-progress-bar");

  el.style.display = "none";
  progressContainer.style.display = "block";
  progressText.innerHTML = "Submitting Job...";

  try {
    const res = await apiFetch("/api/tec/execute", "POST", { results, gem_url: gemUrlInput });
    if (!res.success) {
      el.style.display = "block";
      el.innerHTML = `❌ ${res.error}`;
      progressContainer.style.display = "none";
      return;
    }
    const jobId = res.job_id;
    currentTecJobId = jobId;
    stopBtn.style.display = "inline-block";
    executeBtn.style.display = "none";

    const eventSource = new EventSource(`/api/tec/stream/${jobId}`);
    eventSource.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.type === "info") progressText.innerHTML = esc(data.message);
      if (data.type === "complete") {
        eventSource.close();
        currentTecJobId = null;
        stopBtn.style.display = "none";
        executeBtn.style.display = "inline-block";
        toast("Complete!", "success");
      }
    };
  } catch (e) { toast("Error: " + e, "error"); }
}

async function stopTecExecution() {
  if (!currentTecJobId) return;
  const btn = document.getElementById("tec-stop-btn");
  btn.disabled = true;
  btn.innerHTML = "Stopping...";

  try {
    const res = await apiFetch("/api/tec/stop", "POST", { job_id: currentTecJobId });
    if (res.success) {
      toast("Stop signal sent...", "info");
    } else {
      toast("Failed to stop: " + res.error, "error");
      btn.disabled = false;
      btn.innerHTML = "🛑 Stop Execution";
    }
  } catch (e) {
    toast("Stop request failed: " + e, "error");
    btn.disabled = false;
    btn.innerHTML = "🛑 Stop Execution";
  }
}

// ─── KNOW HOW (Q&A) ──────────────────────────────────────────────────────────
async function askKnowHow() {
  const q = v("knowhow-q").trim();
  const display = document.getElementById("knowhow-qa-display");
  const btn = document.getElementById("knowhow-ask-btn");

  if (!q) { toast("कृपया अपना प्रश्न दर्ज करें", "error"); return; }

  btn.disabled = true;
  const aiMsgId = "a-" + Date.now();

  // Append user question
  if (display.querySelector(".empty-state")) display.innerHTML = "";
  display.innerHTML += `
    <div class="qa-block user-msg" style="margin-bottom:20px; text-align:right">
      <div style="display:inline-block; background:var(--accent); color:white; padding:10px 15px; border-radius:15px 15px 0 15px; max-width:80%; box-shadow:0 2px 5px rgba(0,0,0,0.1)">
        <strong>आप:</strong> ${esc(q)}
      </div>
    </div>
    <div id="${aiMsgId}" class="qa-block ai-msg" style="margin-bottom:20px;">
       <div style="background:var(--bg-hover); padding:15px; border-radius:15px 15px 15px 0; border:1px solid var(--border); max-width:90%">
         <span class="spinner"></span> AI उत्तर तैयार कर रहा है...
         <div style="font-size:11px; margin-top:5px; color:var(--text-muted)">RAG + Web Search Fallback Active</div>
       </div>
    </div>`;

  display.scrollTop = display.scrollHeight;
  document.getElementById("knowhow-q").value = "";

  try {
    const res = await apiFetch("/api/kb/qa", "POST", { question: q });
    const aiEl = document.getElementById(aiMsgId);
    if (res.success) {
      let sourceHtml = "";
      if (res.sources && res.sources.length) {
        sourceHtml = `<div style="margin-top:12px; font-size:11px; color:var(--text-muted); padding-top:8px; border-top:1px solid var(--border)">
          <strong>स्रोतः</strong> ${res.sources.join(", ")}
        </div>`;
      }

      const safeAnswer = res.answer.replace(/"/g, '&quot;').replace(/'/g, "&apos;").replace(/\\/g, '\\\\').replace(/\n/g, '\\n');
      const safeQuestion = q.replace(/"/g, '&quot;').replace(/'/g, "&apos;").replace(/\\/g, '\\\\').replace(/\n/g, '\\n');

      aiEl.innerHTML = `
        <div style="background:var(--bg-hover); padding:15px; border-radius:15px 15px 15px 0; border:1px solid var(--border); max-width:90%; position:relative;">
          <div style="margin-bottom:8px; color:var(--accent); font-weight:600; display:flex; justify-content:space-between; align-items:center;">
            <span>🤖 AI सहायक:</span>
            <button class="btn btn-ghost btn-sm" onclick="translateKnowHow('${aiMsgId}')" style="padding:2px 8px; font-size:11px">A🌐 Translate to Hindi</button>
          </div>
          <div id="${aiMsgId}-content" style="white-space:pre-wrap; line-height:1.6">${res.answer}</div>
          ${sourceHtml}
          
          <div style="margin-top:10px; background:var(--bg-dark); padding:10px; border-radius:6px; font-size:12px;">
             <strong>Feedback / Comment:</strong>
             <div style="display:flex; gap:8px; margin-top:4px;">
               <input type="text" id="${aiMsgId}-fb" class="form-control" autocomplete="off" placeholder="Suggest improvements for next time..." style="flex:1; padding:4px 8px; font-size:12px" />
               <button class="btn btn-primary" style="padding:4px 8px; font-size:12px" onclick="submitKnowHowFeedback('${aiMsgId}', '${safeQuestion}')">Send</button>
             </div>
          </div>
        </div>`;
    } else {
      aiEl.innerHTML = `<div class="result-box error">❌ ${esc(res.error || "त्रुटि")}</div>`;
    }
    loadKnowHowHistory();
  } catch (e) {
    document.getElementById(aiMsgId).innerHTML = `<div class="result-box error">❌ Error: ${esc(String(e))}</div>`;
  } finally {
    btn.disabled = false;
    display.scrollTop = display.scrollHeight;
  }
}

async function translateKnowHow(aiMsgId) {
  const contentEl = document.getElementById(aiMsgId + "-content");
  if (!contentEl) return;
  const originalText = contentEl.innerText;

  contentEl.innerHTML = `<span class="spinner"></span> अनुवाद हो रहा है...`;

  try {
    const res = await apiFetch("/api/kb/qa/translate", "POST", { text: originalText });
    if (res.success) {
      contentEl.innerHTML = res.hindi + `<div style="margin-top:10px; font-size:11px; color:var(--text-muted); border-top:1px dashed var(--border); padding-top:6px"><i>Original English:</i><br>${esc(originalText)}</div>`;
    } else {
      contentEl.innerHTML = `<span style="color:var(--danger)">Translation failed: ${esc(res.error)}</span><br><br>` + esc(originalText);
    }
  } catch (e) {
    contentEl.innerHTML = `<span style="color:var(--danger)">Translation Error</span><br><br>` + esc(originalText);
  }
}

async function submitKnowHowFeedback(aiMsgId, questionText) {
  const fbInput = document.getElementById(aiMsgId + "-fb");
  const contentEl = document.getElementById(aiMsgId + "-content");
  if (!fbInput || !contentEl) return;
  const fbText = fbInput.value.trim();
  if (!fbText) return;
  const answerText = contentEl.innerText;
  fbInput.disabled = true;
  fbInput.value = "Sending...";

  try {
    const res = await apiFetch("/api/kb/qa/feedback", "POST", {
      question: questionText,
      answer: answerText,
      feedback: fbText
    });
    if (res.success) {
      fbInput.value = "Thanks!";
      toast("Feedback submitted!", "success");
    } else {
      fbInput.disabled = false;
      fbInput.value = fbText;
    }
  } catch (e) {
    fbInput.disabled = false;
    fbInput.value = fbText;
  }
}

async function loadKnowHowHistory() {
  const el = document.getElementById("knowhow-history");
  if (!el) return;
  const history = await apiFetch("/api/know-how/history");

  if (!history || !history.length) {
    el.innerHTML = `<div class="empty-state" style="padding:20px">No recent questions</div>`;
    return;
  }

  el.innerHTML = history.map(h => {
    // Escape single quotes for use in inline JS
    const safeAnswer = (h.answer || "").replace(/'/g, "\\'").replace(/\n/g, "\\n");
    return `
      <div class="history-item" style="padding:10px; border-bottom:1px solid var(--border); cursor:pointer; position:relative" onclick="loadHistoryToQA('${esc(h.question)}', '${safeAnswer}')">
        <div style="font-size:13px; font-weight:500; margin-bottom:4px; max-width:85%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap">${esc(h.question)}</div>
        <div style="font-size:11px; color:var(--text-muted)">${h.created_at}</div>
        <button class="btn btn-ghost btn-sm" style="position:absolute; right:5px; top:5px; padding:2px 5px" onclick="event.stopPropagation(); deleteKnowHowHistory(${h.id})">🗑</button>
      </div>`;
  }).join("");
}

function loadHistoryToQA(q, a) {
  const display = document.getElementById("knowhow-qa-display");
  if (display.querySelector(".empty-state")) display.innerHTML = "";
  display.innerHTML += `
    <div class="qa-block user-msg" style="margin-bottom:20px; text-align:right">
      <div style="display:inline-block; background:var(--accent); color:white; padding:10px 15px; border-radius:15px 15px 0 15px; max-width:80%">
        <strong>आप:</strong> ${esc(q)}
      </div>
    </div>
    <div class="qa-block ai-msg" style="margin-bottom:20px;">
       <div style="background:var(--bg-hover); padding:15px; border-radius:15px 15px 15px 0; border:1px solid var(--border); max-width:90%">
         <div style="margin-bottom:8px; color:var(--accent); font-weight:600">🤖 AI सहायक (इतिहास से):</div>
         <div style="white-space:pre-wrap; line-height:1.6">${a}</div>
       </div>
    </div>`;
  display.scrollTop = display.scrollHeight;
}

async function deleteKnowHowHistory(id) {
  if (!confirm("क्या आप इस प्रश्न को इतिहास से हटाना चाहते हैं?")) return;
  const res = await apiFetch(`/api/know-how/history/${id}`, "DELETE");
  if (res.success) {
    toast("इतिहास से हटाया गया", "success");
    loadKnowHowHistory();
  }
}

async function openFolder(path) {
  if (!path) return;
  toast("फोल्डर ओपन हो रहा है...", "info");
  const res = await apiFetch("/api/utils/open-folder", "POST", { path });
  if (!res.success) toast("फोल्डर नहीं खुल सका: " + res.error, "error");
}
