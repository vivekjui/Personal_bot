/* ══════════════════════════════════════════════════
   Noting Bot Dashboard — app.js
   Complete frontend logic for all 8 modules
   ══════════════════════════════════════════════════ */

"use strict";

const API = "";  // Same origin

// ─── GLOBAL STATE ─────────────────────────────────
let allCases = [];
let currentTecJobId = null; // TEC Execution Job State
let currentTecAnalyzeJobId = null;
let currentTecExtractJobId = null;
let currentBidJobId = null; // Bid Downloader Job State
let currentBidV2JobId = null; // Bid Downloader V2 Job State
let currentBidV2EventSource = null;
let selectedMergeFiles = []; // PDF Merge Sequencer State


// ─── INIT ──────────────────────────────────────────
// --- HELPER FUNCTIONS ---
function esc(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
function v(id) {
  const el = document.getElementById(id);
  return el ? el.value : "";
}

function htmlToPlainText(content) {
  if (!content) return "";
  if (!/<[a-z][\s\S]*>/i.test(content)) return String(content);
  const div = document.createElement("div");
  div.innerHTML = content;
  return (div.innerText || div.textContent || "").trim();
}

function plainTextToHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML.replace(/\n/g, "<br>");
}

function normalizeEditorHtml(content) {
  const raw = String(content || "");
  if (!raw.trim()) return "";
  return /<[a-z][\s\S]*>/i.test(raw) ? raw : plainTextToHtml(raw);
}

function getQuillForContainer(containerId) {
  if (containerId === "noting-editor-container") return window.notingQuill || null;
  if (containerId === "noting-template-editor") return window.notingTemplateQuill || null;
  if (containerId === "email-editor-container") return window.emailQuill || null;
  if (containerId === "email-template-editor") return window.emailTemplateQuill || null;
  if (containerId === "pro-editor-container") return window.quill || null;
  return null;
}

function getEditorHtml(containerId) {
  const quill = getQuillForContainer(containerId);
  if (quill) return quill.root.innerHTML;

  const el = document.getElementById(containerId);
  if (!el) return "";
  if (el.tagName === "TEXTAREA") return plainTextToHtml(el.value);
  return el.innerHTML || "";
}

function getEditorText(containerId) {
  const quill = getQuillForContainer(containerId);
  if (quill) return quill.getText().trim();

  const el = document.getElementById(containerId);
  if (!el) return "";
  if (el.tagName === "TEXTAREA") return (el.value || "").trim();
  return (el.innerText || el.textContent || "").trim();
}

function setEditorContent(containerId, content) {
  const quill = getQuillForContainer(containerId);
  const normalized = normalizeEditorHtml(content);

  if (quill) {
    quill.setContents([]); // Clear first
    if (normalized) {
      quill.clipboard.dangerouslyPasteHTML(normalized);
    }
    return;
  }

  const el = document.getElementById(containerId);
  if (!el) return;
  if (el.tagName === "TEXTAREA") {
    el.value = htmlToPlainText(content);
    autoGrowNotingTextarea(el);
    return;
  }
  // If it's a div, we assume it can render HTML
  el.innerHTML = normalized;
}

let disableTableBetter = false;

function isTableBetterAvailable() {
  return !disableTableBetter && typeof QuillTableBetter !== "undefined";
}

function addTableBetterSupport(quillModules, tableOptions, addToolbarButton) {
  if (!isTableBetterAvailable()) return;

  quillModules.keyboard = {
    bindings: QuillTableBetter.keyboardBindings || {}
  };
  quillModules["table-better"] = tableOptions;
  addToolbarButton();
}

function buildSafeQuillModules(quillModules) {
  const safeModules = { ...quillModules };
  delete safeModules.keyboard;
  delete safeModules.table;
  delete safeModules["table-better"];

  safeModules.toolbar = (quillModules.toolbar || []).map(group => {
    if (!Array.isArray(group)) return group;
    return group.filter(item => item !== "table-better");
  });

  return safeModules;
}

function createQuillWithFallback(selector, quillModules) {
  try {
    return new Quill(selector, {
      theme: "snow",
      modules: quillModules
    });
  } catch (error) {
    if (!quillModules["table-better"]) throw error;

    console.warn("quill-table-better init failed; retrying without table support", error);
    disableTableBetter = true;

    return new Quill(selector, {
      theme: "snow",
      modules: buildSafeQuillModules(quillModules)
    });
  }
}

function toggleSidePanel(btn) {
  const panel = btn.closest('.split-layout').querySelector('.side-panel');
  if (panel) {
    const isCollapsed = panel.classList.toggle('collapsed');
    btn.innerHTML = isCollapsed ? '📋' : '☰';
    btn.title = isCollapsed ? 'Show Sidebar' : 'Hide Sidebar';
  }
}

document.addEventListener("DOMContentLoaded", () => {
  // Register Table Module
  if (isTableBetterAvailable()) {
    try {
      Quill.register({ 'modules/table-better': QuillTableBetter }, true);
      if (typeof QuillTableBetter.register === "function") {
        QuillTableBetter.register();
      }
    } catch (error) {
      console.warn("quill-table-better registration failed; disabling table support", error);
      disableTableBetter = true;
    }
  }

  startHealthCheck();
  loadDashboard();
  loadNotingTypes();
  fetchStages(); // Load procurement stages on startup to avoid empty state
  loadLLMStatus(); // Load LLM keys and prompts on startup
  
  // Multiple Noting/Email Editors (Support for old and new containers)
  ['noting-editor-container', 'noting-template-editor', 'noting-editor', 'email-template-editor'].forEach(id => {
    if (document.getElementById(id)) {
      const quillModules = {
        toolbar: [
          [{ 'header': [1, 2, 3, false] }],
          ['bold', 'italic', 'underline', 'strike'],
          [{ 'color': [] }, { 'background': [] }],
          ['clean'] // will add 'table-better' below if available
        ]
      };

      addTableBetterSupport(quillModules, {
          language: 'en_US',
          toolbarTable: true,
          menus: ['column', 'row', 'merge', 'unmerge', 'deleteTable'],
        }, () => {
        quillModules.toolbar[3].unshift('table-better');
      });

      const q = createQuillWithFallback('#' + id, quillModules);

      // Robust Table Pasting Support
      ['TABLE', 'TBODY', 'TR', 'TD', 'TH'].forEach(tag => {
        q.clipboard.addMatcher(tag, (node, delta) => delta);
      });

      if (id === 'noting-editor-container') window.notingQuill = q;
      else if (id === 'noting-template-editor') window.notingTemplateQuill = q;
      else if (id === 'email-template-editor') window.emailTemplateQuill = q;
      else if (id === 'noting-editor') window.notingMainQuill = q;
    }
  });

  // Email Editor (Integrated into page)
  if (document.getElementById("email-editor-container")) {
    const quillEmailModules = {
      toolbar: [
        [{ 'header': [1, 2, 3, false] }],
        ['bold', 'italic', 'underline', 'strike'],
        [{ 'color': [] }, { 'background': [] }],
        ['clean']
      ]
    };

    addTableBetterSupport(quillEmailModules, {
        language: 'en_US',
        toolbarTable: true,
        menus: ['column', 'row', 'insert', 'merge', 'unmerge', 'deleteTable', 'style'],
      }, () => {
      // insert 'table-better' into the toolbar
      quillEmailModules.toolbar.push(['table-better']);
    });

    window.emailQuill = createQuillWithFallback('#email-editor-container', quillEmailModules);
  }

  // Initialize Quill Editor
  if (document.getElementById("pro-editor-container")) {
    const quillProModules = {
      toolbar: [
        [{ 'header': [1, 2, 3, false] }],
        ['bold', 'italic', 'underline', 'strike'],
        [{ 'color': [] }, { 'background': [] }],
        [{ 'list': 'ordered'}, { 'list': 'bullet' }],
        [{ 'align': [] }],
        ['clean']
      ]
    };

    addTableBetterSupport(quillProModules, {
        language: 'en_US',
        toolbarTable: true,
        menus: ['column', 'row', 'insert', 'merge', 'unmerge', 'deleteTable', 'style'],
      }, () => {
      // insert 'table-better' before 'clean' which is the last item
      quillProModules.toolbar.splice(quillProModules.toolbar.length - 1, 0, ['table-better']);
    });

    window.quill = createQuillWithFallback('#pro-editor-container', quillProModules);

    // Support tables in Pro Editor clipboard and pasting
    // We rely on quill-table-better to handle standard HTML tables.
    // Explicitly allowing TABLE tags through without modification allows the module to intercept them.
    window.quill.clipboard.addMatcher('TABLE', (node, delta) => delta);
    window.quill.clipboard.addMatcher('TBODY', (node, delta) => delta);
    window.quill.clipboard.addMatcher('TR', (node, delta) => delta);
    window.quill.clipboard.addMatcher('TD', (node, delta) => delta);
    window.quill.clipboard.addMatcher('TH', (node, delta) => delta);

    // Auto-save logic: pushes content back to the original draft/container
    window.quill.on('text-change', () => {
      if (!window.currentEditorTargetId) return;
      setEditorContent(window.currentEditorTargetId, window.quill.root.innerHTML);
    });
  }

  // Initialize Extract Text Editor
  if (document.getElementById("extract-quill-editor")) {
    const quillExtractModules = {
      toolbar: [
        [{ 'header': [1, 2, 3, false] }],
        ['bold', 'italic', 'underline', 'strike'],
        [{ 'color': [] }, { 'background': [] }],
        [{ 'list': 'ordered'}, { 'list': 'bullet' }],
        [{ 'align': [] }],
        ['link'],
        ['clean']
      ]
    };

    addTableBetterSupport(quillExtractModules, {
        language: 'en_US',
        toolbarTable: true,
        menus: ['column', 'row', 'insert', 'merge', 'unmerge', 'deleteTable', 'style'],
      }, () => {
      quillExtractModules.toolbar.splice(quillExtractModules.toolbar.length - 1, 0, ['table-better']);
    });

    window.extractQuill = createQuillWithFallback('#extract-quill-editor', quillExtractModules);

    // Allow raw HTML tables through clipboard
    window.extractQuill.clipboard.addMatcher('TABLE', (node, delta) => delta);
  }

  // Initialize TEC Minutes Editor
  if (document.getElementById("tec-minutes-editor")) {
    const quillTecModules = {
      toolbar: [
        [{ 'header': [1, 2, 3, false] }],
        ['bold', 'italic', 'underline', 'strike'],
        [{ 'color': [] }, { 'background': [] }],
        [{ 'list': 'ordered'}, { 'list': 'bullet' }],
        [{ 'align': [] }],
        ['clean']
      ]
    };

    addTableBetterSupport(quillTecModules, {
        language: 'en_US',
        toolbarTable: true,
        menus: ['column', 'row', 'insert', 'merge', 'unmerge', 'deleteTable', 'style'],
      }, () => {
      quillTecModules.toolbar.push(['table-better']);
    });

    window.tecMinutesQuill = createQuillWithFallback('#tec-minutes-editor', quillTecModules);
    window.tecMinutesQuill.clipboard.addMatcher('TABLE', (node, delta) => delta);
  }

  // Global Clipboard Listener for Images (for Extract Page)
  document.addEventListener('paste', handleGlobalPaste);

  const lastPage = sessionStorage.getItem("activePage");
  if (lastPage && lastPage !== "dashboard") {
    showPage(lastPage);
  }
  initializeUIStability();
});





