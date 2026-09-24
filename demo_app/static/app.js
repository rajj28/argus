/* SkyOps app.js — data-js hooks only, no ids/classes in selectors */
"use strict";

// ---- Toast ----
function showToast(msg, type = "success") {
  const t = document.createElement("div");
  t.className = `toast toast-${type}`;
  t.setAttribute("role", "status");
  t.setAttribute("aria-live", "polite");
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => {
    t.style.opacity = "0";
    setTimeout(() => t.remove(), 350);
  }, 3000);
}

// ---- Login form ----
const loginForm = document.querySelector("[data-js='login-form']");
if (loginForm) {
  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = loginForm.querySelector("[data-js='login-email']").value.trim();
    const password = loginForm.querySelector("[data-js='login-password']").value;
    const errEl = loginForm.querySelector("[data-js='login-error']");
    if (errEl) errEl.textContent = "";

    try {
      const resp = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (resp.ok) {
        window.location.href = "/dashboard";
      } else {
        const data = await resp.json();
        if (errEl) {
          errEl.textContent = data.error || "Invalid email or password";
          errEl.style.display = "block";
        }
      }
    } catch (err) {
      if (errEl) { errEl.textContent = "Network error. Please try again."; errEl.style.display = "block"; }
    }
  });
}

// ---- Logout ----
function wireLogout() {
  const logoutBtns = document.querySelectorAll("[data-js='logout-btn']");
  // v1.3 bug: logout_crash makes this throw
  const LOGOUT_CRASH = document.documentElement.dataset.logoutCrash === "1";
  logoutBtns.forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      if (LOGOUT_CRASH) {
        // intentional bug: TypeError
        undefined.crash();
      }
      await fetch("/api/logout", { method: "POST" });
      window.location.href = "/login";
    });
  });
}
wireLogout();

// ---- Drone detail: View buttons ----
document.querySelectorAll("[data-js='drone-view-btn']").forEach((btn) => {
  btn.addEventListener("click", () => {
    const droneId = btn.dataset.drone;
    window.location.href = `/drones/${droneId}`;
  });
});

// ---- Missions search ----
const missionSearch = document.querySelector("[data-js='mission-search']");
if (missionSearch) {
  let debounce;
  missionSearch.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(async () => {
      const q = missionSearch.value.trim();
      const url = q ? `/api/missions?q=${encodeURIComponent(q)}` : "/api/missions";
      const data = await fetch(url).then((r) => r.json());
      const tbody = document.querySelector("[data-js='missions-tbody']");
      if (!tbody) return;
      tbody.innerHTML = data.map((m) => `
        <tr>
          <td><a href="/missions/${m.id}">${esc(m.name)}</a></td>
          <td>${esc(m.drone_name)}</td>
          <td>${m.altitude} m</td>
          <td>${statusPill(m.status)}</td>
          <td>${esc(m.created.slice(0, 10))}</td>
        </tr>`).join("") || `<tr><td colspan="5" class="empty-state">No missions found.</td></tr>`;
    }, 300);
  });
}

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function statusPill(status) {
  const map = {
    "Idle": "pill-idle",
    "In mission": "pill-in-mission",
    "Charging": "pill-charging",
    "Maintenance": "pill-maintenance",
    "Scheduled": "pill-scheduled",
    "Completed": "pill-completed",
    "Aborted": "pill-aborted",
  };
  const cls = map[status] || "pill-scheduled";
  return `<span class="pill ${cls}">${esc(status)}</span>`;
}

// ---- Export CSV ----
const exportBtn = document.querySelector("[data-js='export-csv']");
if (exportBtn) {
  exportBtn.addEventListener("click", () => {
    const drone = document.querySelector("[data-js='log-drone-filter']")?.value || "";
    const url = drone ? `/api/logs/export?drone=${encodeURIComponent(drone)}` : "/api/logs/export";
    window.location.href = url;
  });
}

// ---- Log drone filter ----
const logDroneFilter = document.querySelector("[data-js='log-drone-filter']");
if (logDroneFilter) {
  logDroneFilter.addEventListener("change", async () => {
    const drone = logDroneFilter.value;
    const url = drone && drone !== "all" ? `/api/logs?drone=${encodeURIComponent(drone)}` : "/api/logs";
    const data = await fetch(url).then((r) => r.json());
    const tbody = document.querySelector("[data-js='logs-tbody']");
    if (!tbody) return;
    tbody.innerHTML = data.map((l) => `
      <tr>
        <td>${esc(l.date)}</td>
        <td>${esc(l.drone)}</td>
        <td>${esc(l.duration)}</td>
        <td>${esc(String(l.distance_km))} km</td>
        <td>${l.incidents > 0 ? `<span class="badge-danger">${l.incidents}</span>` : "0"}</td>
      </tr>`).join("") || `<tr><td colspan="5" class="empty-state">No logs found.</td></tr>`;
  });
}

