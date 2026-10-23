// OBLIGRAPH Global Client Script

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initSidebar();
  initToasts();
  initGlobalSearch();
  initKeyboardShortcuts();
});

// Theme Toggle Management
function initTheme() {
  const themeToggleBtn = document.getElementById("themeToggleBtn");
  const storedTheme = localStorage.getItem("obligraph-theme") || "dark";

  applyTheme(storedTheme);

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener("click", () => {
      const currentTheme = document.documentElement.getAttribute("data-bs-theme") || "dark";
      const newTheme = currentTheme === "dark" ? "light" : "dark";
      applyTheme(newTheme);
      localStorage.setItem("obligraph-theme", newTheme);
    });
  }
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-bs-theme", theme);
  const themeIcon = document.getElementById("themeIcon");
  if (themeIcon) {
    if (theme === "dark") {
      themeIcon.className = "bi bi-moon-stars-fill text-warning";
    } else {
      themeIcon.className = "bi bi-sun-fill text-warning";
    }
  }
}

// Sidebar Drawer for Mobile
function initSidebar() {
  const sidebarToggle = document.getElementById("sidebarToggle");
  const sidebar = document.querySelector(".app-sidebar");
  const backdrop = document.getElementById("sidebarBackdrop");

  if (sidebarToggle && sidebar) {
    sidebarToggle.addEventListener("click", () => {
      sidebar.classList.toggle("show");
      if (backdrop) backdrop.classList.toggle("d-none");
    });
  }

  if (backdrop) {
    backdrop.addEventListener("click", () => {
      if (sidebar) sidebar.classList.remove("show");
      backdrop.classList.add("d-none");
    });
  }
}

// Toast Alert System
function initToasts() {
  const toastElList = [].slice.call(document.querySelectorAll('.toast'));
  toastElList.map(function (toastEl) {
    return new bootstrap.Toast(toastEl, { delay: 4500 });
  }).forEach(toast => toast.show());
}

window.showToast = function(message, type = "info") {
  const container = document.getElementById("toastContainer");
  if (!container) return;

  const bgClass = type === "success" ? "text-bg-success" : 
                  type === "danger" ? "text-bg-danger" : 
                  type === "warning" ? "text-bg-warning" : "text-bg-primary";

  const toastId = "toast-" + Date.now();
  const html = `
    <div id="${toastId}" class="toast align-items-center ${bgClass} border-0 shadow" role="alert" aria-live="assertive" aria-atomic="true">
      <div class="d-flex">
        <div class="toast-body">
          ${message}
        </div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
      </div>
    </div>
  `;
  container.insertAdjacentHTML("beforeend", html);
  const el = document.getElementById(toastId);
  const bsToast = new bootstrap.Toast(el, { delay: 4500 });
  bsToast.show();
  el.addEventListener("hidden.bs.toast", () => el.remove());
};