// ─── PAGE NAVIGATION ──────────────────────────────
function showPage(pageId, el) {
  sessionStorage.setItem("activePage", pageId);
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
  
  const pageTarget = document.getElementById("page-" + pageId);
  if (pageTarget) {
    pageTarget.classList.add("active");
  } else {
    console.error(`Page target "page-${pageId}" not found in DOM.`);
    return;
  }

  // Highlight navigation item in dropdown if applicable
  const dropItem = document.querySelector(`.dropdown-item[onclick*="'${pageId}'"]`);
  if (dropItem) {
    document.querySelectorAll(".dropdown-item").forEach(d => d.classList.remove("active"));
    dropItem.classList.add("active");
  }

  // Back to Dashboard button visibility
  const backBtn = document.getElementById("back-to-dashboard");
  const navDropdown = document.getElementById("nav-dropdown-container");
  if (backBtn) {
    backBtn.style.display = (pageId === "dashboard") ? "none" : "inline-block";
  }
  if (navDropdown) {
    navDropdown.style.display = "block";
  }

  const titles = {
    dashboard: "Dashboard", cases: "Case Registry", noting: "e-Office Noting",
    "pdf-tools": "PDF & ZIP Tool", bid: "Bid Downloader", tender: "Bid Scrutiny",
    kb: "🧠 Knowledge Base", ai: "⚙️ AI Settings", tec: "TEC Evaluation",
    knowhow: "📖 Know How (Q&A)", extract: "🔍 Extract and Summarize"
  };
  const titleEl = document.getElementById("pageTitle");
  if (titleEl) {
    titleEl.textContent = titles[pageId] || pageId;
  }

  // Lazy-load page data
  if (pageId === "kb") { loadKBStats(); loadKBDocs(); loadKBCategories(); }
  if (pageId === "knowhow") { loadKnowHowHistory(); }
  if (pageId === "noting") switchNotingTab("library");
  if (pageId === "email") fetchEmailLibrary();
  if (pageId === "ai") { loadLLMStatus(); }
  if (pageId === "extract") { /* init if needed */ }
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

function refreshApp() {
  // Hard restart: Clear all state and start from scratch
  const theme = localStorage.getItem('theme');
  
  // Clear everything
  sessionStorage.clear();
  localStorage.clear();
  
  // Restore only the theme to prevent blinding the user if they like dark mode
  if (theme) localStorage.setItem('theme', theme);
  
  toast("Hard Restarting Application...", "info");
  
  setTimeout(() => {
    // Redirect to root without any params, forcing a clean load
    window.location.href = window.location.pathname + "?v=" + Date.now();
  }, 800);
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

function switchDocTab(tabId, el) {
  document.querySelectorAll(".doc-tab-content").forEach(c => c.style.display = "none");
  const target = document.getElementById("doc-tab-" + tabId);
  if (target) {
    target.style.display = "block";
    // If it's the extraction tab, ensure Quill is resized/visible correctly
    if (tabId === 'extraction' && window.extractQuill) {
        setTimeout(() => window.extractQuill.update(), 10);
    }
  }
  
  // Update the pill UI
  const docPage = document.getElementById('page-documents');
  if (docPage) {
    const pills = docPage.querySelectorAll(".tab-pill");
    pills.forEach(p => p.classList.remove("active"));
    if (el) {
      el.classList.add("active");
    } else {
      pills.forEach(p => {
        if (p.getAttribute('onclick').includes(`'${tabId}'`)) p.classList.add('active');
      });
    }
  }
}

function loading(el) { el.innerHTML = `<div style="padding:20px;text-align:center"><span class="spinner"></span></div>`; }

// ─── CASES REMOVED ───

function startHealthCheck() {
  setInterval(async () => {
    try {
      const res = await fetch("/api/admin/status");
      if (!res.ok) throw new Error("Offline");
      const statusIndicator = document.getElementById("system-status-indicator");
      if (statusIndicator) {
        statusIndicator.style.background = "#2ecc71";
        statusIndicator.title = "System Online";
      }
    } catch (e) {
      const statusIndicator = document.getElementById("system-status-indicator");
      if (statusIndicator) {
        statusIndicator.style.background = "#e74c3c";
        statusIndicator.title = "System Unresponsive / Offline";
      }
    }
  }, 5000);
}

// ─── DASHBOARD SUMMARY ─────────────────────────────
async function loadDashboard() {
  try {
    const data = await apiFetch("/api/dashboard/summary");
    const statCases = document.getElementById("stat-cases");
    if (statCases) {
      statCases.textContent = data.active_cases ?? "—";
    }
  } catch (e) {
    console.error("Dashboard summary fail:", e);
  }
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
const NOTING_LIBRARY_PAGE_SIZE = 10;
let notingLibraryObserver = null;
let notingLibraryState = {
  stage: "ALL",
  query: "",
  offset: 0,
  total: 0,
  hasMore: true,
  loading: false
};

function switchNotingTab(tab) {
  document.getElementById("noting-tab-draft").style.display = tab === "draft" ? "block" : "none";
  document.getElementById("noting-tab-library").style.display = tab === "library" ? "block" : "none";
  if (tab === "library") fetchStandardLibrary(window.currentLibraryStage || "ALL");
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
    console.error("Failed to fetch stages:", e);
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

function getLibrarySearchQuery() {
  return (document.getElementById("library-search-input")?.value || "").trim();
}

function updateLibraryStageButtons() {
  const stageListEl = document.getElementById("library-stage-list");
  if (!stageListEl) return;

  stageListEl.innerHTML = `<button class="btn btn-ghost btn-sm stage-filter-btn" id="stage-btn-ALL" onclick="renderLibraryStage('ALL')">📦 All Notings</button>` +
    OFFICIAL_STAGES.map(s => `
      <button class="btn btn-ghost btn-sm stage-filter-btn"
              id="stage-btn-${s.replace(/\s+/g, '-')}"
              onclick="renderLibraryStage('${esc(s)}')">
        📁 ${esc(s)}
      </button>
    `).join("");
}

function highlightLibraryStageButton(stage) {
  document.querySelectorAll(".stage-filter-btn").forEach(btn => btn.classList.remove("active"));
  const activeBtnId = (stage === "ALL" || stage === "📦 All Notings") ? "stage-btn-ALL" : `stage-btn-${stage.replace(/\s+/g, '-')}`;
  const activeBtn = document.getElementById(activeBtnId);
  if (activeBtn) activeBtn.classList.add("active");
}

function syncAddNotingStageOptions(selectedStage = "ALL") {
  const addStageSel = document.getElementById("add-noting-stage");
  if (!addStageSel) return;

  const effectiveStage = selectedStage && selectedStage !== "ALL" ? selectedStage : "";
  addStageSel.innerHTML = OFFICIAL_STAGES.map(s => `
    <option value="${esc(s)}" ${s === effectiveStage ? "selected" : ""}>${esc(s)}</option>
  `).join("");
}

function buildNotingLibraryCard(item) {
  const stageOptions = OFFICIAL_STAGES.map(s => `<option value="${esc(s)}" ${s === item.stage ? 'selected' : ''}>${esc(s)}</option>`).join("");
  const isCustomBadge = item.is_custom ? `<span class="badge badge-success" style="font-size:8px; margin-left:5px">AI REFINED</span>` : '';

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
           <span style="font-size:9px; color:var(--text-muted); opacity:0.7">${formatDate(item.updated_at)}</span>
        </div>
        <div style="display:flex; gap:10px; align-items:center">
           <span id="library-save-status-${item.id}" style="color:var(--success); display:none; font-size:9px">✓ Saved</span>
           <select class="form-control btn-xs" style="width:auto; height:24px; padding:0 5px; font-size:10px" onchange="moveNoting(${item.id}, this.value)">
              <option disabled selected>Move to stage...</option>
              ${stageOptions}
           </select>
           <button class="btn btn-danger btn-xs" style="height:24px; padding:0 8px; font-size:10px" onclick="deleteNoting(${item.id})">🗑</button>
        </div>
      </div>
      <div class="library-textarea-wrapper" style="position:relative">
         <div class="form-control library-editor"
          contenteditable="true"
          onblur="handleLibraryUpdate(this, ${item.id}, 'text', 'noting')"
          style="font-family:'Tahoma', sans-serif; font-size:11pt; line-height:1.4; background:white; color:black; border:1px solid var(--border); padding:10px; height:auto; min-height:100px; overflow-y:auto; border-radius:8px">${normalizeEditorHtml(item.text || item.content || "")}</div>
         <div style="margin-top:10px; display:flex; gap:8px; align-items:center; border-top:1px solid var(--border); padding-top:10px">
            <input type="text" class="form-control btn-xs" id="refine-context-${item.id}"
                   placeholder="Refinement Context (firm name, GeM contract etc.)"
                   style="flex:1; font-size:11pt; padding:6px 10px; height:34px; background:rgba(255,255,255,0.03)" />
            <button class="btn btn-warning btn-sm" onclick="refineLibraryTemplate(${item.id}, this)" style="height:34px; white-space:nowrap">✨ Refine AI</button>
            <button class="btn btn-primary btn-sm" onclick="openTemplateInEditor(${item.id}, 'noting')" style="height:34px; white-space:nowrap">🖋️ Edit in Pro</button>
            <button class="btn btn-primary btn-sm" onclick="copyTextDirect(this)" style="height:34px; white-space:nowrap">📋 Copy</button>
         </div>
      </div>
    </div>
  `;
}

function removeLibraryPaginationRow() {
  document.querySelectorAll("#library-results-container .library-pagination-row").forEach(el => el.remove());
}

function renderNotingLibraryCards(items, { append = false } = {}) {
  const resultsContainer = document.getElementById("library-results-container");
  if (!resultsContainer) return;

  removeLibraryPaginationRow();

  if (!append && !items.length) {
    const label = notingLibraryState.query || notingLibraryState.stage || "current view";
    resultsContainer.innerHTML = `<div class="result-box info" style="margin:0; text-align:center; padding:40px">No notings found for "${esc(label)}".</div>`;
    return;
  }

  const markup = items.map(buildNotingLibraryCard).join("");
  if (append) {
    resultsContainer.insertAdjacentHTML("beforeend", markup);
  } else {
    resultsContainer.innerHTML = markup;
  }

  setTimeout(() => {
    document.querySelectorAll("#library-results-container .library-editor").forEach(ta => autoGrowNotingTextarea(ta));
  }, 10);
}

function disconnectNotingLibraryObserver() {
  if (notingLibraryObserver) {
    notingLibraryObserver.disconnect();
    notingLibraryObserver = null;
  }
}

function renderNotingLibraryFooter() {
  const resultsContainer = document.getElementById("library-results-container");
  if (!resultsContainer || !standardLibraryData.length) return;

  removeLibraryPaginationRow();

  let content = "";
  if (notingLibraryState.loading) {
    content = `<span class="spinner"></span> Loading next ${NOTING_LIBRARY_PAGE_SIZE} notings...`;
  } else if (notingLibraryState.hasMore) {
    content = `<div id="library-load-more-sentinel">Scroll down to load the next ${NOTING_LIBRARY_PAGE_SIZE} notings...</div>`;
  } else {
    content = `Loaded ${standardLibraryData.length} of ${notingLibraryState.total || standardLibraryData.length} notings.`;
  }

  resultsContainer.insertAdjacentHTML(
    "beforeend",
    `<div class="library-pagination-row result-box info" style="margin:12px 0 0 0; text-align:center; padding:16px;">${content}</div>`
  );
}

function connectNotingLibraryObserver() {
  disconnectNotingLibraryObserver();
  if (!notingLibraryState.hasMore || notingLibraryState.loading) return;

  const sentinel = document.getElementById("library-load-more-sentinel");
  if (!sentinel) return;

  const root = document.querySelector("#page-noting .main-panel-content") || null;
  notingLibraryObserver = new IntersectionObserver((entries) => {
    if (entries.some(entry => entry.isIntersecting)) {
      loadNextStandardLibraryPage();
    }
  }, {
    root,
    rootMargin: "250px 0px"
  });
  notingLibraryObserver.observe(sentinel);
}

async function loadNextStandardLibraryPage() {
  const resultsContainer = document.getElementById("library-results-container");
  if (!resultsContainer || notingLibraryState.loading || !notingLibraryState.hasMore) return;

  notingLibraryState.loading = true;
  renderNotingLibraryFooter();

  try {
    const params = new URLSearchParams({
      paged: "1",
      limit: String(NOTING_LIBRARY_PAGE_SIZE),
      offset: String(notingLibraryState.offset)
    });
    if (notingLibraryState.query) params.set("query", notingLibraryState.query);
    if (notingLibraryState.stage && notingLibraryState.stage !== "ALL") params.set("stage", notingLibraryState.stage);

    const response = await apiFetch(`/api/noting/standard?${params.toString()}`);
    const items = Array.isArray(response.items) ? response.items : [];
    items.forEach(item => {
      if (item.content && !item.text) item.text = item.content;
    });

    const append = notingLibraryState.offset > 0;
    standardLibraryData = append ? standardLibraryData.concat(items) : items;
    notingLibraryState.offset += items.length;
    notingLibraryState.total = Number(response.total || 0);
    notingLibraryState.hasMore = Boolean(response.has_more) && items.length > 0;

    renderNotingLibraryCards(items, { append });
  } catch (e) {
    console.error("Error fetching paged noting library:", e);
    if (!notingLibraryState.offset) {
      resultsContainer.innerHTML = `<div class="result-box error" style="margin:0; text-align:center; padding:40px">Failed to load noting library.</div>`;
    } else {
      toast("Could not load more notings.", "error");
    }
    notingLibraryState.hasMore = false;
  } finally {
    notingLibraryState.loading = false;
    if (standardLibraryData.length) renderNotingLibraryFooter();
    connectNotingLibraryObserver();
  }
}

async function fetchStandardLibrary(initialStage = null) {
  const resultsContainer = document.getElementById("library-results-container");
  if (!resultsContainer) return;

  try {
    await fetchStages();
    updateLibraryStageButtons();
    await renderLibraryStage(initialStage || window.currentLibraryStage || "ALL");
  } catch (e) {
    console.error("Error fetching library:", e);
    resultsContainer.innerHTML = `<div class="result-box error" style="margin:0; text-align:center; padding:40px">Failed to load library data.</div>`;
  }
}

async function renderLibraryStage(stage) {
  const resultsContainer = document.getElementById("library-results-container");
  if (!resultsContainer) return;

  window.currentLibraryStage = stage || "ALL";
  highlightLibraryStageButton(window.currentLibraryStage);
  syncAddNotingStageOptions(window.currentLibraryStage);

  disconnectNotingLibraryObserver();
  standardLibraryData = [];
  notingLibraryState = {
    stage: window.currentLibraryStage,
    query: getLibrarySearchQuery(),
    offset: 0,
    total: 0,
    hasMore: true,
    loading: false
  };

  resultsContainer.innerHTML = `<div class="result-box info" style="margin:0; text-align:center; padding:32px"><span class="spinner"></span> Loading recent notings...</div>`;
  await loadNextStandardLibraryPage();
}

function useTemplate(id, type) {
    const data = type === 'noting' ? standardLibraryData : emailLibraryData;
    const item = data.find(x => x.id === id);
    if (!item) return;

    if (type === 'noting') {
        const suggestionSection = document.getElementById("noting-suggestion-section");
        suggestionSection.style.display = 'block';
        if (window.notingQuill) {
            setEditorContent("noting-editor-container", item.text || item.content || "");
        }
        suggestionSection.scrollIntoView({ behavior: 'smooth' });
    } else {
        const suggestionSection = document.getElementById("email-suggestion-section");
        suggestionSection.style.display = 'block';
        if (window.emailQuill) {
            window.emailQuill.setText('');
            window.emailQuill.clipboard.dangerouslyPasteHTML(item.text.replace(/\n/g, '<br>'));
        }
        suggestionSection.scrollIntoView({ behavior: 'smooth' });
    }
}

async function submitNewNoting() {
  const keyword = v("add-noting-keyword").trim();
  const stage = document.getElementById("add-noting-stage").value;
  const text = v("add-noting-text").trim();

  if (!keyword || !text) return toast("Keyword and Text are required", "error");

  const res = await apiFetch("/api/noting/library/add", "POST", { stage, keyword, text });
  if (res.success) {
    toast("Noting added successfully!", "success");
    closeModal("modal-add-noting");
    // Clear form
    document.getElementById("add-noting-keyword").value = "";
    document.getElementById("add-noting-text").value = "";
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
    await fetchStandardLibrary(window.currentLibraryStage || 'ALL');
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
    await fetchStandardLibrary(window.currentLibraryStage || 'ALL');
  } else {
    toast("Error: " + res.error, "error");
  }
}


function copyTextDirect(btn) {
  const container = btn.closest(".library-textarea-wrapper");
  // Cards use a contenteditable div, not a textarea
  const editor = container.querySelector(".library-editor");
  const text = editor ? (editor.innerText || editor.textContent || "") : "";
  navigator.clipboard.writeText(text.trim()).then(() => {
    const original = btn.innerHTML;
    btn.innerHTML = "✅ Copied";
    setTimeout(() => { btn.innerHTML = original; }, 2000);
  }).catch(() => toast("Could not copy to clipboard", "error"));
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
  if (!card) return toast("Card not found", "error");
  
  const textarea = card.querySelector(".library-editor");
  const contextInput = document.getElementById(`refine-context-${id}`);
  const context = contextInput ? contextInput.value.trim() : "";
  
  // FIX: contenteditable divs use innerText, not value
  const originalText = (textarea.innerText || textarea.textContent || "").trim();
  const originalHtml = textarea.innerHTML;

  if (!originalText) {
    return toast("Please enter some text in the editor first", "warning");
  }

  const originalBtnHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Refining...`;

  try {
    const res = await apiFetch('/api/noting/refine', 'POST', {
      text: originalText,
      html: textarea.innerHTML || "",
      modifications: context,
      target_lang: "hindi"
    });

    if (res.success) {
      const refinedContent = res.refined_html || res.refined_text || "";
      // Instead of updating current, ADD AS NEW entries as per user request
      const addRes = await apiFetch("/api/noting/library/add", "POST", {
        stage: card.querySelector("select").value || "General",
        keyword: card.querySelector(".library-keyword-editor").value + " (Refined)",
        text: refinedContent
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
        let msg = `New Update Available: ${res.latest}\n\n${res.notes || 'No release notes provided.'}\n\n`;
        msg += `Would you like to INSTALL it locally now? (The bot will download and extract the new version over existing files)`;

        if (confirm(msg)) {
          installBotUpdate();
        } else if (confirm("Would you like to open the GitHub repository instead?")) {
          window.open(res.url, "_blank");
        }
      } else {
        toast(`You are on the latest version (${res.current})`, "success");
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

  toast("Starting update download... please wait.", "info");

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
      setEditorContent("noting-template-editor", res.text || "");
      document.getElementById("noting-step-1").style.display = "none";
      document.getElementById("noting-step-2").style.display = "block";
      toast("Direct AI draft generated!", "success");

      // Auto-save to library
      try {
        await apiFetch("/api/noting/library/add", "POST", {
          stage: "AI Drafts",
          keyword: context.substring(0, 30) + " (Auto-saved)",
          text: res.text || ""
        });
      } catch (saveErr) {
        console.warn("Auto-save failed:", saveErr);
      }
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
      resultsContainer.innerHTML = res.notings.map((n, idx) => `
        <div class="noting-result-item" onclick="selectNotingTemplate(${idx})" style="padding:12px; border-bottom:1px solid var(--border); cursor:pointer; transition:background 0.2s">
            <div style="display:flex; justify-content:space-between; margin-bottom:5px">
                <span class="badge badge-info">${esc(n.source || n.category || 'Library')}</span>
                ${n.score ? `<span style="font-size:10px; color:var(--text-muted)">Match: ${n.score}%</span>` : ''}
                ${n.keyword ? `<span style="font-size:10px; font-weight:700; color:var(--accent)">${esc(n.keyword)}</span>` : ''}
            </div>
            <div style="font-size:12px; color:var(--text); white-space:pre-wrap; max-height:80px; overflow:hidden;">
                ${esc(n.text.substring(0, 200))}...
            </div>
            <div style="display:none" id="noting-template-raw-${idx}">${esc(n.text)}</div>
        </div>
      `).join("");

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
      resultsContainer.innerHTML = res.notings.map((n, idx) => `
        <div class="noting-result-item" onclick="selectNotingTemplate(${idx})" style="padding:12px; border-bottom:1px solid var(--border); cursor:pointer; transition:background 0.2s">
            <div style="display:flex; justify-content:space-between; margin-bottom:5px">
                <span class="badge badge-info">${esc(n.category)}</span>
                ${n.score ? `<span style="font-size:10px; color:var(--text-muted)">Match: ${n.score}%</span>` : ''}
            </div>
            <div style="font-size:12px; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                ${esc(n.text.substring(0, 150))}...
            </div>
            <div style="display:none" id="noting-template-raw-${idx}">${esc(n.text)}</div>
        </div>
      `).join("");

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

function selectNotingTemplate(idx) {
  const step1 = document.getElementById("noting-step-1");
  const step2 = document.getElementById("noting-step-2");

  let textToLoad = "";
  if (idx === -1) {
    textToLoad = "";
  } else {
    textToLoad = document.getElementById(`noting-template-raw-${idx}`).textContent;
  }

  setEditorContent("noting-template-editor", textToLoad);

  step1.style.display = "none";
  step2.style.display = "block";

  // Trigger auto-grow
  const editor = window.notingTemplateQuill ? window.notingTemplateQuill.root : document.getElementById("noting-template-editor");
  setTimeout(() => autoGrowNotingTextarea(editor), 10);
  if (editor && typeof editor.focus === "function") editor.focus();
}

function autoResizeTextarea(el) {
  if (!el || el.tagName !== "TEXTAREA") return;
  el.style.height = "auto";
  el.style.height = (el.scrollHeight) + "px";
}

async function refineNotingAI(clickedBtn) {
  console.log("Refining noting action triggered...");
  
  // 1. Determine Source Editor based on the clicked button or modal state
  let sourceId = "noting-template-editor";
  let isPro = false;
  
  if (document.getElementById("modal-pro-editor")?.classList.contains("open")) {
    sourceId = "pro-editor-container";
    isPro = true;
  } else if (clickedBtn && clickedBtn.id === "noting-refine-btn") {
    sourceId = "noting-template-editor";
  } else if (clickedBtn && clickedBtn.id === "noting-bar-refine-btn") {
    sourceId = "noting-editor-container";
  } else {
    // If no specific button ID, try to find context from the closest section
    const section = clickedBtn ? clickedBtn.closest('.result-section, .draft-section') : null;
    if (section && section.id === 'noting-suggestion-section') sourceId = "noting-editor-container";
    else sourceId = "noting-template-editor";
  }

  const templateText = getEditorText(sourceId);
  const templateHtml = getEditorHtml(sourceId);
  
  console.log(`Refining from source: ${sourceId}, Content Length: ${templateText.length}`);
  
  // Get modifications from the appropriate context box
  let extraContext = "";
  if (isPro) {
    extraContext = document.getElementById("pro-refine-context").value.trim();
  } else {
    extraContext = document.getElementById("noting-refine-context").value.trim();
  }

  const targetLang = "hindi";

  if (!templateText) {
    return toast("Please provide some text to refine", "error");
  }

  // Handle status and button state
  const status = isPro ? null : document.getElementById("noting-status");
  const refineBtn = clickedBtn || document.getElementById("noting-refine-btn");
  const originalBtnHtml = refineBtn ? refineBtn.innerHTML : "";

  if (status) {
    status.style.display = "block";
    status.className = "result-box info";
    status.innerHTML = `<span class="spinner"></span> Running Official GSI Refinement & Translation...`;
  }
  
  if (refineBtn) {
    refineBtn.disabled = true;
    refineBtn.classList.add("loading");
    refineBtn.innerHTML = `<span class="spinner"></span> Refining...`;
  }

  // Show a toast as well if we're not in pro mode to ensure visibility
  if (!isPro) {
    toast("AI is refining your document...", "info");
  } else {
    toast("Refining draft via AI...", "info");
  }

  try {
    const res = await apiFetch('/api/noting/refine', 'POST', {
      text: templateText,
      html: templateHtml,
      modifications: extraContext,
      target_lang: targetLang,
      document_type: 'noting'
    });
    
    if (status) status.style.display = "none";
    if (refineBtn) {
      refineBtn.disabled = false;
      refineBtn.classList.remove("loading");
    }

    if (res.success) {
      if (res.refined_text && res.refined_text.startsWith("[AI Error")) {
          toast(res.refined_text, "error");
          if (refineBtn) {
            refineBtn.disabled = false;
            refineBtn.classList.remove("loading");
          }
          return;
      }

      // Update the active editor
      setEditorContent(sourceId, res.refined_html || res.refined_text || "");
      
      // If we are NOT in pro editor, update the result editor
      if (!isPro && sourceId !== "noting-editor-container") {
          setEditorContent("noting-editor-container", res.refined_html || res.refined_text || "");
      }

      if (!isPro) {
        const suggestionSection = document.getElementById("noting-suggestion-section");
        if (suggestionSection) {
          suggestionSection.style.display = "block";
          suggestionSection.scrollIntoView({ behavior: 'smooth' });
        }
      } else {
          // Clear pro-editor instruction box after success
          document.getElementById("pro-refine-context").value = "";
      }
      
      toast("Refined successfully!", "success");
    } else {
      toast(res.error || "Refinement failed", "error");
    }
  } catch (e) {
    console.error("Refine AI Error:", e);
    if (status) {
      status.style.display = "block";
      status.className = "result-box error";
      status.innerHTML = `<span>Error: ${e.message}</span>`;
    }
    toast("Error: " + e.message, "error");
  } finally {
    if (refineBtn) {
      refineBtn.disabled = false;
      refineBtn.classList.remove("loading");
      refineBtn.innerHTML = originalBtnHtml;
    }
  }
}

function saveRefinedToLibrary() {
  const text = getEditorText("noting-editor-container");
  if (!text) return toast("Nothing to save", "error");
  const html = getEditorHtml("noting-editor-container");
  const content = /<table\b/i.test(html || "") ? html : text;

  // Populate the "Add New" modal with the refined text
  document.getElementById("add-noting-text").value = content;
  const context = document.getElementById("noting-library-search")?.value || "";
  document.getElementById("add-noting-keyword").value = "AI Refined - " + (context.substring(0, 30) || "Untilted");

  // Open the modal
  openModal('modal-add-noting');
}

// Noting History functions removed as requested

function resetNoting() {
  document.getElementById("noting-step-1").style.display = "block";
  document.getElementById("noting-step-2").style.display = "none";
  document.getElementById("noting-suggestion-section").style.display = "none";
  document.getElementById("noting-status").style.display = "none";
  document.getElementById("noting-results-list").style.display = "none";
  document.getElementById("noting-context").value = "";
  document.getElementById("noting-refine-context").value = "";
  setEditorContent("noting-template-editor", "");
  setEditorContent("noting-editor-container", "");
}

function copyNotingText() {
  const text = getEditorText("noting-editor-container");
  if (!text) { toast("No text to copy", "error"); return; }
  navigator.clipboard.writeText(text);
  toast("Text copied successfully!", "success");
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
    if (res.job_id) {
       pollZipStatus(res.job_id, el);
    } else {
       handleZipProcessResponse(res, el);
    }
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
    const res = await apiFetch("/api/documents/process-zip-local", "POST", { folder_path: path });
    if (res.job_id) {
       pollZipStatus(res.job_id, el);
    } else {
       handleZipProcessResponse(res, el);
    }
  } catch (e) {
    el.className = "result-box error";
    el.innerHTML = `❌ API Error: ${esc(String(e))}`;
  }
}

async function pollZipStatus(jobId, el) {
  const interval = setInterval(async () => {
    try {
      const res = await apiFetch(`/api/documents/zip-status/${jobId}`);
      if (res.status === "complete" || res.status === "failed") {
        clearInterval(interval);
        handleZipProcessResponse(res, el);
      } else {
        const pct = res.total ? Math.round((res.progress / res.total) * 100) : 0;
        el.innerHTML = `<span class="spinner"></span> Processing... ${pct}% (${res.progress}/${res.total})`;
      }
    } catch (e) {
      clearInterval(interval);
      el.className = "result-box error";
      el.innerHTML = `❌ Polling Error: ${esc(String(e))}`;
    }
  }, 1000);
}

function handleZipProcessResponse(res, el) {
  if (res.success && res.status !== "failed") {
    el.className = "result-box success";
    let html = `✅ <strong>Processing Complete!</strong><br><br>`;
    
    // Check if any sub-tasks failed
    const failures = res.results ? res.results.filter(r => r.error) : [];
    if (failures.length > 0) {
      html = `⚠️ <strong>Processing Completed with some errors</strong><br><br>`;
      el.className = "result-box warning";
    }

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
      
      // Auto-open folder if successful
      if (typeof openFolder === 'function') {
        openFolder(res.output_dir);
      }
    }
    
    if (failures.length > 0) {
      toast(`Processed with ${failures.length} errors`, "warning");
    } else {
      toast("Zip processing successful!", "success");
    }
  } else {
    el.className = "result-box error";
    const errorMsg = res.error || (res.status === "failed" ? "The process encountered a catastrophic error." : "Processing failed");
    el.innerHTML = `❌ ${esc(errorMsg)}`;
    toast("Error: " + errorMsg, "error");
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
        openFolder(res.output_dir);
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

  if (!fileInput.files.length) { toast("एक या अधिक PDF फ़ाइलें चुनें", "error"); return; }

  el.style.display = "block"; el.className = "result-box";
  const files = Array.from(fileInput.files);
  let processedCount = 0;
  let lastOutputDir = null;

  for (const file of files) {
    processedCount++;
    el.innerHTML = `<span class="spinner"></span> 📉 <strong>Processing ${processedCount} of ${files.length}:</strong> ${esc(file.name)}...`;
    
    const fd = new FormData();
    fd.append("file", file);
    fd.append("mode", mode);

    try {
      const r = await fetch("/api/documents/compress-pdf", { method: "POST", body: fd });
      if (!r.ok) throw new Error(`Failed for ${file.name} (${r.status})`);
      
      const res = await r.json();
      if (res.success) {
        lastOutputDir = res.output_dir;
        if (res.needs_split) {
          el.className = "result-box warning";
          el.innerHTML = `
            <div style="display:flex; flex-direction:column; gap:12px;">
              <span>⚠️ <strong>फाइल अभी भी 20MB से बड़ी है:</strong> ${esc(res.message)}</span>
              <p style="font-size:12px">बचा हुआ बैच (Batch) जारी रखने के लिए पहले इसे स्प्लिट करें या 'Skip' करें।</p>
              <div style="display:flex; align-items:center; gap:10px; background:rgba(255,193,7,0.1); padding:10px; border-radius:4px; border:1px dashed var(--warning);">
                <label style="font-size:12px; margin-bottom:0; font-weight:bold;">Pages per part:</label>
                <input type="number" id="manual-split-pages" class="form-control" style="width:80px; height:30px; font-size:12px;" placeholder="Auto" min="1" />
              </div>
              <div style="display:flex; gap:10px;">
                <button class="btn btn-primary btn-sm" onclick="executeSplit('${res.temp_path.replace(/\\/g, '\\\\')}', '${esc(file.name)}')">✂️ Split Now</button>
                <button class="btn btn-ghost btn-sm" onclick="location.reload()">⏭️ Skip & Finish Batch</button>
              </div>
            </div>
          `;
          toast("File exceeds 20MB, batch paused.", "warning");
          return; 
        }
      } else {
        toast(`Error processing ${file.name}: ${res.error}`, "error");
      }
    } catch (e) {
      toast(`Network error for ${file.name}: ${e.message}`, "error");
    }
  }

  el.className = "result-box success";
  el.innerHTML = `✅ Batch process complete! ${files.length} files processed.`;
  if (lastOutputDir) {
    el.innerHTML += `<div style="margin-top:10px">
       <button class="btn btn-primary btn-sm" onclick="openFolder('${lastOutputDir.replace(/\\/g, '\\\\')}')">📂 Open Output Folder</button>
    </div>`;
    openFolder(lastOutputDir);
  }
  toast("Batch compression successful!", "success");
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
        openFolder(res.output_dir);
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
  el.innerHTML = `<span class="spinner"></span> Launching Chrome (legacy debug mode)...`;

  try {
    const res = await apiFetch("/api/bid/launch-chrome", "POST");
    if (res.success) {
      el.className = "result-box success";
      el.innerHTML = `🌐 Chrome launched (debug). If GeM logs out in this mode, keep Direct Mode OFF and just start download in Managed Mode instead.`;
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

async function openBidPageInChrome() {
  const gemUrl = v("bid-gem-url").trim();
  const el = document.getElementById("bid-execute-status");
  const btn = document.getElementById("bid-open-chrome-btn");
  if (el) {
    el.style.display = "block";
    el.className = "result-box";
    el.innerHTML = `<span class="spinner"></span> Opening Chrome in debug mode on port 9222...`;
  }
  if (btn) btn.disabled = true;

  try {
    const res = await apiFetch("/api/utils/open-chrome", "POST", {
      url: gemUrl || "https://gem.gov.in"
    });
    if (res.success) {
      if (el) {
        el.className = "result-box success";
        el.innerHTML = `Debug Chrome is ready on port ${esc(res.port || 9222)} and opened ${esc(res.url || gemUrl || "https://gem.gov.in")}.`;
      }
      toast("Debug Chrome opened.", "success");
    } else {
      if (el) {
        el.className = "result-box error";
        el.innerHTML = `❌ ${esc(res.error || "Failed to open debug Chrome.")}`;
      }
    }
  } catch (e) {
    if (el) {
      el.className = "result-box error";
      el.innerHTML = `❌ Network Error: ${esc(String(e))}`;
    }
  } finally {
    if (btn) btn.disabled = false;
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
    download_all: downloadAll,
    use_direct_mode: document.getElementById("bid-direct-mode").checked
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
        logToElement("bid-live-log", data.message);
        progressText.innerHTML = esc(data.message);
        updateStats(data.stats);
      }
      else if (data.type === "progress") {
        logToElement("bid-live-log", `${data.status.toUpperCase()}: ${data.message}`);
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
}// ─── BID DOWNLOADER V2 (AGENT) ───────────────────
async function startBidDownloadV2() {
  const gem_url = v("bid-gem-url");
  const docTypesRaw = v("bid-doc-types").trim();
  const doc_types = docTypesRaw
    ? docTypesRaw.split(",").map(s => s.trim()).filter(Boolean)
    : [];
  const download_all = document.getElementById("bid-download-all").checked;
  const si_from = v("bid-si-from");
  const si_to = v("bid-si-to");
  
  const el = document.getElementById("bid-v2-status") || document.getElementById("bid-execute-status");
  const progressContainer = document.getElementById("bid-v2-progress-container") || document.getElementById("bid-progress-container");
  const startBtn = document.getElementById("bid-v2-start");
  const stopBtn = document.getElementById("bid-v2-stop");
  
  el.style.display = "none";
  progressContainer.style.display = "block";

  if (!download_all && doc_types.length === 0) {
    el.style.display = "block";
    el.className = "result-box error";
    el.innerHTML = `❌ ${esc("Enter document types or choose Download All.")}`;
    return;
  }
  
  try {
    const res = await apiFetch("/api/bid_v2/execute", "POST", { gem_url, doc_types, download_all, si_from, si_to });
    if (!res.success) {
      el.style.display = "block";
      el.className = "result-box error";
      el.innerHTML = `❌ ${esc(res.error || "Failed to start Agent V2.")}`;
      return;
    }
    
    const jobId = res.job_id;
    currentBidV2JobId = jobId;
    if (startBtn) startBtn.style.display = "none";
    if (stopBtn) stopBtn.style.display = "inline-block";
    
    setupBidStreamV2(jobId);
  } catch (e) {
    toast("Error starting Agent V2: " + e.message, "error");
  }
}

function setupBidStreamV2(jobId) {
  const progressText = document.getElementById("bid-progress-text");
  const liveLog = document.getElementById("bid-live-log");
  const startBtn = document.getElementById("bid-v2-start");
  const stopBtn = document.getElementById("bid-v2-stop");
  const statsEl = document.getElementById("bid-stats-summary");
  const progressBar = document.getElementById("bid-progress-bar");
  const progressPct = document.getElementById("bid-progress-pct");

  if (currentBidV2EventSource) {
    try { currentBidV2EventSource.close(); } catch (_) {}
  }
  const eventSource = new EventSource(`/api/bid_v2/stream/${jobId}`);
  currentBidV2EventSource = eventSource;

  const cleanupV2 = () => {
    try { eventSource.close(); } catch (_) {}
    if (currentBidV2EventSource === eventSource) currentBidV2EventSource = null;
    currentBidV2JobId = null;
    if (startBtn) startBtn.style.display = "inline-block";
    if (stopBtn) stopBtn.style.display = "none";
  };
  
  const updateStatsV2 = (s) => {
    if (!s || !statsEl) return;
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
    if (progressBar) progressBar.style.width = `${pct}%`;
    if (progressPct) progressPct.innerHTML = `${pct}%`;
  };

  eventSource.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === "info") {
      if (progressText) progressText.innerText = data.message;
      if (liveLog) {
        const div = document.createElement("div");
        div.innerHTML = `&gt; ${esc(data.message)}`;
        liveLog.prepend(div);
      }
      if (data.stats) updateStatsV2(data.stats);
    } else if (data.type === "progress") {
      if (progressText) progressText.innerText = `Processing: ${esc(data.firm)}`;
      if (liveLog) {
        const div = document.createElement("div");
        div.innerHTML = `&gt; ${esc(data.message)}`;
        liveLog.prepend(div);
      }
      if (data.stats) updateStatsV2(data.stats);
    } else if (data.type === "success") {
      if (progressText) progressText.innerText = "Complete!";
      if (data.stats) updateStatsV2(data.stats);
      if (progressBar) progressBar.style.width = "100%";
      if (progressPct) progressPct.innerHTML = "100%";
      toast("Agent V2 finished successfully!", "success");
      cleanupV2();
    } else if (data.type === "error") {
       if (progressText) progressText.innerText = "Error: " + data.error;
       toast(data.error, "error");
       cleanupV2();
    }
  };
  
  eventSource.onerror = () => {
    cleanupV2();
  };
}

async function stopBidDownloadV2() {
   toast("Stop requested for Agent V2...", "info");
   const jobId = currentBidV2JobId;
   if (!jobId) return;
   try {
      await apiFetch("/api/bid_v2/stop", "POST", { job_id: jobId });
   } catch (e) {
      toast("Stop request failed: " + e.message, "error");
   } finally {
      if (currentBidV2EventSource) {
         try { currentBidV2EventSource.close(); } catch (_) {}
         currentBidV2EventSource = null;
      }
      currentBidV2JobId = null;
      const startBtn = document.getElementById("bid-v2-start");
      const stopBtn = document.getElementById("bid-v2-stop");
      if (startBtn) startBtn.style.display = "inline-block";
      if (stopBtn) stopBtn.style.display = "none";
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

  // Update header pills
  const hb = document.getElementById("header-backend-status");
  const hc = document.getElementById("header-cloud-status");
  if (hb) {
    hb.className = `status-pill ${s.active_backend !== 'None' ? 'online' : 'offline'}`;
    hb.querySelector(".status-text").textContent = `Backend: ${s.active_backend}`;
  }
  if (hc) {
    hc.className = `status-pill ${geminiOk ? 'online' : 'offline'}`;
    hc.querySelector(".status-text").textContent = `Cloud: ${geminiOk ? 'Ready' : 'Missing Key'}`;
  }
  // pre-fill config form
  const se = id => document.getElementById(id);
  const lc = s.llm_config || {};
  if (se("llm-provider")) {
    se("llm-provider").value = s.provider;
    toggleLLMKeyGroups(s.provider);
  }
  
  if (se("llm-gemini-key") && s.gemini_key_set) se("llm-gemini-key").placeholder = "•••••••••••••••• (API set)";
  if (se("llm-groq-key") && lc.groq_api_key) se("llm-groq-key").placeholder = "•••••••••••••••• (API set)";
  
  if (se("llm-gemini-model")) se("llm-gemini-model").value = lc.gemini_model || "gemini-2.0-flash";
  if (se("llm-model-id")) se("llm-model-id").value = (s.provider === 'groq' ? lc.groq_model : lc.gemini_model) || "";
  if (se("llm-temp")) se("llm-temp").value = lc.temperature || 0.3;
  if (se("llm-context")) se("llm-context").value = lc.context_length || 8192;

  if (se("llm-summarization-master-prompt")) se("llm-summarization-master-prompt").value = lc.summarization_master_prompt || "";
  if (se("llm-tec-evaluation-prompt")) se("llm-tec-evaluation-prompt").value = lc.tec_evaluation_prompt || "";
  
  // Missing prompts pre-fill
  if (se("llm-noting-master-prompt")) se("llm-noting-master-prompt").value = lc.noting_master_prompt || "";
  if (se("llm-email-master-prompt")) se("llm-email-master-prompt").value = lc.email_master_prompt || "";
  if (se("llm-knowhow-master-prompt")) se("llm-knowhow-master-prompt").value = lc.qa_system_prompt || "";

  // Proxy settings pre-fill
  const nw = s.network || {};
  if (se("network-proxy-mode")) {
    se("network-proxy-mode").value = nw.proxy_mode || "off";
    toggleProxyFields();
  }
  if (se("network-proxy-server")) se("network-proxy-server").value = nw.proxy_server || "";
  if (se("network-proxy-port")) se("network-proxy-port").value = nw.proxy_port || "";
  if (se("network-proxy-user")) se("network-proxy-user").value = nw.proxy_username || "";

  // Render Quick Analysis Buttons Configuration
  renderQuickAnalysisConfig(lc.quick_analysis_buttons);

  // Render Quick Analysis Buttons in Extraction Page
  renderQuickAnalysisButtons(lc.quick_analysis_buttons);

  // Set up provider change listener
  if (se("llm-provider")) {
    se("llm-provider").onchange = (e) => toggleLLMKeyGroups(e.target.value);
  }
}

async function checkModelAvailability(event) {
  const btn = event.currentTarget;
  const originalHtml = btn.innerHTML;
  btn.innerHTML = `<span class="spinner" style="width:12px;height:12px;border-width:2px;margin:0"></span> Checking...`;
  btn.disabled = true;

  try {
    const models = await apiFetch("/api/ai/models");
    const provider = document.getElementById("llm-provider").value;
    const providerModels = models[provider] || [];

    if (providerModels.length === 0) {
      toast(`No suitable models found for ${provider}. Check your API key.`, "warning");
      return;
    }

    // Show a small dropdown or list
    let listHtml = `<div id="model-availability-list" class="floating-list" style="position:absolute; background:white; border:1px solid var(--border); border-radius:8px; box-shadow:0 10px 30px rgba(0,0,0,0.1); z-index:1000; width:280px; max-height:300px; overflow-y:auto; padding:8px; margin-top:5px; right:0; top:100%;">`;
    listHtml += `<div style="font-size:11px; color:var(--text-secondary); padding:5px; border-bottom:1px solid var(--border); margin-bottom:5px; font-weight:700">Suitable Models for ${provider.toUpperCase()}</div>`;
    
    providerModels.forEach(m => {
      if (!m || !m.id) return;
      const mId = m.id.replace(/'/g, "\\'"); // Escape single quotes for onclick
      listHtml += `
        <div class="model-list-item" style="padding:10px; cursor:pointer; border-radius:4px; font-size:13px; transition:all 0.2s;" onmouseover="this.style.background='rgba(0,0,0,0.05)'" onmouseout="this.style.background='transparent'" onclick="pickModel('${mId}')">
          <div style="font-weight:700; color:var(--primary)">${esc(m.id)}</div>
          <div style="font-size:11px; color:var(--text-secondary); line-height:1.2; margin-top:2px;">${esc(m.name || m.id)}</div>
        </div>
      `;
    });
    listHtml += `</div>`;

    // Remove existing if any
    const existing = document.getElementById("model-availability-list");
    if (existing) existing.remove();

    // Append near the button's parent (label)
    const container = btn.parentElement;
    container.style.position = "relative";
    container.insertAdjacentHTML("beforeend", listHtml);

    // Close on click outside
    setTimeout(() => {
      const closeModelList = (e) => {
        const list = document.getElementById("model-availability-list");
        if (list && !list.contains(e.target) && e.target !== btn) {
          list.remove();
          document.removeEventListener("click", closeModelList);
        }
      };
      document.addEventListener("click", closeModelList);
    }, 10);

  } catch (e) {
    console.error(e);
    toast("Failed to fetch models: " + e.message, "error");
  } finally {
    btn.innerHTML = originalHtml;
    btn.disabled = false;
  }
}

function pickModel(modelId) {
  if (!modelId || modelId === 'undefined') {
    console.error("Attempted to select an undefined model ID");
    return;
  }
  const input = document.getElementById("llm-model-id");
  if (input) {
    input.value = modelId;
    input.classList.add("highlight-flash");
    setTimeout(() => input.classList.remove("highlight-flash"), 1000);
  }
  document.getElementById("model-availability-list")?.remove();
  toast("Model selected: " + modelId, "success");
}

function renderQuickAnalysisButtons(buttonsJson) {
  const containers = [
    { el: document.getElementById("quick-analysis-container"), type: 'button' },
    { el: document.getElementById("summary-quick-actions"), type: 'button' },
    { el: document.getElementById("summary-quick-dropdown"), type: 'item' }
  ].filter(c => c.el !== null);
  
  if (containers.length === 0) return;
  
  let buttons = [];
  try {
    buttons = typeof buttonsJson === 'string' ? JSON.parse(buttonsJson) : buttonsJson;
    if (!Array.isArray(buttons)) buttons = [];
  } catch(e) { buttons = []; }

  containers.forEach(c => {
    if (buttons.length === 0) {
      c.el.innerHTML = `<div class="empty-state" style="padding:5px; font-size:11px">No custom buttons defined. Add them in AI Settings.</div>`;
      return;
    }

    if (c.type === 'button') {
      c.el.innerHTML = buttons.map(b => `
        <button class="btn btn-ghost btn-sm" onclick="setExtractContext('${esc(b.prompt).replace(/'/g, "\\'")}', true)" title="${esc(b.prompt)}">
          ${esc(b.label)}
        </button>
      `).join("");
    } else {
      // Dropdown items
      c.el.innerHTML = buttons.map(b => `
        <div class="model-list-item" style="padding:10px; cursor:pointer; font-size:13px; border-bottom:1px solid var(--border);" onclick="setExtractContext('${esc(b.prompt).replace(/'/g, "\\'")}', true)">
          ${esc(b.label)}
        </div>
      `).join("");
    }
  });
}

function toggleSummaryQuickDropdown(event) {
  event.stopPropagation();
  const dropdown = document.getElementById("summary-quick-dropdown");
  if (!dropdown) return;
  
  const isVisible = dropdown.style.display === "block";
  // Close all other dropdowns
  document.querySelectorAll(".floating-list").forEach(el => el.style.display = "none");
  
  if (!isVisible) {
    dropdown.style.display = "block";
    const closeDropdown = (e) => {
      if (!dropdown.contains(e.target)) {
        dropdown.style.display = "none";
        document.removeEventListener("click", closeDropdown);
      }
    };
    document.addEventListener("click", closeDropdown);
  }
}

function renderQuickAnalysisConfig(buttonsJson) {
  const container = document.getElementById("quick-analysis-buttons-config");
  if (!container) return;

  let buttons = [];
  try {
    buttons = typeof buttonsJson === 'string' ? JSON.parse(buttonsJson) : buttonsJson;
    if (!Array.isArray(buttons)) buttons = [];
  } catch(e) { buttons = []; }

  container.innerHTML = buttons.map((b, idx) => `
    <div class="card" style="padding:15px; border:1px solid var(--border); background:rgba(0,0,0,0.02)">
      <div style="display:grid; grid-template-columns: 1fr 2fr auto; gap:10px; align-items:start">
        <div class="form-group">
          <label>Button Label</label>
          <input type="text" class="form-control qa-label" value="${esc(b.label)}" placeholder="e.g. 📝 Summary">
        </div>
        <div class="form-group">
          <label>AI Prompt</label>
          <textarea class="form-control qa-prompt" rows="2">${esc(b.prompt)}</textarea>
        </div>
        <button class="btn btn-danger btn-sm" onclick="this.parentElement.parentElement.remove()" style="margin-top:22px">✕</button>
      </div>
    </div>
  `).join("");
}

function addQuickAnalysisItem() {
  const container = document.getElementById("quick-analysis-buttons-config");
  const div = document.createElement("div");
  div.className = "card";
  div.style = "padding:15px; border:1px solid var(--border); background:rgba(0,0,0,0.02); margin-top:10px;";
  div.innerHTML = `
    <div style="display:grid; grid-template-columns: 1fr 2fr auto; gap:10px; align-items:start">
      <div class="form-group">
        <label>Button Label</label>
        <input type="text" class="form-control qa-label" placeholder="e.g. 🔍 Audit">
      </div>
      <div class="form-group">
        <label>AI Prompt</label>
        <textarea class="form-control qa-prompt" rows="2" placeholder="Instructions for the AI..."></textarea>
      </div>
      <button class="btn btn-danger btn-sm" onclick="this.parentElement.parentElement.remove()" style="margin-top:22px">✕</button>
    </div>
  `;
  container.appendChild(div);
}

async function saveQuickAnalysisConfig() {
  const container = document.getElementById("quick-analysis-buttons-config");
  const items = container.querySelectorAll(".card");
  const buttons = [];
  items.forEach(item => {
    const label = item.querySelector(".qa-label").value.trim();
    const prompt = item.querySelector(".qa-prompt").value.trim();
    if (label && prompt) {
      buttons.push({ id: label.toLowerCase().replace(/\s+/g, '-'), label, prompt });
    }
  });

  try {
    const res = await apiFetch("/api/llm/config", "POST", {
      quick_analysis_buttons: JSON.stringify(buttons)
    });
    if (res.success) {
      toast("Quick Analysis buttons saved!", "success");
      loadLLMStatus(); // Reload UI
    } else {
      toast("Error saving config: " + res.error, "error");
    }
  } catch(e) {
    toast("Connection error: " + e.message, "error");
  }
}

function dropInDefaultPrompt(type) {
  const prompts = {
    noting: `You are an expert procurement professional.

Draft an official noting in Hindi by default. Convert Hinglish into proper official Hindi.
If any sentence is in English, convert it to Hindi unless the source content must stay as-is. Use the available reference context and writing style examples when helpful.
- Table data MUST remain as HTML tables (<table>, <tr>, <td>). Never use Markdown tables (|---|).
- बोली to be replaced with निविदा
- Ensure the firm name / contract name etc remain same throughout if the user forgets to update in later paragraphs.
- बोलीदाता to be replaced with निविदाकर्ता
- Use English alternative (in bracket) of complex Hindi word / terminology.
- If a highly relevant draft or template is found in the Preferred Style Examples, follow its exact structure.
- Check for calculations and correct if wrong. If there is any Figure in Rupees, then same may be written in words in bracket also.

Additional Context:
{additional_context}

Reference Context:
{rag_context}

Preferred Style Examples:
{user_style_examples}

Return only the final noting text without subject or sub-heading.`,
    email: `You are an expert Indian Government official drafting a formal email.\n\nRefine the provided draft into a polished official email body in {target_language}.\n- Keep the output as an email, not a file noting.\n- Never add the closing line "\\u092b\\u093e\\u0907\\u0932 \\u0906\\u092a\\u0915\\u0947 \\u0905\\u0935\\u0932\\u094b\\u0915\\u0928\\u093e\\u0930\\u094d\\u0925 \\u092a\\u094d\\u0930\\u0938\\u094d\\u0924\\u0941\\u0924 \\u0939\\u0948 \\u0964" or any similar file-submission line unless the user explicitly asks for it.\n- If the draft already contains a closing/sign-off, keep only one appropriate closing and do not repeat it.\n- Preserve names, references, numbers, contract details, and email-specific structure unless the user asks to change them.\n- Follow the user's stored style and learned wording preferences whenever relevant.\n\nDraft Content:\n{draft_content}\n\nAdditional Instructions:\n{additional_instructions}\n\nPreferred Style Examples:\n{user_style_examples}\n\nStyle Summary:\n{style_summary}\n\nLearning Instructions:\n{learning_instructions}\n\nReturn only the final email content without explanation.`,
    knowhow: `You are an expert Government Official and Procurement Specialist.\nYour task is to answer user questions based STRICTLY on the provided Knowledge Base context.\n\nIf the information is not in the context, say you don't know rather than hallucinating.\nAlways provide rule numbers or circular references if mentioned in the context.\n\nANSWER PATTERN (strictly follow this order):\n1. GFR 2017: Relevant clause and description (if found in context).\n2. Manual for Procurement of Goods: Relevant clause and description (if found in context).\n3. GeM ATC (Additional Terms & Conditions): Relevant clause and description (if found in context).\n4. GSI Manual: Relevant clause and description (if found in context).\n5. Web Search Result / Supplemental Info: Provide relevant external or supplemental info.\n6. Advisory: Provide a practical advisory or recommendation for the user.\n\n=== LEARNING CONTEXT ===\n{learning_context}\n\n=== KNOWLEDGE BASE CONTEXT ===\n{context}\n==============================\n\nUser Question: {prompt}\n\nProvide a helpful, precise answer in {target_language}.`,
    summarization: `Analyze the following extracted document text based on the USER REQUIREMENT.\n\nUSER REQUIREMENT: {user_requirement}\n\nGUIDELINES:\n1. Provide a structured, professional summary or analysis as per the user requirement.\n2. Maintain an official, government-standard tone.\n3. Highlight key dates, entities (firms, individuals), monetary amounts, and action items.\n4. If technical evaluation is involved, clearly list qualification status for each vendor.\n5. Use Markdown tables or bullet points for clarity.\n6. Provide the result in clean, well-formatted Rich Text (HTML).\n\nEXTRACTED TEXT:\n---\n{document_text}\n---\n`,
    tec_evaluation: `You are an expert Technical Evaluation Committee (TEC) assistant for Government Procurement.\nYour task is to analyze the technical parameters of a firm and decide if they are QUALIFIED or DISQUALIFIED based on the provided criteria.\n\nCRITERIA:\n{criteria}\n\nFIRM DATA:\n{firm_data}\n\nRULES:\n1. Be strict. If a mandatory document is missing or a parameter is 'No'/'Not Submitted', the firm is disqualified.\n2. Provide a concise, professional reason for disqualification.\n3. If the firm is disqualified, the reason MUST start with: "Firm is technically not qualified".\n4. For qualification, provide a brief summary of compliance.\n5. Return the result strictly in JSON format:\n{\n  "is_qualified": boolean,\n  "reason": "Official reason string...",\n  "summary": "Brief summary of parameters analyzed..."\n}`
  };

  const idMap = {
    noting: "llm-noting-master-prompt",
    email: "llm-email-master-prompt",
    knowhow: "llm-knowhow-master-prompt",
    summarization: "llm-summarization-master-prompt",
    tec_evaluation: "llm-tec-evaluation-prompt"
  };

  const el = document.getElementById(idMap[type]);
  if (el) {
    el.value = prompts[type];
    toast(`Default ${type} prompt dropped in!`, "info");
    // Expand the details if closed
    const details = el.closest('details');
    if (details) details.open = true;
  }
}

function toggleLLMKeyGroups(provider) {
  const gemGroup = document.getElementById("gemini-key-group");
  const groqGroup = document.getElementById("groq-key-group");
  if (!gemGroup || !groqGroup) return;

  if (provider === "groq") {
    gemGroup.style.display = "none";
    groqGroup.style.display = "block";
  } else {
    gemGroup.style.display = "block";
    groqGroup.style.display = "none";
  }
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
  } else {
    el.className = "result-box error";
    el.innerHTML = `❌ Error: ${esc(res.error)}`;
    toast("Failed to save network settings", "error");
  }
}

async function saveLLMConfig() {
  const ctxInput = v("llm-context") || "8192";

  const payload = {
    provider: v("llm-provider"),
    gemini_model: v("llm-gemini-model"),
    temperature: parseFloat(v("llm-temp")),
    context_length: parseInt(ctxInput),
    noting_master_prompt: v("llm-noting-master-prompt"),
    email_master_prompt: v("llm-email-master-prompt"),
    qa_system_prompt: v("llm-knowhow-master-prompt"),
    summarization_master_prompt: v("llm-summarization-master-prompt"),
    tec_evaluation_prompt: v("llm-tec-evaluation-prompt")
  };

  // Collect Quick Analysis Buttons
  const qBtnContainer = document.getElementById("quick-analysis-buttons-config");
  if (qBtnContainer) {
    const qItems = qBtnContainer.querySelectorAll(".card");
    const buttons = [];
    qItems.forEach(item => {
      const labelInput = item.querySelector(".qa-label");
      const promptInput = item.querySelector(".qa-prompt");
      if (labelInput && promptInput) {
        const label = labelInput.value.trim();
        const prompt = promptInput.value.trim();
        if (label && prompt) {
          buttons.push({ id: label.toLowerCase().replace(/\s+/g, '-'), label, prompt });
        }
      }
    });
    payload.quick_analysis_buttons = JSON.stringify(buttons);
  }

  const geminiKey = v("llm-gemini-key").trim();
  if (geminiKey && !geminiKey.includes("••••")) {
    payload.gemini_api_key = geminiKey;
  }
  const groqKey = v("llm-groq-key").trim();
  if (groqKey && !groqKey.includes("••••")) {
    payload.groq_api_key = groqKey;
  }

  const res = await apiFetch("/api/llm/config", "POST", payload);
  const el = document.getElementById("llm-config-status");
  el.style.display = "block";
  if (res.success) {
    el.className = "result-box success";
    const activeModel = (payload.provider === "groq" ? payload.groq_model : payload.gemini_model) || "Default";
    el.innerHTML = `✅ LLM config saved. Provider: <strong>${esc(payload.provider)}</strong>, Model: <strong>${esc(activeModel)}</strong>`;
    toast("LLM settings saved!", "success");
    await loadLLMStatus();
  } else {
    el.className = "result-box error";
    el.innerHTML = `❌ Error: ${esc(res.error)}`;
    toast("Failed to save LLM settings", "error");
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
function v(id) { return document.getElementById(id)?.value || ""; }
function esc(s) { return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
function fmt(n) { return n ? Number(n).toLocaleString("en-IN") : "0"; }
function formatDate(d) { if (!d) return "—"; try { return new Date(d).toLocaleDateString("en-IN"); } catch { return d; } }
function copyToClipboard(t) { navigator.clipboard.writeText(t); toast("Copied!", "success"); }
function openFile(p) { fetch(`/api/documents/serve?path=${encodeURIComponent(p)}`); }

function logToElement(id, message, append = true) {
  const el = document.getElementById(id);
  if (!el) return;
  const div = document.createElement("div");
  div.style.marginBottom = "4px";
  div.innerHTML = `&gt; ${esc(message)}`;
  if (append) {
    el.appendChild(div);
  } else {
    el.prepend(div);
  }
  el.scrollTop = el.scrollHeight;
}

// ─── MODULE 10: TEC EVALUATION ────────────────────────────────────────────────
let tecSession = { file_id: null, extension: null };

async function extractTecData() {
  const fileInput = document.getElementById("tec-file");
  const el = document.getElementById("tec-status");
  const tableSec = document.getElementById("tec-table-section");
  const mappingSec = document.getElementById("tec-mapping-section");

  if (!fileInput.files.length) { toast("Please select a PDF or DOCX file.", "error"); return; }

  el.style.display = "block"; el.className = "result-box";
  el.innerHTML = `<span class="spinner"></span> Starting background analysis...`;
  tableSec.style.display = "none";
  mappingSec.style.display = "none";

  const fd = new FormData();
  fd.append("file", fileInput.files[0]);

  try {
    const useLlm = document.getElementById("tec-llm-assist").checked;
    const r = await fetch("/api/tec/analyze", { 
      method: "POST", 
      body: fd,
      headers: { 'X-Use-LLM': useLlm ? 'true' : 'false' }
    });
    const res = await r.json();

    if (res.success && res.job_id) {
      currentTecAnalyzeJobId = res.job_id;
      el.innerHTML = `<span class="spinner"></span> Extracting and analyzing tables in background (Job: ${res.job_id})...`;
      
      const poll = setInterval(async () => {
        try {
          const statusRes = await apiFetch(`/api/tec/analyze-status/${currentTecAnalyzeJobId}`);
          if (statusRes.status === "complete") {
            clearInterval(poll);
            const result = statusRes.result;
            el.style.display = "none";
            tecSession = { file_id: result.file_id, extension: result.extension };
            renderMappingTable(result.parameters);
            mappingSec.style.display = "block";
            toast("Parameters detected! Please define your criteria.", "success");
            currentTecAnalyzeJobId = null;
          } else if (statusRes.status === "failed") {
            clearInterval(poll);
            el.className = "result-box error";
            el.innerHTML = `❌ ${esc(statusRes.error || "Failed to analyze document.")}`;
            toast("Error: " + (statusRes.error || "Failed"), "error");
            currentTecAnalyzeJobId = null;
          }
        } catch (pollErr) {
          clearInterval(poll);
          el.className = "result-box error";
          el.innerHTML = `❌ Polling Error: ${esc(String(pollErr))}`;
          currentTecAnalyzeJobId = null;
        }
      }, 2000);
    } else {
      el.className = "result-box error";
      el.innerHTML = `❌ ${esc(res.error || "Failed to start analysis.")}`;
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
  el.innerHTML = `<span class="spinner"></span> Submitting extraction job...`;
  mappingSec.style.display = "none";

  try {
    const res = await apiFetch("/api/tec/extract", "POST", {
      file_id: tecSession.file_id,
      extension: tecSession.extension,
      criteria: criteria,
      use_llm: document.getElementById("tec-llm-assist").checked
    });

    if (res.success && res.job_id) {
      currentTecExtractJobId = res.job_id;
      el.innerHTML = `<span class="spinner"></span> Generating final evaluations in background (Job: ${res.job_id})...`;
      
      const poll = setInterval(async () => {
        try {
          const statusRes = await apiFetch(`/api/tec/extract-status/${currentTecExtractJobId}`);
          if (statusRes.status === "complete") {
            clearInterval(poll);
            const result = statusRes.result;
            el.style.display = "none";
            tableSec.style.display = "block";
            const s = result.stats;
            const statsEl = document.getElementById("tec-stats-summary");
            if (statsEl) {
              statsEl.innerHTML = `<div>Total: ${s.total_detected}</div><div>Qualified: ${s.total_qualified}</div>`;
            }
            tbody.innerHTML = result.results.map((item, idx) => `
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
            currentTecExtractJobId = null;
          } else if (statusRes.status === "failed") {
            clearInterval(poll);
            el.className = "result-box error";
            el.innerHTML = `❌ ${esc(statusRes.error || "Failed to extract results.")}`;
            currentTecExtractJobId = null;
          }
        } catch (pollErr) {
          clearInterval(poll);
          el.className = "result-box error";
          el.innerHTML = `❌ Polling Error: ${esc(String(pollErr))}`;
          currentTecExtractJobId = null;
        }
      }, 2000);
    } else {
      el.className = "result-box error";
      el.innerHTML = `❌ ${esc(res.error || "Failed to start extraction.")}`;
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

  const useDirectMode = document.getElementById("tec-direct-mode")?.checked || false;

  try {
    const res = await apiFetch("/api/tec/execute", "POST", { results, gem_url: gemUrlInput, use_direct_mode: useDirectMode });
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
    const liveLog = document.getElementById("tec-live-log");
    if (liveLog) liveLog.innerHTML = "";

    eventSource.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.type === "info") {
        progressText.innerHTML = esc(data.message);
        logToElement("tec-live-log", data.message);
      }
      if (data.type === "progress") {
        progressText.innerHTML = `Evaluated: ${esc(data.firm)}`;
        logToElement("tec-live-log", `✅ ${data.firm}: ${data.message}`);
        if (data.stats) {
           // update stats if available
        }
      }
      if (data.type === "complete") {
        eventSource.close();
        currentTecJobId = null;
        stopBtn.style.display = "none";
        executeBtn.style.display = "inline-block";
        toast("Complete!", "success");
        logToElement("tec-live-log", "✅ Execution completed successfully.");
      }
      if (data.type === "error") {
        eventSource.close();
        currentTecJobId = null;
        stopBtn.style.display = "none";
        executeBtn.style.display = "inline-block";
        toast("Error during execution", "error");
        logToElement("tec-live-log", "❌ ERROR: " + data.message);
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

// ─── EMAIL LIBRARY ──────────────────────────────────
let emailLibraryData = [];
let OFFICIAL_EMAIL_CATEGORIES = [];

async function fetchEmailLibrary(initialCategory = null) {
  const catListEl = document.getElementById("email-category-list");
  const resultsEl = document.getElementById("email-library-results");
  if (!catListEl || !resultsEl) return;

  try {
    const cats = await apiFetch("/api/email/categories");
    OFFICIAL_EMAIL_CATEGORIES = Array.isArray(cats) ? cats : [];
    
    catListEl.innerHTML = `<button class="btn btn-ghost btn-sm stage-filter-btn" id="email-cat-ALL" onclick="renderEmailLibrary('ALL')">📦 All Templates</button>` +
      OFFICIAL_EMAIL_CATEGORIES.map(c => `
        <button class="btn btn-ghost btn-sm stage-filter-btn" id="email-cat-${c.replace(/\s+/g, '-')}" onclick="renderEmailLibrary('${esc(c)}')">
          📁 ${esc(c)}
        </button>
      `).join("");

    const data = await apiFetch("/api/email/library");
    emailLibraryData = data || [];
    renderEmailLibrary(initialCategory || 'ALL');
  } catch (e) {
    console.error("Email library fetch error:", e);
  }
}

function buildEmailLibraryCard(item) {
  const catOptions = OFFICIAL_EMAIL_CATEGORIES.map(c => `<option value="${esc(c)}" ${c === item.category ? 'selected' : ''}>${esc(c)}</option>`).join("");
  const isCustomBadge = item.is_custom ? `<span class="badge badge-success" style="font-size:8px; margin-left:5px">AI REFINED</span>` : '';

  return `
    <div class="library-item-card" style="position:relative; background:var(--bg-dark); border:1px solid var(--border); border-radius:10px; padding:15px; margin-bottom:15px">
      <div style="font-size:10px; font-weight:700; color:var(--info); margin-bottom:10px; display:flex; justify-content:space-between; align-items:center">
        <div style="display:flex; align-items:center; gap:5px">
           <span style="font-weight:700">#${item.id}</span>
           ${isCustomBadge}
           <input type="text" class="form-control btn-xs library-keyword-editor"
                  value="${esc(item.keyword)}"
                  placeholder="Keyword..."
                  oninput="handleLibraryUpdate(this, ${item.id}, 'keyword', 'email')"
                  style="display:inline-block; width:180px; height:22px; font-size:10px; padding:0 5px; background:rgba(255,255,255,0.05); color:var(--info); border:1px solid var(--border)" />
           <span style="font-size:9px; color:var(--text-muted); opacity:0.7">${formatDate(item.updated_at)}</span>
        </div>
        <div style="display:flex; gap:10px; align-items:center">
           <span id="email-save-status-${item.id}" style="color:var(--success); display:none; font-size:9px">✓ Saved</span>
           <select class="form-control btn-xs" style="width:auto; height:24px; padding:0 5px; font-size:10px" onchange="moveEmail(${item.id}, this.value)">
              <option disabled selected>Move to stage...</option>
              ${catOptions}
           </select>
           <button class="btn btn-danger btn-xs" style="height:24px; padding:0 8px; font-size:10px" onclick="deleteEmail(${item.id})">🗑</button>
        </div>
      </div>
      <div class="library-textarea-wrapper" style="position:relative">
         <div class="form-control library-editor"
          contenteditable="true"
          onblur="handleLibraryUpdate(this, ${item.id}, 'text', 'email')"
          style="font-family:'Tahoma', sans-serif; font-size:11pt; line-height:1.4; background:white; color:black; border:1px solid var(--border); padding:10px; height:auto; min-height:100px; overflow-y:auto; border-radius:8px">${normalizeEditorHtml(item.text || item.content || "")}</div>
         <div style="margin-top:10px; display:flex; gap:8px; align-items:center; border-top:1px solid var(--border); padding-top:10px">
            <input type="text" class="form-control btn-xs" id="refine-email-context-${item.id}"
                   placeholder="Refinement Context (firm name, subject etc.)"
                   style="flex:1; font-size:11pt; padding:6px 10px; height:34px; background:rgba(255,255,255,0.03)" />
            <button class="btn btn-warning btn-sm" onclick="refineEmailLibraryTemplate(${item.id}, this)" style="height:34px; white-space:nowrap">✨ Refine AI</button>
            <button class="btn btn-primary btn-sm" onclick="openTemplateInEditor(${item.id}, 'email')" style="height:34px; white-space:nowrap">🖋️ Edit in Pro</button>
            <button class="btn btn-primary btn-sm" onclick="copyTextDirect(this)" style="height:34px; white-space:nowrap">📋 Copy</button>
         </div>
      </div>
    </div>
  `;
}

function renderEmailLibrary(category) {
  const resultsEl = document.getElementById("email-library-results");
  if (!resultsEl) return;

  window.currentEmailCategory = category;
  document.querySelectorAll("#email-category-list .stage-filter-btn").forEach(btn => btn.classList.remove("active"));
  const activeId = (category === 'ALL') ? 'email-cat-ALL' : `email-cat-${category.replace(/\s+/g, '-')}`;
  document.getElementById(activeId)?.classList.add("active");

  let filtered = (category === 'ALL') ? [...emailLibraryData] : emailLibraryData.filter(i => i.category === category);
  
  const query = (document.getElementById("email-library-search")?.value || "").toLowerCase().trim();
  if (query) {
    filtered = filtered.filter(i => i.keyword.toLowerCase().includes(query) || i.text.toLowerCase().includes(query));
  }

  // Populate Add Modal's Category dropdown
  const addCatSel = document.getElementById("add-email-category");
  if (addCatSel) {
    addCatSel.innerHTML = OFFICIAL_EMAIL_CATEGORIES.map(c => `<option value="${esc(c)}" ${c === category ? 'selected' : ''}>${esc(c)}</option>`).join("");
  }

  if (!filtered.length) {
    resultsEl.innerHTML = `<div class="result-box info" style="text-align:center; padding:40px">No email templates found for "${esc(category)}".</div>`;
    return;
  }

  resultsEl.innerHTML = filtered.map(buildEmailLibraryCard).join("");
  
  setTimeout(() => { document.querySelectorAll(".library-editor").forEach(ta => autoGrowNotingTextarea(ta)); }, 10);
}

async function refineEmailLibraryTemplate(id, btn) {
  const row = btn.closest(".library-item-card");
  const editor = row.querySelector(".library-editor");
  const contextInput = document.getElementById(`refine-email-context-${id}`);
  const mods = contextInput ? contextInput.value.trim() : "";
  const text = (editor.innerText || editor.textContent || "").trim();
  const html = editor.innerHTML;

  if (!text) return toast("Content is empty", "error");

  const originalBtnHtml = btn.innerHTML;
  btn.disabled = true;
  btn.classList.add("loading");
  btn.innerHTML = `<span class="spinner"></span> Refining...`;
  
  try {
    const res = await apiFetch("/api/noting/refine", "POST", {
      text,
      html,
      modifications: mods,
      document_type: "email"
    });

    if (res.success) {
      editor.innerHTML = res.refined_html || res.refined_text;
      toast("Email template refined!", "success");
      if (contextInput) contextInput.value = "";
      
      // Save back to DB
      await handleLibraryUpdate(editor, id, 'text', 'email');
    } else {
      toast(res.error || "Refinement failed", "error");
    }
  } catch (e) {
    toast("Error: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.classList.remove("loading");
    btn.innerHTML = originalBtnHtml;
  }
}

async function submitNewEmail() {
  const keyword = v("add-email-keyword").trim();
  const category = document.getElementById("add-email-category").value;
  const text = v("add-email-text").trim();
  if (!keyword || !text) return toast("Keyword and Body are required", "error");

  const res = await apiFetch("/api/email/library/add", "POST", { category, keyword, text });
  if (res.success) {
    toast("Email added!", "success");
    closeModal("modal-add-email");
    fetchEmailLibrary(category);
  }
}

async function moveEmail(id, newCat) {
  if (!confirm(`Move to "${newCat}"?`)) return fetchEmailLibrary();
  const res = await apiFetch("/api/email/library/move", "POST", { id, category: newCat });
  if (res.success) {
    toast("Moved!", "success");
    fetchEmailLibrary(window.currentEmailCategory);
  }
}

async function deleteEmail(id) {
  if (!confirm("Delete this email template?")) return;
  const res = await apiFetch(`/api/email/library/delete/${id}`, "DELETE");
  if (res.success) {
    toast("Deleted", "info");
    fetchEmailLibrary(window.currentEmailCategory);
  }
}

async function addNewEmailCategory() {
  const name = v("new-email-category-name").trim();
  if (!name) return toast("Category name required", "error");
  const newList = [...OFFICIAL_EMAIL_CATEGORIES, name];
  const res = await apiFetch("/api/email/categories/update", "POST", newList);
  if (res.success) {
    document.getElementById("new-email-category-name").value = "";
    fetchEmailLibrary();
    showManageEmailCategoriesModal();
  }
}

async function showManageEmailCategoriesModal() {
  const listEl = document.getElementById("manage-email-categories-list");
  if (!listEl) return;
  await apiFetch("/api/email/categories").then(cats => { OFFICIAL_EMAIL_CATEGORIES = cats; });
  listEl.innerHTML = OFFICIAL_EMAIL_CATEGORIES.map((c, i) => `
    <div style="display:flex; align-items:center; justify-content:space-between; padding:10px; border-bottom:1px solid var(--border)">
      <span>${esc(c)}</span>
      <button class="btn btn-danger btn-xs" onclick="removeEmailCategory(${i})">🗑</button>
    </div>`).join("");
  openModal("modal-manage-email-categories");
}

async function removeEmailCategory(idx) {
  if (!confirm(`Delete category "${OFFICIAL_EMAIL_CATEGORIES[idx]}"?`)) return;
  const newList = OFFICIAL_EMAIL_CATEGORIES.filter((_, i) => i !== idx);
  await apiFetch("/api/email/categories/update", "POST", newList).then(r => {
    if (r.success) { OFFICIAL_EMAIL_CATEGORIES = newList; fetchEmailLibrary(); showManageEmailCategoriesModal(); }
  });
}

// ─── PRO EDITOR ─────────────────────────────────────
let currentEditorTargetId = null;

function openProEditor(targetId) {
  window.currentEditorTargetId = targetId;
  const sourceEl = document.getElementById(targetId);
  if (!sourceEl) return toast("Source element not found", "error");

  const content = getEditorHtml(targetId);
  openModal("modal-pro-editor");
  
  if (window.quill) {
    // Small delay to ensure modal is visible before Quill refresh
    setTimeout(() => {
      setEditorContent("pro-editor-container", content);
      window.quill.focus();
      // Ensure the toolbar is updated and the editor root is correctly sized
      window.quill.update();
    }, 50);
  }
}

function openTemplateInEditor(id, type) {
  const data = type === 'noting' ? standardLibraryData : emailLibraryData;
  const item = data.find(x => x.id === id);
  if (!item) return toast("Template not found", "error");
  
  const targetId = type === 'noting' ? 'noting-editor-container' : 'email-editor-container';
  const suggestionSectionId = type === 'noting' ? 'noting-suggestion-section' : 'email-suggestion-section';
  
  document.getElementById(suggestionSectionId).style.display = 'block';
  
  // Populate the specific module's container with HTML version for Quill if needed, 
  // but usually we just open the Pro Modal directly.
  // The user said "open in text editor", so let's open the Modal.
  
  currentEditorTargetId = targetId; // We'll apply changes back to this container
  window.currentEditorTargetId = targetId;
  
  // CRITICAL FIX: Use the template's own content, not the background editor's!
  let content = item.text || item.content || "";

  openModal("modal-pro-editor");
  if (window.quill) {
    requestAnimationFrame(() => {
      setEditorContent("pro-editor-container", content);
      window.quill.focus();
    });
  }
}

function applyProEditorChanges() {
  if (!window.currentEditorTargetId) {
    closeModal('modal-pro-editor');
    return;
  }
  
  const content = getEditorHtml("pro-editor-container");
  setEditorContent(window.currentEditorTargetId, content);
  
  toast("Changes applied successfully!", "success");
  closeModal('modal-pro-editor');
}

function proEditorAction(action) {
  if (action === 'remove-space') {
    // Remove blank lines while preserving formatting (operate on HTML)
    if (!window.quill) return;
    const html = window.quill.root.innerHTML;
    // Strip empty <p> and <br>-only paragraphs
    const cleaned = html
      .replace(/<p[^>]*>\s*(<br\s*\/?>)?\s*<\/p>/gi, '')
      .replace(/(\s*<br\s*\/?\s*>){2,}/gi, '<br>')
      .trim();
    window.quill.clipboard.dangerouslyPasteHTML(cleaned);
    toast('Blank lines removed', 'success');
  } else if (action === 'insert-table') {
    insertProTable();
  } else if (action === 'refine') {
    refineProEditorAI();
  } else if (action === 'copy') {
    copyProEditorText();
  } else if (action === 'copy-html') {
    copyProEditorHtml();
  }
}

function insertProTable(rows, cols) {
  if (!window.quill) return toast('Editor not ready', 'error');
  rows = rows || 3;
  cols = cols || 3;

  // Try quill-table-better API first
  try {
    const tableBetterModule = window.quill.getModule('table-better');
    if (tableBetterModule && typeof tableBetterModule.insertTable === 'function') {
      tableBetterModule.insertTable(rows, cols);
      toast(`Table ${rows}×${cols} inserted`, 'success');
      return;
    }
  } catch(e) { /* fall through */ }

  // Fallback: manually build an HTML table and paste it
  let tableHtml = '<table border="1" style="border-collapse:collapse;width:100%;margin:8px 0">';
  tableHtml += '<tbody>';
  for (let r = 0; r < rows; r++) {
    tableHtml += '<tr>';
    for (let c = 0; c < cols; c++) {
      const tag = r === 0 ? 'th' : 'td';
      tableHtml += `<${tag} style="border:1px solid #ccc;padding:8px;min-width:60px">&nbsp;</${tag}>`;
    }
    tableHtml += '</tr>';
  }
  tableHtml += '</tbody></table><p><br></p>';

  const range = window.quill.getSelection(true);
  window.quill.clipboard.dangerouslyPasteHTML(range.index, tableHtml);
  toast(`Table ${rows}×${cols} inserted`, 'success');
}

async function refineProEditorAI() {
  // Use the consolidated function but pass the correct event context if needed
  await refineNotingAI();
}

function copyProEditorText() {
  if (!window.quill) return;
  const text = window.quill.getText();
  navigator.clipboard.writeText(text).then(() => {
    toast('Plain text copied to clipboard!', 'success');
  }).catch(() => {
    toast('Copy failed — try Ctrl+A then Ctrl+C inside the editor', 'error');
  });
}

function copyProEditorHtml() {
  if (!window.quill) return;
  const html = window.quill.root.innerHTML;
  // Use ClipboardItem if available (modern browsers)
  try {
    const blob = new Blob([html], { type: 'text/html' });
    const textBlob = new Blob([window.quill.getText()], { type: 'text/plain' });
    const item = new ClipboardItem({ 'text/html': blob, 'text/plain': textBlob });
    navigator.clipboard.write([item]).then(() => {
      toast('Formatted HTML copied! Paste into Word/LibreOffice.', 'success');
    });
  } catch(e) {
    // Fallback: copy plain text
    navigator.clipboard.writeText(window.quill.getText()).then(() => {
      toast('Copied as plain text (HTML copy not supported by browser)', 'warning');
    });
  }
}

// ─── SHARED UTILS ───────────────────────────────────
function handleLibraryUpdate(el, id, field, type = 'noting') {
  if (el.tagName === 'TEXTAREA') autoGrowNotingTextarea(el);
  const statusId = type === 'noting' ? `library-save-status-${id}` : `email-save-status-${id}`;
  const statusEl = document.getElementById(statusId);
  if (statusEl) statusEl.style.display = 'none';

  clearTimeout(el.saveTimeout);
  el.saveTimeout = setTimeout(async () => {
    const payload = { id };
    // Get content based on element type
    if (el.tagName === 'DIV' && el.contentEditable === "true") {
        payload[field] = el.innerHTML;
    } else {
        payload[field] = el.value;
    }
    
    const path = type === 'noting' ? "/api/noting/library/update" : "/api/email/library/update";
    const res = await apiFetch(path, "POST", payload);
    if (res.success) {
      if (statusEl) {
        statusEl.style.display = 'inline';
        setTimeout(() => { if (statusEl) statusEl.style.display = 'none'; }, 2000);
      }
      const pool = type === 'noting' ? standardLibraryData : emailLibraryData;
      const item = pool.find(x => x.id === id);
      if (item) {
          item[field] = payload[field];
          item.updated_at = new Date().toISOString();
      }
    }
  }, field === 'text' ? 2000 : 1000); // Wait longer for text content
}

async function directEmailDraft() {
  const context = v("email-library-search").trim();
  const status = document.getElementById("email-status");
  status.style.display = "block";
  status.innerHTML = `<span class="spinner"></span> Generating official email draft from library context…`;

  try {
    const res = await apiFetch("/api/noting/draft", "POST", {
        context,
        document_type: "email"
    });

    status.style.display = "none";
    if (res.success) {
      setEditorContent("email-template-editor", res.text || "");
      document.getElementById("email-step-1").style.display = "none";
      document.getElementById("email-step-2").style.display = "block";
      toast("Direct Email draft generated!", "success");

      // Auto-save to library
      try {
        await apiFetch("/api/email/library/add", "POST", {
          stage: "AI Drafts",
          keyword: context.substring(0, 30) + " (Auto-saved Email)",
          text: res.text || ""
        });
      } catch (saveErr) {
        console.warn("Email auto-save failed:", saveErr);
      }
    } else {
      toast(res.error || "Generation failed", "error");
    }
  } catch (e) {
    status.innerHTML = `<span style="color:var(--danger)">Error: ${e.message}</span>`;
  }
}

async function refineEmailAI(clickedBtn) {
  // 1. Determine Source Editor based on the clicked button or modal state
  let sourceId = "email-template-editor";
  let isPro = false;
  
  if (document.getElementById("modal-pro-editor")?.classList.contains("open")) {
    sourceId = "pro-editor-container";
    isPro = true;
  } else if (clickedBtn && clickedBtn.id === "email-refine-btn") {
    sourceId = "email-template-editor";
  } else if (clickedBtn && clickedBtn.id === "email-bar-refine-btn") {
    sourceId = "email-editor-container";
  } else if (getEditorText("email-editor-container").length > 0) {
    sourceId = "email-editor-container";
  }

  const text = getEditorText(sourceId);
  const html = getEditorHtml(sourceId);
  const refineBtn = clickedBtn || document.getElementById("email-refine-btn");
  const mods = isPro ? v("pro-refine-context").trim() : v("email-refine-context").trim();
  const status = isPro ? null : document.getElementById("email-status");

  if (!text) return toast("Base text required", "error");

  if (refineBtn) {
    refineBtn.disabled = true;
    refineBtn.classList.add("loading");
  }
  
  if (status) {
    status.style.display = "block";
    status.innerHTML = `<span class="spinner"></span> AI is refining and formalizing your email…`;
  } else if (isPro) {
    toast("AI is refining your email...", "info");
  }

  try {
    const res = await apiFetch("/api/noting/refine", "POST", {
      text,
      html,
      modifications: mods,
      target_lang: "english",
      document_type: "email"
    });

    if (status) status.style.display = "none";
    if (refineBtn) {
      refineBtn.disabled = false;
      refineBtn.classList.remove("loading");
    }

    if (res.success) {
      if (res.refined_text && res.refined_text.startsWith("[AI Error")) {
          toast(res.refined_text, "error");
          return;
      }

      setEditorContent(sourceId, res.refined_html || res.refined_text || "");
      if (!isPro && sourceId !== "email-editor-container") {
          setEditorContent("email-editor-container", res.refined_html || res.refined_text || "");
      }

      if (!isPro) {
        document.getElementById("email-suggestion-section").style.display = "block";
        document.getElementById("email-suggestion-section").scrollIntoView({ behavior: "smooth" });
      } else {
        document.getElementById("pro-refine-context").value = "";
      }
      toast("Email refined successfully!", "success");
    } else {
      toast(res.error || "Refinement failed", "error");
    }
  } catch (e) {
    if (status) status.innerHTML = `<span style="color:var(--danger)">Error: ${e.message}</span>`;
    if (refineBtn) {
      refineBtn.disabled = false;
      refineBtn.classList.remove("loading");
    }
    toast("Error: " + e.message, "error");
  }
}

function resetEmail() {
  document.getElementById("email-step-1").style.display = "block";
  document.getElementById("email-step-2").style.display = "none";
  document.getElementById("email-suggestion-section").style.display = "none";
  document.getElementById("email-status").style.display = "none";
  document.getElementById("email-library-search").value = "";
  document.getElementById("email-refine-context").value = "";
  setEditorContent("email-template-editor", "");
  setEditorContent("email-editor-container", "");
}

function saveEmailToLibrary() {
  const text = getEditorText("email-editor-container");
  if (!text) return toast("Nothing to save", "error");

  // Populate the "Add New Email" modal with the refined text
  const addTextEl = document.getElementById("add-email-text");
  const addKeywordEl = document.getElementById("add-email-keyword");
  
  if (addTextEl) addTextEl.value = text;
  if (addKeywordEl) {
    const context = document.getElementById("email-library-search")?.value || "";
    addKeywordEl.value = "AI Refined - " + (context.substring(0, 30) || "Untitled");
  }

  // Open the modal
  openModal('modal-add-email');
}

// ─── EXTRACT TEXT MODULE ──────────────────────────
function getExtractStatusEl() {
  return document.getElementById("smart-status");
}

function getSummaryResultEl() {
  return document.getElementById("summary-display") || document.getElementById("ai-summary-result");
}

function setSummaryResult(content) {
  const summaryArea = getSummaryResultEl();
  if (summaryArea) {
    summaryArea.innerHTML = plainTextToHtml(content || "");
  }
}

function resetSummaryResult() {
  const summaryArea = getSummaryResultEl();
  if (summaryArea) {
    summaryArea.innerHTML = `<div class="empty-state">Analysis results will appear here...</div>`;
  }
}

function setExtractActionButtonsDisabled(disabled) {
  ["btn-run-extraction", "btn-run-smart"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = disabled;
  });
}

function getExtractEditorText() {
  if (!window.extractQuill) return "";
  const html = window.extractQuill.root.innerHTML;
  const tmp = document.createElement("div");
  tmp.innerHTML = html;
  return (tmp.innerText || tmp.textContent || "").trim();
}

function renderExtractedResult(result) {
  const out = (result && typeof result === "object") ? (result.text || result.html || "") : (result || "");
  if (window.extractQuill) {
    window.extractQuill.setContents([]);
    const cleanContent = cleanAiOutput(out);
    window.extractQuill.clipboard.dangerouslyPasteHTML(result?.html || plainTextToHtml(cleanContent));
  }
  window.lastExtractedText = out;
  return out;
}

async function runTextExtraction(options = {}) {
  const fileInput = document.getElementById("extract-file-input");
  const status = getExtractStatusEl();
  const btn = document.getElementById("btn-run-extraction");
  const method = "vision"; // Default to vision for better quality
  const autoAnalyze = Boolean(options.autoAnalyze);
  const context = options.context || "";

  if (!fileInput.files.length && !window.extractClipboardBlob) {
    return toast("Please select a file or paste an image first.", "error");
  }

  if (status) {
    status.style.display = "block";
    status.className = "result-box info";
    status.innerHTML = `<span class="spinner"></span> 🚀 Uploading and starting extraction via Vision LLM...`;
  }
  setExtractActionButtonsDisabled(true);

  try {
    const formData = new FormData();
    let fileHash = null;
    
    if (fileInput.files.length) {
      const file = fileInput.files[0];
      formData.append("file", file);
      fileHash = await calculateFileHash(file);
    } else if (window.extractClipboardBlob) {
      formData.append("file", window.extractClipboardBlob, "pasted_image.png");
    }
    formData.append("method", method);

    const res = await fetch("/api/extract/text", {
      method: "POST",
      body: formData
    });

    const data = await res.json();
    if (data.success && data.job_id) {
      pollExtractionStatus(data.job_id, status, btn, "Extraction", (result) => {
        const out = renderExtractedResult(result);
        window.lastFileHash = fileHash;
        window.pendingExtractSource = false;

        if (autoAnalyze) {
          return runSmartProcess({
            text: out,
            fileHash,
            context,
            skipFileInputs: true
          });
        }
      });
    } else {
      if (status) {
        status.className = "result-box error";
        status.innerHTML = `❌ Error: ${esc(data.error)}`;
      }
      setExtractActionButtonsDisabled(false);
    }
  } catch (e) {
    if (status) {
      status.className = "result-box error";
      status.innerHTML = `❌ Connection Error: ${esc(e.message)}`;
    }
    setExtractActionButtonsDisabled(false);
  }
}

async function pollExtractionStatus(jobId, statusEl, btn, label, onComplete) {
  const interval = setInterval(async () => {
    try {
      const res = await apiFetch(`/api/extract/status/${jobId}`);
      if (res.status === "complete") {
        clearInterval(interval);
        if (statusEl) {
          statusEl.className = "result-box success";
          statusEl.innerHTML = `✅ ${label} complete!`;
        }
        setExtractActionButtonsDisabled(false);
        if (onComplete) onComplete(res.result);
      } else if (res.status === "failed") {
        clearInterval(interval);
        if (statusEl) {
          statusEl.className = "result-box error";
          statusEl.innerHTML = `❌ ${label} failed: ${esc(res.error)}`;
        }
        setExtractActionButtonsDisabled(false);
      } else {
        // Still running
        if (statusEl) statusEl.innerHTML = `<span class="spinner"></span> ⚙️ ${label} in progress... please wait.`;
      }
    } catch (e) {
      clearInterval(interval);
      if (statusEl) statusEl.className = "result-box error";
      // Safely stringify — e may be an Error object or raw value, not always a string
      const errMsg = (e && e.message) ? String(e.message) : String(e);
      if (statusEl) statusEl.innerHTML = `❌ Polling Error: ${esc(errMsg)}`;
      setExtractActionButtonsDisabled(false);
    }
  }, 2000);
}

/* ── Direct AI Analyze ─ sends file straight to LLM without OCR extraction ── */
async function runDirectAnalyze() {
  const fileInput = document.getElementById('extract-file-input');
  const context   = document.getElementById('extract-ai-context').value.trim();
  const statusEl  = getExtractStatusEl();
  const btn       = document.getElementById('btn-run-extraction') || document.getElementById('btn-run-smart');

  if (!fileInput || !fileInput.files.length) {
    toast('Please upload a file first (PDF or image).', 'warning');
    return;
  }

  const file = fileInput.files[0];
  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.className = 'result-box info';
    statusEl.innerHTML = '<span class="spinner"></span> 🧠 Sending file directly to AI for analysis...';
  }
  setExtractActionButtonsDisabled(true);

  try {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('context', context || 'Provide a clear, structured summary of this document.');

    const r = await fetch('/api/extract/direct-analyze', { method: 'POST', body: fd });
    const res = await r.json();

    if (res.success && res.job_id) {
      pollExtractionStatus(res.job_id, statusEl, btn, "Direct Analysis", (result) => {
        setSummaryResult(result?.processed_text || "");
        toast('🧠 Direct analysis done!', 'success');
      });
    } else {
      if (statusEl) {
        statusEl.className = 'result-box error';
        statusEl.innerHTML = `❌ ${esc(res.error || 'Direct analysis failed.')}`;
      }
    }
  } catch (e) {
    if (statusEl) {
      statusEl.className = 'result-box error';
      statusEl.innerHTML = `❌ Network Error: ${esc(String(e.message || e))}`;
    }
    setExtractActionButtonsDisabled(false);
  }
}

async function calculateFileHash(file) {
  try {
    const buffer = await file.arrayBuffer();
    const hashBuffer = await crypto.subtle.digest('SHA-256', buffer);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
  } catch (e) {
    console.warn("Hash calculation failed:", e);
    return null;
  }
}

async function runSmartProcess(options = {}) {
  const contextEl = document.getElementById("extract-ai-context");
  const context = options.context ?? (contextEl ? contextEl.value : "");
  const status = getExtractStatusEl();
  const btn = document.getElementById("btn-run-extraction") || document.getElementById("btn-run-smart");
  const fileInput = document.getElementById("summary-file-input");
  
  if (status) {
    status.style.display = "block";
    status.className = "result-box info";
    status.innerHTML = `<span class="spinner"></span> Initializing processing...`;
  }
  setExtractActionButtonsDisabled(true);

  try {
    let rawText = (options.text || window.lastExtractedText || "").trim();
    let fileHash = options.fileHash || window.lastFileHash || null;
    
    // Fallback: strip HTML from Quill if no lastExtractedText
    if (!rawText) {
      rawText = getExtractEditorText();
    }
    
    // 1. Handle direct file upload for summary (Direct Analysis path)
    if (!options.skipFileInputs && fileInput && fileInput.files.length > 0) {
      if (status) status.innerHTML = `<span class="spinner"></span> 📄 Analyzing file directly (One-step summary)...`;
      const file = fileInput.files[0];
      
      const formData = new FormData();
      formData.append("file", file);
      formData.append("context", context);
      formData.append("method", "vision");
      
      const res = await fetch("/api/extract/direct-analyze", { 
        method: "POST", 
        body: formData 
      });
      
      const data = await res.json();
      if (!data.success || !data.job_id) throw new Error(data.error || "Direct analysis failed");
      
      return pollExtractionStatus(data.job_id, status, btn, "Direct Analysis", (result) => {
        const out = result.processed_text || "";
        setSummaryResult(out);
        fileInput.value = ""; 
        toast("Direct analysis complete!", "success");
      });
    }

    const mainInput = document.getElementById("extract-file-input");
    const hasPendingMainSource = !options.skipFileInputs
      && window.pendingExtractSource
      && ((mainInput && mainInput.files.length > 0) || window.extractClipboardBlob);
    if (hasPendingMainSource) {
      return runTextExtraction({ autoAnalyze: true, context });
    }
    
    // 2. Fallback check
    if (!rawText) {
       if (!options.skipFileInputs && ((mainInput && mainInput.files.length > 0) || window.extractClipboardBlob)) {
          return runTextExtraction({ autoAnalyze: true, context });
       }

       if (status) status.style.display = "none";
       setExtractActionButtonsDisabled(false);
       return toast("Please paste text or select a file to summarize.", "info");
    }

    // 3. AI Analysis (Async)
    if (status) status.innerHTML = `<span class="spinner"></span> 🤖 Submitting document for AI Analysis...`;
    const res = await apiFetch("/api/extract/smart-process", "POST", {
      text: rawText,
      context: context,
      file_hash: fileHash
    });

    if (res.success && res.job_id) {
      pollExtractionStatus(res.job_id, status, btn, "AI Analysis", (result) => {
        const out = result.processed_text || "";
        setSummaryResult(out);
        
        // Reset the file input after success
        if (fileInput) fileInput.value = "";
      });
    } else {
      if (status) {
        status.className = "result-box error";
        status.innerHTML = `❌ Error: ${esc(res.error)}`;
      }
      setExtractActionButtonsDisabled(false);
    }
  } catch (e) {
    if (status) {
      status.className = "result-box error";
      status.innerHTML = `❌ Error: ${esc(e.message)}`;
    }
    setExtractActionButtonsDisabled(false);
  }
}

function setExtractContext(text, trigger = false) {
  const textarea = document.getElementById("extract-ai-context");
  if (textarea) {
    textarea.value = text;
    textarea.style.height = "auto";
    textarea.style.height = (textarea.scrollHeight) + "px";
    if (trigger) {
      setTimeout(() => runSmartProcess(), 50);
    }
  }
}

function handleGlobalPaste(e) {
  // Only handle paste if we are on the extract page
  if (!document.getElementById("page-extract")?.classList.contains("active")) return;

  const items = (e.clipboardData || e.originalEvent.clipboardData).items;
  for (const item of items) {
    if (item.type.indexOf("image") !== -1) {
      const blob = item.getAsFile();
      window.extractClipboardBlob = blob;
      window.pendingExtractSource = true;
      window.lastExtractedText = "";
      window.lastFileHash = null;
      if (window.extractQuill) window.extractQuill.setContents([]);
      resetSummaryResult();
      const reader = new FileReader();
      reader.onload = (event) => {
        const container = document.getElementById("extract-preview-container");
        const img = document.getElementById("extract-preview-img");
        const filename = document.getElementById("extract-preview-filename");
        
        container.style.display = "block";
        img.src = event.target.result;
        filename.textContent = `Pasted image (${(blob.size / 1024).toFixed(1)} KB)`;
        
        // Clear file input if image is pasted
        document.getElementById("extract-file-input").value = "";
        toast("Image pasted from clipboard!", "success");
      };
      reader.readAsDataURL(blob);
      break;
    }
  }
}

function clearExtractInput() {
  document.getElementById("extract-file-input").value = "";
  const summaryFileInput = document.getElementById("summary-file-input");
  if (summaryFileInput) summaryFileInput.value = "";
  document.getElementById("extract-preview-container").style.display = "none";
  window.extractClipboardBlob = null;
  window.pendingExtractSource = false;
  window.lastExtractedText = "";
  window.lastFileHash = null;
  if (window.extractQuill) window.extractQuill.setContents([]);
  const status = getExtractStatusEl();
  if (status) status.style.display = "none";
  resetSummaryResult();
}

function previewExtractFile(e) {
  const file = e.target.files[0];
  if (!file) return;

  window.extractClipboardBlob = null; // Clear pasted image if file selected
  window.pendingExtractSource = true;
  window.lastExtractedText = "";
  window.lastFileHash = null;
  if (window.extractQuill) window.extractQuill.setContents([]);
  resetSummaryResult();
  const container = document.getElementById("extract-preview-container");
  const img = document.getElementById("extract-preview-img");
  const filename = document.getElementById("extract-preview-filename");

  if (file.type.startsWith("image/")) {
    const reader = new FileReader();
    reader.onload = (event) => {
      container.style.display = "block";
      img.src = event.target.result;
      filename.textContent = file.name;
    };
    reader.readAsDataURL(file);
  } else if (file.type === "application/pdf") {
    container.style.display = "block";
    img.src = "https://cdn-icons-png.flaticon.com/512/337/337946.png"; // PDF Icon
    filename.textContent = file.name;
  }
}

function updateZipFileList(input) {
  const count = input.files.length;
  const countEl = document.getElementById("zip-file-count");
  const startBtn = document.getElementById("btn-start-zip-process");
  
  if (count > 0) {
    countEl.textContent = `📁 ${count} ZIP file(s) selected`;
    countEl.style.display = "block";
    startBtn.style.display = "inline-block";
  } else {
    countEl.style.display = "none";
    startBtn.style.display = "none";
  }
}

async function processMultipleZipsManual() {
  const input = document.getElementById("zip-batch-input");
  const el = document.getElementById("zip-process-result");
  
  if (!input.files.length) return toast("Select at least one ZIP file", "error");

  el.style.display = "block";
  el.className = "result-box";
  el.innerHTML = `<span class="spinner"></span> Processing ${input.files.length} ZIP files...`;
  
  const formData = new FormData();
  for (let file of input.files) {
    formData.append("files", file); // Backend expects 'files'
  }
  
  // Add options
  formData.append("auto_rename", document.getElementById("zip-opt-rename").checked);
  formData.append("generate_summary", document.getElementById("zip-opt-summary").checked);

  try {
    const res = await fetch("/api/documents/process-zip", {
      method: "POST",
      body: formData
    });
    const data = await res.json();
    if (data.job_id) {
       pollZipStatus(data.job_id, el);
    } else {
       handleZipProcessResponse(data, el);
    }
  } catch (e) {
    el.className = "result-box error";
    el.innerHTML = `❌ Error: ${e.message}`;
    toast("Error: " + e.message, "error");
  }
}

async function processMultipleZips() {
  // Legacy function - redirected to manual flow or kept for compatibility
  processMultipleZipsManual();
}

async function downloadExtractAsDoc() {
  if (!window.extractQuill) return;
  const html = window.extractQuill.root.innerHTML;
  const text = window.extractQuill.getText();

  toast("Saving to Desktop...", "info");
  try {
    const res = await fetch("/api/extract/download-to-desktop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ html, text, filename: `Extracted_Content_${new Date().getTime()}` })
    });
    
    const data = await res.json();
    if (data.success) {
      toast(`✅ Saved to Desktop: ${data.path}`, "success");
      console.log("File saved to:", data.path);
    } else {
      toast("Error: " + (data.error || "Save failed"), "error");
    }
  } catch (e) {
    toast("Error: " + e.message, "error");
  }
}

function copyExtractResult() {
  if (window.extractQuill) {
    const text = window.extractQuill.getText();
    navigator.clipboard.writeText(text).then(() => toast("Copied text!", "success"));
  }
}

async function pasteFromClipboard() {
  try {
    const items = await navigator.clipboard.read();
    for (const item of items) {
      if (item.types.some(t => t.startsWith("image/"))) {
        const type = item.types.find(t => t.startsWith("image/"));
        const blob = await item.getType(type);
        window.extractClipboardBlob = blob;
        window.pendingExtractSource = true;
        window.lastExtractedText = "";
        window.lastFileHash = null;
        if (window.extractQuill) window.extractQuill.setContents([]);
        resetSummaryResult();
        
        const reader = new FileReader();
        reader.onload = (event) => {
          const container = document.getElementById("extract-preview-container");
          const img = document.getElementById("extract-preview-img");
          const filename = document.getElementById("extract-preview-filename");
          
          container.style.display = "block";
          img.src = event.target.result;
          filename.textContent = `Pasted image (${(blob.size / 1024).toFixed(1)} KB)`;
          document.getElementById("extract-file-input").value = "";
          toast("Image pasted from clipboard!", "success");
        };
        reader.readAsDataURL(blob);
        return;
      }
    }
    toast("No image found in clipboard", "error");
  } catch (err) {
    toast("Clipboard access failed", "error");
  }
}

function plainTextToHtml(text) {
  if (!text) return "";
  
  // 1. Clean up AI formatting artifacts
  text = cleanAiOutput(text);

  // 2. Heuristic: If it looks like it already contains HTML tags (e.g., <table>, <p>, <strong>), 
  // return it as is to allow the editor to render it.
  const hasHtml = /<[a-z][\s\S]*>/i.test(text);
  if (hasHtml) {
    // Fix [Parchment] Maximum optimize iterations reached by removing newlines/spaces between tags
    return text.replace(/>\s+</g, '><');
  }

  // 3. Check for Markdown Tables
  if (text.includes('|') && text.includes('--')) {
    text = markdownTablesToHtml(text);
    return text; // It now contains HTML
  }

  // 4. Fallback: Convert plain text with newlines to HTML paragraphs
  return text
    .split(/\n\n+/)
    .map(para => `<p>${esc(para).replace(/\n/g, '<br>')}</p>`)
    .join('');
}

function cleanAiOutput(text) {
  if (!text) return "";
  // Strip code blocks like ```html ... ``` or ```markdown ... ```
  text = text.replace(/^```(html|markdown)?\s*/i, '').replace(/\s*```$/i, '');
  // Remove common AI preambles
  text = text.replace(/^(Here is the|Sure, here is the|Okay, here is the|Below is the).*?:\s*/i, '');
  return text.trim();
}

function markdownTablesToHtml(text) {
  // Simple markdown table to HTML converter
  const lines = text.split('\n');
  let inTable = false;
  let html = '';
  let tableHtml = '';

  lines.forEach(line => {
    if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
      if (!inTable) {
        inTable = true;
        tableHtml = '<table border="1" style="border-collapse: collapse; width: 100%;"><tbody>';
      }
      
      const cells = line.split('|').filter(c => c.trim() !== '' || line.indexOf('|' + c + '|') !== -1);
      // Skip separator lines like |---|---|
      if (line.includes('---')) return;

      tableHtml += '<tr>';
      cells.forEach(cell => {
        tableHtml += `<td style="border: 1px solid #ccc; padding: 8px;">${esc(cell.trim())}</td>`;
      });
      tableHtml += '</tr>';
    } else {
      if (inTable) {
        inTable = false;
        tableHtml += '</tbody></table>';
        html += tableHtml;
        tableHtml = '';
      }
      html += `<p>${esc(line).replace(/\n/g, '<br>')}</p>`;
    }
  });

  if (inTable) {
    tableHtml += '</tbody></table>';
    html += tableHtml;
  }

  return html;
}


// ─── MODEL PICKER LOGIC ─────────────────────────────
async function showModelPicker() {
  const container = document.getElementById("model-list-container");
  openModal("modal-model-picker");
  container.innerHTML = `<div class="empty-state"><span class="spinner"></span> Fetching available models...</div>`;

  try {
    const data = await apiFetch("/api/ai/models");
    let html = "";
    
    // Group by provider
    for (const [provider, models] of Object.entries(data)) {
      if (!models.length) continue;
      html += `<div style="background:var(--bg-card); padding:10px; border-bottom:1px solid var(--border); font-weight:700; color:var(--accent); position:sticky; top:0; z-index:10">${provider.toUpperCase()} Models</div>`;
      models.forEach(m => {
        html += `
          <div class="model-item" style="padding:12px; border-bottom:1px solid var(--border); cursor:pointer; transition:background 0.2s" 
               onclick="selectModel('${provider}', '${m.id}')"
               onmouseover="this.style.background='rgba(255,255,255,0.05)'"
               onmouseout="this.style.background='transparent'">
            <div style="font-weight:600; font-size:14px">${esc(m.name)}</div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px">${esc(m.description || 'No description available')}</div>
            <div style="font-size:10px; color:var(--accent); margin-top:2px; font-family:monospace">${esc(m.id)}</div>
          </div>
        `;
      });
    }
    
    if (!html) html = `<div class="empty-state">No models found. Check your API keys.</div>`;
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = `<div class="result-box error">Failed to load models: ${esc(e.message)}</div>`;
  }
}

function selectModel(provider, modelId) {
  const provSel = document.getElementById("llm-provider");
  const modelIdInput = document.getElementById("llm-model-id");

  if (provider === "gemini") {
    provSel.value = "gemini";
  } else if (provider === "groq") {
    provSel.value = "groq";
  }
  
  if (modelIdInput) modelIdInput.value = modelId;
  
  toggleLLMKeyGroups(provider); // Ensure correct key field is shown
  toast(`Selected model: ${modelId} for ${provider}`, "success");
  closeModal("modal-model-picker");
}

// ─── ZOOM & UI STABILITY ─────────────────────────────
let currentAppZoom = 100;
function toggleAppZoom(delta) {
  if (delta === 0) {
    currentAppZoom = 100;
  } else {
    currentAppZoom = Math.max(50, Math.min(200, currentAppZoom + delta));
  }
  document.body.style.zoom = currentAppZoom + "%";
  const textEl = document.getElementById("zoom-level-text");
  if (textEl) textEl.textContent = currentAppZoom + "%";
  localStorage.setItem("app-zoom-level", currentAppZoom);
}

function clearExtractResultText() {
  if (window.extractQuill && confirm("Are you sure you want to clear the extracted text?")) {
    window.extractQuill.setContents([]);
    toast("Result cleared", "info");
  }
}

function toggleExtractResult() {
  const card = document.getElementById("extract-result-card");
  const icon = document.getElementById("extract-result-toggle-icon");
  if (!card || !icon) return;
  
  const isCollapsed = card.classList.toggle("collapsed-card");
  icon.textContent = isCollapsed ? "▶" : "▼";
  localStorage.setItem("extract-result-collapsed", isCollapsed);
}

function initializeUIStability() {
  const savedZoom = localStorage.getItem("app-zoom-level");
  if (savedZoom) {
    currentAppZoom = parseInt(savedZoom);
    document.body.style.zoom = currentAppZoom + "%";
    const textEl = document.getElementById("zoom-level-text");
    if (textEl) textEl.textContent = currentAppZoom + "%";
  }
  
  const isCollapsed = localStorage.getItem("extract-result-collapsed") === "true";
  if (isCollapsed) {
    const card = document.getElementById("extract-result-card");
    const icon = document.getElementById("extract-result-toggle-icon");
    if (card && icon) {
      card.classList.add("collapsed-card");
      icon.textContent = "▶";
    }
  }

  // Auto-resize all textareas on start and input
  document.querySelectorAll('textarea.form-control').forEach(autoResizeTextarea);
  document.body.addEventListener('input', (e) => {
    if (e.target.tagName === 'TEXTAREA' && e.target.classList.contains('form-control')) {
      autoResizeTextarea(e.target);
    }
  });
} // ← FIXED: was missing closing brace, trapping all monitor functions inside

// --- GeM Monitoring ---
let monitorJobId = null;
let monitorTimer = null;

async function toggleGeMMonitor() {
  const btn = document.getElementById("btn-start-monitor");
  const bidInput = document.getElementById("monitor-bid-id");
  const intervalSelect = document.getElementById("monitor-interval");
  const container = document.getElementById("monitor-status-container");
  
  if (!monitorJobId) {
    const bidId = bidInput.value.trim();
    if (!bidId) {
      alert("Please enter a Bid ID/Number to monitor.");
      return;
    }
    
    btn.disabled = true;
    btn.innerHTML = "<i class='fas fa-spinner fa-spin'></i> Starting...";
    
    try {
      const res = await apiFetch("/api/monitor/start", "POST", {
        bid_id: bidId,
        interval: parseInt(intervalSelect.value),
        gem_url: document.getElementById("tec-gem-url") ? document.getElementById("tec-gem-url").value : ""
      });
      
      if (res.success) {
        monitorJobId = res.job_id;
        btn.disabled = false;
        btn.classList.remove("btn-accent");
        btn.classList.add("btn-danger");
        btn.innerHTML = "🛑 Stop Monitor";
        container.style.display = "block";
        document.getElementById("mon-bid-id").innerText = bidId;
        
        // Start polling status
        pollMonitorStatus();
      } else {
        alert("Failed to start monitor: " + res.error);
        btn.disabled = false;
        btn.innerHTML = "📡 Start Monitor";
      }
    } catch (err) {
      alert("Error starting monitor: " + err);
      btn.disabled = false;
      btn.innerHTML = "📡 Start Monitor";
    }
  } else {
    // Stop monitor
    try {
      await apiFetch(`/api/monitor/stop/${monitorJobId}`, "POST");
      stopLocalMonitor();
    } catch (err) {
      console.error("Error stopping monitor:", err);
      stopLocalMonitor(); // Force stop locally anyway
    }
  }
}

function stopLocalMonitor() {
  const btn = document.getElementById("btn-start-monitor");
  const container = document.getElementById("monitor-status-container");
  
  monitorJobId = null;
  if (monitorTimer) clearTimeout(monitorTimer);
  
  btn.classList.remove("btn-danger");
  btn.classList.add("btn-accent");
  btn.innerHTML = "📡 Start Monitor";
}

async function pollMonitorStatus() {
  if (!monitorJobId) return;
  
  try {
    const res = await apiFetch(`/api/monitor/status/${monitorJobId}`, "GET");
    if (res.success && res.summary) {
      const s = res.summary;
      document.getElementById("mon-current-status").innerText = s.status || "Checking...";
      document.getElementById("mon-last-check").innerText = s.last_check || "—";
      
      const logBox = document.getElementById("monitor-log");
      if (s.history && s.history.length > 0) {
        logBox.innerHTML = s.history.map(h => 
          `<div style="margin-bottom:5px; border-bottom:1px solid #333; padding-bottom:3px">
            <span style="color:var(--accent)">[${h.time}]</span> 
            <span style="color:var(--success)">${h.event}</span>: 
            <strong>${h.status}</strong>
          </div>`
        ).join("");
      } else {
        logBox.innerHTML = "<div style='color:#666'>Connected. Waiting for updates...</div>";
      }
    }
  } catch (err) {
    console.error("Monitor poll error:", err);
  }
  
  // Poll every 10 seconds for UI updates
  if (monitorJobId) {
    monitorTimer = setTimeout(pollMonitorStatus, 10000);
  }
}

// ─── MODULE: TEC MINUTES ───────────────────────────
async function draftTECMinutes() {
  const tecType = v("tec-minutes-type");
  const category = v("tec-minutes-category");
  const indentingMember = v("tec-indenting-member");
  const rawData = v("tec-minutes-raw-data");

  if (!rawData.trim()) {
    return toast("Please provide some tender data in the input box.", "error");
  }

  const btn = event?.target || document.querySelector('[onclick="draftTECMinutes()"]');
  if (btn) btn.disabled = true;
  toast("🤖 Drafting formal TEC minutes...", "info");

  try {
    const res = await apiFetch("/api/tec/minutes/draft", "POST", {
      tec_type: tecType,
      category: category,
      indenting_member: indentingMember,
      raw_input: rawData
    });

    if (res.success && res.draft_html) {
      if (window.tecMinutesQuill) {
        window.tecMinutesQuill.setContents([]);
        window.tecMinutesQuill.clipboard.dangerouslyPasteHTML(res.draft_html);
      } else {
        const editor = document.getElementById("tec-minutes-editor");
        if (editor) editor.innerHTML = res.draft_html;
      }
      toast("✅ TEC Minutes drafted successfully!", "success");
    } else {
      toast("Error: " + (res.error || "Drafting failed"), "error");
    }
  } catch (e) {
    toast("Error: " + e.message, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function downloadTECMinutes() {
  if (!window.tecMinutesQuill) return;
  const html = window.tecMinutesQuill.root.innerHTML;
  const tecType = v("tec-minutes-type");

  toast("Generating Legal-sized DOCX...", "info");
  try {
    const res = await fetch("/api/tec/minutes/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ 
        html, 
        tec_type: tecType,
        filename: `TEC_Minutes_${tecType}_${new Date().getTime()}` 
      })
    });
    
    const data = await res.json();
    if (data.success) {
      toast(`✅ Saved to Desktop: ${data.path}`, "success");
    } else {
      toast("Error: " + (data.error || "Download failed"), "error");
    }
  } catch (e) {
    toast("Error: " + e.message, "error");
  }
}

function copyTECMinutes() {
  if (window.tecMinutesQuill) {
    const text = window.tecMinutesQuill.getText();
    navigator.clipboard.writeText(text).then(() => toast("Copied to clipboard!", "success"));
  }
}

function clearTECMinutesInput() {
  if (confirm("Clear all TEC Minutes input and draft?")) {
    document.getElementById("tec-minutes-raw-data").value = "";
    document.getElementById("tec-indenting-member").value = "";
    if (window.tecMinutesQuill) window.tecMinutesQuill.setContents([]);
  }
}