// ---- Settings form ----
const settingsForm = document.querySelector("[data-js='settings-form']");
if (settingsForm) {
  settingsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      display_name: settingsForm.querySelector("[data-js='settings-display-name']").value.trim(),
      units: settingsForm.querySelector("[data-js='settings-units']:checked")?.value || "Metric",
      email_reports: settingsForm.querySelector("[data-js='settings-email-reports']").checked,
    };
    try {
      const resp = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (resp.ok) {
        showToast("Settings saved");
      } else {
        showToast("Something went wrong", "error");
      }
    } catch {
      showToast("Network error", "error");
    }
  });
}

// ---- Abort mission ----
const abortBtn = document.querySelector("[data-js='abort-mission-btn']");
if (abortBtn) {
  abortBtn.addEventListener("click", async () => {
    const missionId = abortBtn.dataset.missionId;
    if (!confirm("Are you sure you want to abort this mission?")) return;
    const resp = await fetch(`/api/missions/${missionId}/abort`, { method: "POST" });
    if (resp.ok) {
      const statusEl = document.querySelector("[data-js='mission-status']");
      if (statusEl) statusEl.innerHTML = statusPill("Aborted");
      abortBtn.disabled = true;
      showToast("Mission aborted");
    } else {
      showToast("Failed to abort mission", "error");
    }
  });
}