// Global Fast Autocomplete Search
function initGlobalSearch() {
  const modalEl = document.getElementById("globalSearchModal");
  const searchInput = document.getElementById("globalSearchInput");
  const resultsContainer = document.getElementById("globalSearchResults");

  if (!modalEl || !searchInput || !resultsContainer) return;

  modalEl.addEventListener("shown.bs.modal", () => {
    searchInput.focus();
    searchInput.select();
  });

  let debounceTimer = null;
  searchInput.addEventListener("input", (e) => {
    const query = e.target.value.trim();
    clearTimeout(debounceTimer);

    if (query.length < 2) {
      resultsContainer.innerHTML = `
        <div class="text-center py-4 text-muted small">
          <i class="bi bi-stars text-primary fs-4 d-block mb-1"></i>
          <span>Type at least 2 characters to search across the entire OBLIGRAPH graph.</span>
        </div>
      `;
      return;
    }

    resultsContainer.innerHTML = `
      <div class="text-center py-4">
        <div class="spinner-border spinner-border-sm text-primary" role="status"></div>
        <div class="text-muted small mt-2">Searching active obligation graph...</div>
      </div>
    `;

    debounceTimer = setTimeout(() => {
      fetch(`/api/search?q=${encodeURIComponent(query)}`)
        .then(res => res.json())
        .then(data => {
          if (!data.results || data.results.length === 0) {
            resultsContainer.innerHTML = `
              <div class="text-center py-4 text-muted small">
                <i class="bi bi-search text-secondary fs-4 d-block mb-1"></i>
                <span>No graph records matching "<strong>${query}</strong>"</span>
              </div>
            `;
            return;
          }

          let html = '<div class="d-flex flex-column gap-1">';
          data.results.forEach(item => {
            const badgeClass = item.badge === 'HIGH' ? 'bg-danger' : 
                               item.badge === 'MEDIUM' ? 'bg-warning text-dark' : 
                               item.badge === 'LOW' ? 'bg-success' : 'bg-primary-subtle text-primary border border-primary-subtle';
            html += `
              <a href="${item.url}" class="search-result-item">
                <div class="p-2 rounded bg-surface border border-subtle text-primary">
                  <i class="bi ${item.icon} fs-5"></i>
                </div>
                <div class="flex-grow-1 min-w-0">
                  <div class="d-flex align-items-center gap-2">
                    <span class="fw-semibold small text-truncate">${item.title}</span>
                    <span class="badge ${badgeClass} font-monospace" style="font-size: 0.65rem;">${item.badge}</span>
                  </div>
                  <div class="text-muted fs-xs text-truncate">${item.subtitle}</div>
                </div>
                <i class="bi bi-chevron-right text-muted small"></i>
              </a>
            `;
          });
          html += '</div>';
          resultsContainer.innerHTML = html;
        })
        .catch(err => {
          resultsContainer.innerHTML = `<div class="alert alert-danger py-2 small mb-0">Search query failed: ${err}</div>`;
        });
    }, 200);
  });
}

// Global Keyboard Shortcuts System
function initKeyboardShortcuts() {
  let chordPending = false;
  let chordTimer = null;

  document.addEventListener("keydown", (e) => {
    const targetTag = (e.target.tagName || "").toLowerCase();
    const isInputActive = targetTag === "input" || targetTag === "textarea" || e.target.isContentEditable;

    // Handle '/' for search when not typing in an input
    if (e.key === "/" && !isInputActive) {
      e.preventDefault();
      const searchModal = document.getElementById("globalSearchModal");
      if (searchModal) {
        const bsModal = bootstrap.Modal.getOrCreateInstance(searchModal);
        bsModal.show();
      }
      return;
    }

    // Handle '?' for shortcuts cheatsheet modal
    if (e.key === "?" && !isInputActive) {
      e.preventDefault();
      const shortcutsModal = document.getElementById("shortcutsModal");
      if (shortcutsModal) {
        const bsModal = bootstrap.Modal.getOrCreateInstance(shortcutsModal);
        bsModal.show();
      }
      return;
    }

    // If typing in input, don't execute two-key chords
    if (isInputActive) return;

    // Sequence navigation chords starting with 'g'
    if (e.key.toLowerCase() === "g" && !chordPending) {
      chordPending = true;
      clearTimeout(chordTimer);
      chordTimer = setTimeout(() => { chordPending = false; }, 1200);
      return;
    }

    if (chordPending) {
      chordPending = false;
      clearTimeout(chordTimer);
      const nextKey = e.key.toLowerCase();
      
      const routes = {
        "d": "/dashboard",
        "c": "/contracts/",
        "o": "/obligations/",
        "g": "/obligations/graph",
        "e": "/evidence/",
        "s": "/disputes/",
        "l": "/escalations/",
        "q": "/query/"
      };

      if (routes[nextKey]) {
        e.preventDefault();
        window.location.href = routes[nextKey];
        return;
      }
    }

    // Export report chord: 'e' then 'r'
    if (e.key.toLowerCase() === "e" && !window.exportChordPending) {
      window.exportChordPending = true;
      clearTimeout(window.exportChordTimer);
      window.exportChordTimer = setTimeout(() => { window.exportChordPending = false; }, 1200);
      return;
    }

    if (window.exportChordPending) {
      window.exportChordPending = false;
      clearTimeout(window.exportChordTimer);
      if (e.key.toLowerCase() === "r") {
        e.preventDefault();
        window.location.href = "/reports/export";
        return;
      }
    }
  });
}