// ---- Mission wizard ----
(function initWizard() {
  const wizardEl = document.querySelector("[data-js='mission-wizard']");
  if (!wizardEl) return;

  const panels = Array.from(document.querySelectorAll("[data-js='wizard-panel']"));
  const stepCircles = Array.from(document.querySelectorAll("[data-js='step-circle']"));
  const stepLabels = Array.from(document.querySelectorAll("[data-js='step-label']"));
  const stepLines = Array.from(document.querySelectorAll("[data-js='step-line']"));

  let currentStep = 0;
  const formData = {};

  function getStepId(idx) {
    return panels[idx]?.dataset.step || String(idx);
  }

  function showStep(idx) {
    panels.forEach((p, i) => {
      p.classList.toggle("active", i === idx);
      p.setAttribute("aria-hidden", String(i !== idx));
    });
    stepCircles.forEach((c, i) => {
      c.classList.toggle("active", i === idx);
      c.classList.toggle("done", i < idx);
    });
    stepLabels.forEach((l, i) => {
      l.classList.toggle("active", i === idx);
    });
    stepLines.forEach((l, i) => {
      l.classList.toggle("done", i < idx);
    });
    currentStep = idx;
    // Update hash
    const stepId = panels[idx]?.dataset.step;
    if (stepId) history.replaceState(null, "", `#step-${stepId}`);
  }

  function collectStep(idx) {
    const panel = panels[idx];
    const step = panel?.dataset.step;
    const data = {};
    if (step === "details") {
      data.name = panel.querySelector("[data-js='field-mission-name']")?.value.trim() || "";
      data.type = panel.querySelector("[data-js='field-mission-type']")?.value || "";
      data.site = panel.querySelector("[data-js='field-mission-site']")?.value || "";
      const pilotEl = panel.querySelector("[data-js='field-pilot']");
      if (pilotEl) data.pilot = pilotEl.value.trim();
    } else if (step === "drone") {
      const checked = panel.querySelector("[data-js='field-drone']:checked");
      data.drone_id = checked ? checked.value : "";
    } else if (step === "params") {
      data.altitude = panel.querySelector("[data-js='field-altitude']")?.value || "";
      data.speed = panel.querySelector("[data-js='field-speed']")?.value || "";
      data.rth = panel.querySelector("[data-js='field-rth']")?.checked || false;
    }
    return data;
  }

  function clearErrors(panel) {
    panel.querySelectorAll("[data-js='field-error']").forEach((el) => { el.textContent = ""; });
  }

  function showErrors(panel, errors) {
    Object.entries(errors).forEach(([field, msg]) => {
      const errEl = panel.querySelector(`[data-js='field-error'][data-field='${field}']`);
      if (errEl) errEl.textContent = msg;
    });
    // Also show as alert if error el exists
    const alertEl = panel.querySelector("[data-js='step-alert']");
    if (alertEl && Object.keys(errors).length > 0) {
      alertEl.textContent = Object.values(errors)[0];
      alertEl.style.display = "block";
    }
  }

  async function validateStep(idx) {
    const panel = panels[idx];
    const step = panel?.dataset.step;
    const data = collectStep(idx);
    clearErrors(panel);
    const alertEl = panel.querySelector("[data-js='step-alert']");
    if (alertEl) alertEl.style.display = "none";

    try {
      const resp = await fetch("/api/missions/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ step, data }),
      });
      if (resp.ok) return true;
      const body = await resp.json();
      showErrors(panel, body.errors || {});
      return false;
    } catch {
      return false;
    }
  }

  // Next buttons
  document.querySelectorAll("[data-js='wizard-next']").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const valid = await validateStep(currentStep);
      if (!valid) return;
      Object.assign(formData, collectStep(currentStep));
      if (currentStep < panels.length - 1) {
        updateReview();
        showStep(currentStep + 1);
      }
    });
  });

  // Back buttons
  document.querySelectorAll("[data-js='wizard-back']").forEach((btn) => {
    btn.addEventListener("click", () => {
      Object.assign(formData, collectStep(currentStep));
      if (currentStep > 0) showStep(currentStep - 1);
    });
  });

  // Launch button
  const launchBtn = document.querySelector("[data-js='wizard-launch']");
  if (launchBtn) {
    launchBtn.addEventListener("click", async () => {
      // Collect remaining
      for (let i = 0; i < panels.length - 1; i++) {
        Object.assign(formData, collectStep(i));
      }
      try {
        launchBtn.disabled = true;
        const resp = await fetch("/api/missions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(formData),
        });
        if (resp.status === 201) {
          const body = await resp.json();
          window.location.href = `/missions/${body.id}?launched=1`;
        } else {
          const body = await resp.json();
          const errors = body.errors || {};
          showToast(Object.values(errors)[0] || "Failed to launch mission", "error");
          launchBtn.disabled = false;
        }
      } catch {
        showToast("Network error", "error");
        launchBtn.disabled = false;
      }
    });
  }

  // Drone card selection
  document.querySelectorAll(".drone-card:not(.disabled)").forEach((card) => {
    card.addEventListener("click", () => {
      const radio = card.querySelector("input[type='radio']");
      if (radio && !radio.disabled) {
        radio.checked = true;
        document.querySelectorAll(".drone-card").forEach((c) => c.classList.remove("selected"));
        card.classList.add("selected");
      }
    });
  });

  function updateReview() {
    // Collect all data
    for (let i = 0; i < panels.length - 1; i++) {
      Object.assign(formData, collectStep(i));
    }
    const reviewPanel = panels[panels.length - 1];
    if (!reviewPanel) return;
    const setReview = (jsKey, val) => {
      const el = reviewPanel.querySelector(`[data-js='review-${jsKey}']`);
      if (el) el.textContent = val || "—";
    };
    setReview("name", formData.name);
    setReview("type", formData.type);
    setReview("site", formData.site);
    setReview("pilot", formData.pilot);
    setReview("drone", getDroneName(formData.drone_id));
    setReview("altitude", formData.altitude ? `${formData.altitude} m` : "");
    setReview("speed", formData.speed ? `${formData.speed} m/s` : "");
    setReview("rth", formData.rth ? "Yes" : "No");
  }

  function getDroneName(droneId) {
    if (!droneId) return "";
    const card = document.querySelector(`.drone-card input[value='${droneId}']`);
    if (card) return card.closest(".drone-card").querySelector(".drone-name")?.textContent || droneId;
    return droneId;
  }

  // Show initial step from hash
  const hash = window.location.hash;
  const stepMatch = hash.match(/^#step-(.+)$/);
  if (stepMatch) {
    const stepId = stepMatch[1];
    const idx = panels.findIndex((p) => p.dataset.step === stepId);
    if (idx >= 0) showStep(idx);
    else showStep(0);
  } else {
    showStep(0);
  }
})();

// ---- Show launched toast on mission detail ----
if (new URLSearchParams(window.location.search).get("launched") === "1") {
  showToast("Mission launched");
  // Remove param from URL cleanly
  const url = new URL(window.location.href);
  url.searchParams.delete("launched");
  history.replaceState(null, "", url.toString());
}
