(() => {
  "use strict";

  const shell = document.querySelector("[data-initial-release]");
  const initialRelease = shell.dataset.initialRelease || "r1";
  const state = {
    release: initialRelease,
    route: "/login",
    selectedDoctor: null,
    slots: [],
    booking: freshBooking(),
    appointments: [],
    renderToken: 0,
  };
  const validRoutes = new Set([
    "/login",
    "/app/home",
    "/app/doctors",
    "/app/book",
    "/app/appointments",
    "/app/billing",
    "/app/settings",
  ]);
  const pageNames = {
    home: "Overview",
    doctors: "Doctors",
    book: "New appointment",
    appointments: "Appointments",
    billing: "Billing",
    settings: "Settings",
  };

  class ClinicToggle extends HTMLElement {
    constructor() {
      super();
      this.checked = false;
    }

    connectedCallback() {
      if (this.shadowRoot) {
        return;
      }
      const root = this.attachShadow({ mode: "open" });
      root.innerHTML = `
        <style>
          :host { display: inline-block; }
          button { width: 52px; height: 30px; border: 0; border-radius: 999px; background: #c7d3da; padding: 3px; cursor: pointer; transition: background 140ms ease; }
          button[data-on="true"] { background: #087f8c; }
          span { display: block; width: 24px; height: 24px; border-radius: 50%; background: white; box-shadow: 0 2px 5px rgba(20, 45, 61, .25); transition: transform 140ms ease; }
          button[data-on="true"] span { transform: translateX(22px); }
          :focus-visible { outline: 3px solid rgba(8, 127, 140, .25); outline-offset: 2px; border-radius: 999px; }
        </style>
        <button type="button" role="switch" aria-label="SMS reminders"><span></span></button>`;
      this.button = root.querySelector("button");
      this.button.addEventListener("click", () => {
        this.checked = !this.checked;
        this.#paint();
        this.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
      });
      this.#paint();
    }

    set checked(value) {
      this._checked = Boolean(value);
      if (this.button) {
        this.#paint();
      }
    }

    get checked() {
      return Boolean(this._checked);
    }

    #paint() {
      if (!this.button) {
        return;
      }
      this.button.dataset.on = String(this.checked);
      this.button.setAttribute("aria-checked", String(this.checked));
    }
  }

  if (!customElements.get("mq-toggle")) {
    customElements.define("mq-toggle", ClinicToggle);
  }

  function freshBooking() {
    return {
      step: 0,
      name: "",
      age: "",
      phone: "",
      visitType: "",
      referralSource: "",
      slot: "",
    };
  }

  function isRefresh() {
    return state.release !== "r1";
  }

  function name(value) {
    return isRefresh() ? `v2-${value}` : `mq-${value}`;
  }

  function css(value) {
    return name(value);
  }

  function attr(value) {
    return isRefresh() ? `data-refresh-${value}` : `data-mq-${value}`;
  }

  function releaseNumber() {
    return Number(state.release.slice(1));
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function money(value) {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
      minimumFractionDigits: 2,
    }).format(Number(value));
  }

  function formatDate(value) {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return new Intl.DateTimeFormat("en-IN", {
      weekday: "short",
      day: "2-digit",
      month: "short",
      hour: "numeric",
      minute: "2-digit",
    }).format(parsed);
  }

  function initials(value) {
    return String(value)
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toUpperCase();
  }

  function delay(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const body = response.status === 204 ? null : await response.json();
    if (!response.ok) {
      const detail = body && typeof body.detail === "string" ? body.detail : "Something went wrong";
      throw new Error(detail);
    }
    return body;
  }

  function isAuthenticated() {
    try {
      return sessionStorage.getItem("mq-authenticated") === "true";
    } catch {
      return false;
    }
  }

  function setAuthenticated(value) {
    try {
      if (value) {
        sessionStorage.setItem("mq-authenticated", "true");
      } else {
        sessionStorage.removeItem("mq-authenticated");
      }
    } catch {
      return;
    }
  }

  function sessionFlag(key) {
    try {
      return sessionStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function setSessionFlag(key, value) {
    try {
      sessionStorage.setItem(key, value);
    } catch {
      return;
    }
  }

  function routeFromLocation() {
    const route = window.location.hash.replace(/^#/, "") || "/login";
    return validRoutes.has(route) ? route : "/login";
  }

  function navigate(route, replace = false) {
    const target = `#${route}`;
    if (replace) {
      history.replaceState({ route }, "", target);
    } else {
      history.pushState({ route }, "", target);
    }
    renderRoute();
  }

  function applyChrome() {
    shell.id = name("shell");
    shell.className = css("frame");
    document.body.className = isRefresh() ? "refresh-skin" : "mq-skin";
    const main = document.getElementById("main-content") || document.querySelector("main");
    if (main) {
      main.id = name("main");
      main.className = css("main");
    }
    const toast = document.getElementById("toast-region") || document.querySelector('[aria-live="polite"]');
    if (toast) {
      toast.id = name("toast-region");
      toast.className = css("toast-region");
    }
    const modal = document.getElementById("modal-region") || document.querySelector("#modal-region, #dialog-host");
    if (modal) {
      modal.id = name("modal-region");
    }
  }

  function brand(classPrefix = css("brand")) {
    return `<div class="${classPrefix}"><span class="${name("brand-mark")}" aria-hidden="true">M</span><span>MediQueue</span></div>`;
  }

  function pageHeading(eyebrow, heading, description, extra = "") {
    return `
      <div class="${css("page-heading")}">
        <div>
          <span class="${css("eyebrow")}">${escapeHtml(eyebrow)}</span>
          <h1>${escapeHtml(heading)}</h1>
          <p>${escapeHtml(description)}</p>
        </div>
        ${extra}
      </div>`;
  }

  function renderLogin() {
    applyChrome();
    const main = document.getElementById(name("main"));
    main.innerHTML = `
      <section class="${css("login")}">
        <div class="${css("login-visual")}">
          ${brand()}
          <div class="${css("login-copy")}">
            <h1>Calmer clinics start with a clear queue.</h1>
            <p>Appointments, billing, and patient follow-up in one focused workspace for your front desk team.</p>
          </div>
          <div class="${css("trust")}"><span>Private by design</span><span>Built for Indian clinics</span><span>Always available</span></div>
        </div>
        <div class="${css("login-panel")}">
          <div class="${css("login-box")}">
            <span class="${css("eyebrow")}">Front desk portal</span>
            <h2>Welcome back</h2>
            <p class="${css("login-intro")}">Sign in to manage today’s clinic schedule.</p>
            <form id="${name("login-form")}" class="${css("form")}" novalidate>
              <label class="${css("field")}">
                <span>Email address</span>
                <input class="${css("input")}" name="email" type="email" autocomplete="username" placeholder="you@clinic.in" required>
              </label>
              <label class="${css("field")}">
                <span>Password</span>
                <input class="${css("input")}" name="password" type="password" autocomplete="current-password" placeholder="Enter your password" required>
              </label>
              <div id="${name("form-error")}" class="${css("form-error")}" role="alert" hidden></div>
              <button class="${css("primary")}" type="submit">Sign in to MediQueue</button>
            </form>
            <div class="${css("demo")}"><strong>Demo access</strong><br>reception@mediqueue.io · triage42</div>
          </div>
        </div>
      </section>`;
    const form = document.getElementById(name("login-form"));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = form.querySelector('button[type="submit"]');
      const error = document.getElementById(name("form-error"));
      error.hidden = true;
      submit.disabled = true;
      submit.textContent = "Signing in…";
      try {
        const formData = new FormData(form);
        await api("/api/login", {
          method: "POST",
          body: JSON.stringify({
            email: formData.get("email"),
            password: formData.get("password"),
          }),
        });
        setAuthenticated(true);
        navigate("/app/home");
      } catch (requestError) {
        error.textContent = requestError.message;
        error.hidden = false;
        submit.disabled = false;
        submit.textContent = "Sign in to MediQueue";
      }
    });
    document.querySelector(`#${name("main")}, #main-content`)?.focus();
  }

  function appHeader() {
    const routes = [
      ["home", "Overview"],
      ["doctors", "Doctors"],
      ["book", "New appointment"],
      ["appointments", "Appointments"],
      ["billing", "Billing"],
    ];
    if (state.release !== "r6") {
      routes.push(["settings", "Settings"]);
    }
    const current = state.route.split("/").pop();
    const links = routes
      .map(([route, label]) => {
        const currentAttribute = route === current ? ' aria-current="page"' : "";
        return `<a class="${css("nav-link")}" href="#/app/${route}"${currentAttribute}>${escapeHtml(label)}</a>`;
      })
      .join("");
    return `
      <header class="${css("header")}">
        ${brand()}
        <nav class="${css("nav")}" aria-label="Main navigation">${links}</nav>
        <div class="${css("user")}"><span class="${css("avatar")}">FD</span><span>Front desk</span><button id="${name("logout")}" class="${css("secondary")}" type="button">Sign out</button></div>
      </header>`;
  }

  function emptyContent() {
    return `<div class="${css("loading-card")}" role="status">Loading ${escapeHtml(pageNames[state.route.split("/").pop()] || "page")}…</div>`;
  }

  async function renderRoute() {
    applyChrome();
    const requested = routeFromLocation();
    if (!isAuthenticated() && requested !== "/login") {
      navigate("/login", true);
      return;
    }
    if (isAuthenticated() && requested === "/login") {
      navigate("/app/home", true);
      return;
    }
    state.route = requested;
    closeModal();
    if (requested === "/login") {
      renderLogin();
      return;
    }
    const token = ++state.renderToken;
    const main = document.getElementById(name("main"));
    main.innerHTML = `${appHeader()}<section id="${name("content")}" class="${css("content")}">${emptyContent()}</section>`;
    document.getElementById(name("logout")).addEventListener("click", () => {
      setAuthenticated(false);
      navigate("/login");
    });
    main.querySelectorAll(".mq-nav-link, .v2-nav-link").forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        navigate(link.getAttribute("href").slice(1));
      });
    });
    const content = document.getElementById(name("content"));
    const routeName = requested.split("/").pop();
    if (routeName === "home") {
      renderHome(content, token);
    } else if (routeName === "doctors") {
      await renderDoctors(content, token);
    } else if (routeName === "book") {
      await renderBooking(content, token);
    } else if (routeName === "appointments") {
      await renderAppointments(content, token);
    } else if (routeName === "billing") {
      await renderBilling(content, token);
    } else {
      await renderSettings(content, token);
    }
  }

  async function renderHome(content, token) {
    content.innerHTML = `
      ${pageHeading("Clinic pulse", "Good morning, Front Desk", "Here is what needs attention across today’s schedule.")}
      <div class="${css("metrics")}">
        <div class="${css("metric")}"><span>Confirmed today</span><strong id="${name("metric-today")}">—</strong></div>
        <div class="${css("metric")}"><span>Active doctors</span><strong>8</strong></div>
        <div class="${css("metric")}"><span>Open slots</span><strong>24</strong></div>
      </div>
      <div class="${css("quick-actions")}">
        <a class="${css("quick-action")}" href="#/app/book"><strong>Book an appointment</strong><span>Find a doctor and reserve a time.</span></a>
        <a class="${css("quick-action")}" href="#/app/appointments"><strong>Manage appointments</strong><span>Review, reschedule, or cancel bookings.</span></a>
        <a class="${css("quick-action")}" href="#/app/billing"><strong>View billing</strong><span>Review invoice lines and totals.</span></a>
      </div>`;
    content.querySelectorAll(`.${css("quick-action")}`).forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        navigate(link.getAttribute("href").slice(1));
      });
    });
    try {
      const appointments = await api("/api/appointments");
      if (token !== state.renderToken) {
        return;
      }
      const today = new Date().toDateString();
      const count = appointments.filter((item) => new Date(item.slot).toDateString() === today).length;
      const metric = document.getElementById(name("metric-today"));
      if (metric) {
        metric.textContent = String(count);
      }
    } catch (requestError) {
      showToast(requestError.message, true);
    }
    if (releaseNumber() >= 3 && state.route === "/app/home" && sessionFlag("mq-release-modal-seen") !== "true") {
      window.setTimeout(() => {
        if (state.route === "/app/home") {
          openReleaseModal();
        }
      }, 180);
    }
  }

  function skeletonCards() {
    return Array.from({ length: 8 }, () => `
      <div class="${css("skeleton-card")}" aria-hidden="true">
        <div class="${css("skeleton-line")} circle"></div>
        <div class="${css("skeleton-line")} short"></div>
        <div class="${css("skeleton-line")} medium"></div>
        <div class="${css("skeleton-line")} medium"></div>
      </div>`).join("");
  }

  async function renderDoctors(content, token) {
    content.innerHTML = `
      ${pageHeading("Clinical directory", "Find the right specialist", "Compare expertise, consultation fees, and the next available time.", `<span class="${css("date-chip")}">6 departments online</span>`)}
      <div class="${css("toolbar")}"><h2>Available doctors</h2><span class="${css("count")}" role="status">Loading directory</span></div>
      <div class="${css("doctor-grid")}" aria-busy="true">${skeletonCards()}</div>`;
    await delay(1200);
    try {
      const doctors = await api("/api/doctors");
      if (token !== state.renderToken) {
        return;
      }
      const cards = doctors
        .map((doctor, index) => {
          const iconOnly = isRefresh() && index % 3 === 2;
          const buttonLabel = isRefresh() ? (iconOnly ? "" : "Reserve") : "Book";
          const accessibleLabel = iconOnly ? ' aria-label="Book appointment"' : "";
          return `
            <article id="${name(`doctor-card-${doctor.id}`)}" class="${css("doctor-card")}" ${attr("doctor")}="${escapeHtml(doctor.id)}">
              <div class="${css("doctor-top")}">
                <div class="${css("doctor-avatar")}" aria-hidden="true">${escapeHtml(initials(doctor.name))}</div>
                <div><h2>${escapeHtml(doctor.name)}</h2><span class="${css("specialty")}">${escapeHtml(doctor.specialty)}</span></div>
              </div>
              <div class="${css("doctor-facts")}">
                <div><span>Consultation</span><strong>${money(doctor.fee)}</strong></div>
                <div><span>Next available</span><strong>${escapeHtml(doctor.next_slot)}</strong></div>
              </div>
              <button class="${iconOnly ? css("icon-button") : css("primary")}" type="button" ${attr("book-doctor")}="${escapeHtml(doctor.id)}"${accessibleLabel}>${buttonLabel}</button>
            </article>`;
        })
        .join("");
      content.querySelector(`.${css("doctor-grid")}`).innerHTML = cards;
      content.querySelector(`.${css("doctor-grid")}`).setAttribute("aria-busy", "false");
      content.querySelector(`.${css("count")}`).textContent = `${doctors.length} doctors available`;
      content.querySelectorAll(`[${attr("book-doctor")}]`).forEach((button) => {
        button.addEventListener("click", () => {
          const doctor = doctors.find((item) => item.id === button.getAttribute(attr("book-doctor")));
          state.booking = freshBooking();
          state.selectedDoctor = doctor;
          state.slots = [];
          navigate("/app/book");
        });
      });
    } catch (requestError) {
      content.innerHTML = `<div class="${css("form-error")}" role="alert">${escapeHtml(requestError.message)}</div>`;
    }
  }

  function customDropdown(field, label, value, options, required = false) {
    const selected = options.find((option) => option === value);
    return `
      <div class="${css("field")} ${css("field-wide")}">
        <span>${escapeHtml(label)}${required ? " *" : ""}</span>
        <div class="${css("select")}" ${attr("dropdown")}="${escapeHtml(field)}" data-open="false">
          <button class="${css("select-trigger")}" type="button" aria-haspopup="listbox" aria-expanded="false">${escapeHtml(selected || `Choose ${label.toLowerCase()}`)}</button>
          <div class="${css("options")}" role="listbox" aria-label="${escapeHtml(label)}">
            ${options.map((option) => `<button class="${css("option")}" type="button" role="option" aria-selected="${option === value}" ${attr("option")}="${escapeHtml(option)}">${escapeHtml(option)}</button>`).join("")}
          </div>
        </div>
      </div>`;
  }

  function patientStep() {
    const maxAge = state.release === "r5" ? "" : ' max="120" min="0" inputmode="numeric"';
    return `
      <div class="${css("wizard-header")}"><h2>Patient details</h2><p>Enter the patient’s contact information and the reason for this visit.</p></div>
      <div class="${css("field-grid")}">
        <label class="${css("field")} ${css("field-wide")}"><span>Patient name *</span><input id="${name("patient-name")}" class="${css("input")}" value="${escapeHtml(state.booking.name)}" autocomplete="name" required></label>
        <label class="${css("field")}"><span>Age *</span><input id="${name("patient-age")}" class="${css("input")}" value="${escapeHtml(state.booking.age)}"${maxAge} required></label>
        <label class="${css("field")}"><span>Phone number *</span><input id="${name("patient-phone")}" class="${css("input")}" value="${escapeHtml(state.booking.phone)}" inputmode="tel" autocomplete="tel" required></label>
        ${customDropdown("visitType", "Visit type", state.booking.visitType, ["Consultation", "Follow-up", "Emergency"], true)}
        ${releaseNumber() >= 3 ? customDropdown("referralSource", "Referral source", state.booking.referralSource, ["Search engine", "Patient recommendation", "Hospital", "Walk-in"], true) : ""}
      </div>`;
  }

  function slotStep() {
    return `
      <div class="${css("wizard-header")}"><h2>Choose a time</h2><p>These times are shown in the clinic’s local timezone.</p></div>
      <div class="${css("chip-grid")}" role="radiogroup" aria-label="Appointment time">
        ${state.slots.map((slot) => `
          <label class="${css("slot")}">
            <input type="radio" name="appointment-slot" value="${escapeHtml(slot.value)}" ${state.booking.slot === slot.value ? "checked" : ""}>
            <span>${escapeHtml(slot.label)}</span>
          </label>`).join("")}
      </div>`;
  }

  function bookingFee() {
    const fee = Number(state.selectedDoctor?.fee || 0);
    if (state.booking.visitType === "Emergency") {
      return state.release === "r5" ? 500 : 0;
    }
    if (state.booking.visitType === "Follow-up") {
      return Math.round(fee * 0.6 * 100) / 100;
    }
    return fee;
  }

  function confirmStep() {
    const doctorFee = bookingFee();
    const gst = Math.round(doctorFee * 0.18 * 100) / 100;
    const total = Math.round((doctorFee + gst) * 100) / 100;
    return `
      <div class="${css("wizard-header")}"><h2>Review and confirm</h2><p>Check the appointment and fee breakdown before saving.</p></div>
      <div class="${css("summary")}">
        <div class="${css("summary-row")}"><span>Patient</span><strong>${escapeHtml(state.booking.name)}, ${escapeHtml(state.booking.age)}</strong></div>
        <div class="${css("summary-row")}"><span>Doctor</span><strong>${escapeHtml(state.selectedDoctor.name)}</strong></div>
        <div class="${css("summary-row")}"><span>Appointment</span><strong>${escapeHtml(formatDate(state.booking.slot))}</strong></div>
        <div class="${css("summary-row")}"><span>Visit type</span><strong>${escapeHtml(state.booking.visitType)}</strong></div>
        ${releaseNumber() >= 3 ? `<div class="${css("summary-row")}"><span>Referral source</span><strong>${escapeHtml(state.booking.referralSource)}</strong></div>` : ""}
        <div class="${css("summary-row")}"><span>Doctor fee</span><strong>${money(doctorFee)}</strong></div>
        <div class="${css("summary-row")}"><span>GST (18%)</span><strong>${money(gst)}</strong></div>
        <div class="${css("summary-row")} ${css("summary-total")}"><span>Total</span><strong>${money(total)}</strong></div>
      </div>`;
  }

  async function ensureDoctor() {
    if (state.selectedDoctor) {
      return;
    }
    const doctors = await api("/api/doctors");
    state.selectedDoctor = doctors[0];
  }

  async function ensureSlots() {
    const doctorId = state.selectedDoctor.id;
    if (state.slots.length) {
      return;
    }
    state.slots = await api(`/api/slots?doctor_id=${encodeURIComponent(doctorId)}`);
  }

  function bookingSteps() {
    return releaseNumber() >= 3 ? ["slot", "patient", "confirm"] : ["patient", "slot", "confirm"];
  }

  async function renderBooking(content, token) {
    try {
      await ensureDoctor();
      await ensureSlots();
    } catch (requestError) {
      content.innerHTML = `<div class="${css("form-error")}" role="alert">${escapeHtml(requestError.message)}</div>`;
      return;
    }
    if (token !== state.renderToken) {
      return;
    }
    const steps = bookingSteps();
    state.booking.step = Math.min(state.booking.step, steps.length - 1);
    const labels = {
      patient: "Patient",
      slot: "Slot",
      confirm: "Confirm",
    };
    const currentStep = steps[state.booking.step];
    const stepContent = currentStep === "patient" ? patientStep() : currentStep === "slot" ? slotStep() : confirmStep();
    const stepper = steps
      .map((step, index) => {
        const current = index === state.booking.step ? ' aria-current="step"' : "";
        const enabled = index < state.booking.step ? "" : " disabled";
        return `<button class="${css("step")}" type="button" data-step-index="${index}"${current}${enabled}><span class="${css("step-number")}">${index + 1}</span><span>${labels[step]}</span></button>`;
      })
      .join("");
    const nextLabel = currentStep === "confirm" ? "Confirm booking" : isRefresh() ? "Proceed" : "Next";
    content.innerHTML = `
      ${pageHeading("Appointment desk", "Book an appointment", "Reserve a time with the selected specialist.")}
      <div class="${css("booking-layout")}>
        <aside class="${css("stepper")}" aria-label="Booking progress">${stepper}</aside>
        <section class="${css("wizard")}">
          ${stepContent}
          <div id="${name("wizard-error")}" class="${css("inline-error")}" role="alert" hidden></div>
          <div class="${css("wizard-actions")}">
            <button id="${name("booking-back")}" class="${css("secondary")}" type="button" ${state.booking.step === 0 ? "disabled" : ""}>Back</button>
            <button id="${name("booking-next")}" class="${css("primary")}" type="button">${nextLabel}</button>
          </div>
        </section>
      </div>`;
    bindBookingEvents(content, currentStep);
  }

  function validateBookingStep(step) {
    const error = document.getElementById(name("wizard-error"));
    if (step === "patient") {
      const age = Number(state.booking.age);
      if (!state.booking.name.trim()) {
        return "Patient name is required";
      }
      if (!Number.isInteger(age) || age < 0 || (state.release !== "r5" && age > 120)) {
        return state.release === "r5" ? "Enter a valid whole-number age" : "Age must be between 0 and 120";
      }
      if (state.booking.phone.trim().length < 6) {
        return "Enter a valid phone number";
      }
      if (!state.booking.visitType) {
        return "Choose a visit type";
      }
      if (releaseNumber() >= 3 && !state.booking.referralSource) {
        return "Choose a referral source";
      }
    }
    if (step === "slot" && !state.booking.slot) {
      return "Choose an appointment time";
    }
    error.hidden = true;
    return "";
  }

  function bindBookingEvents(content, currentStep) {
    const patientName = document.getElementById(name("patient-name"));
    const patientAge = document.getElementById(name("patient-age"));
    const patientPhone = document.getElementById(name("patient-phone"));
    patientName?.addEventListener("input", (event) => { state.booking.name = event.target.value; });
    patientAge?.addEventListener("input", (event) => { state.booking.age = event.target.value; });
    patientPhone?.addEventListener("input", (event) => { state.booking.phone = event.target.value; });
    content.querySelectorAll('input[name="appointment-slot"]').forEach((input) => {
      input.addEventListener("change", (event) => { state.booking.slot = event.target.value; });
    });
    content.querySelectorAll(`[${attr("dropdown")}]`).forEach((dropdown) => {
      const trigger = dropdown.querySelector("button");
      trigger.addEventListener("click", (event) => {
        event.stopPropagation();
        const willOpen = dropdown.dataset.open !== "true";
        content.querySelectorAll(`[${attr("dropdown")}]`).forEach((item) => {
          item.dataset.open = "false";
          item.querySelector("button").setAttribute("aria-expanded", "false");
        });
        dropdown.dataset.open = String(willOpen);
        trigger.setAttribute("aria-expanded", String(willOpen));
      });
      dropdown.querySelectorAll(`[${attr("option")}]`).forEach((option) => {
        option.addEventListener("click", () => {
          const value = option.getAttribute(attr("option"));
          if (dropdown.getAttribute(attr("dropdown")) === "visitType") {
            state.booking.visitType = value;
          } else {
            state.booking.referralSource = value;
          }
          renderRoute();
        });
      });
    });
    content.querySelectorAll("[data-step-index]").forEach((button) => {
      button.addEventListener("click", () => {
        state.booking.step = Number(button.dataset.stepIndex);
        renderRoute();
      });
    });
    document.getElementById(name("booking-back")).addEventListener("click", () => {
      state.booking.step = Math.max(0, state.booking.step - 1);
      renderRoute();
    });
    document.getElementById(name("booking-next")).addEventListener("click", async () => {
      const validationMessage = validateBookingStep(currentStep);
      if (validationMessage) {
        const error = document.getElementById(name("wizard-error"));
        error.textContent = validationMessage;
        error.hidden = false;
        return;
      }
      if (currentStep !== "confirm") {
        state.booking.step += 1;
        renderRoute();
        return;
      }
      const button = document.getElementById(name("booking-next"));
      button.disabled = true;
      button.textContent = "Saving…";
      try {
        await api("/api/appointments", {
          method: "POST",
          body: JSON.stringify({
            patient: state.booking.name,
            age: Number(state.booking.age),
            phone: state.booking.phone,
            doctor_id: state.selectedDoctor.id,
            slot: state.booking.slot,
            visit_type: state.booking.visitType,
            referral_source: state.booking.referralSource || null,
          }),
        });
      } catch (requestError) {
        if (state.release !== "r5") {
          const error = document.getElementById(name("wizard-error"));
          error.textContent = requestError.message;
          error.hidden = false;
          button.disabled = false;
          button.textContent = "Confirm booking";
          return;
        }
      }
      showToast("Appointment confirmed");
      state.selectedDoctor = null;
      state.slots = [];
      state.booking = freshBooking();
      navigate("/app/appointments");
    });
  }

  async function renderAppointments(content, token) {
    try {
      state.appointments = await api("/api/appointments");
    } catch (requestError) {
      content.innerHTML = `<div class="${css("form-error")}" role="alert">${escapeHtml(requestError.message)}</div>`;
      return;
    }
    if (token !== state.renderToken) {
      return;
    }
    content.innerHTML = `
      ${pageHeading("Patient schedule", "Appointments", "Review confirmed visits and make changes when plans change.")}
      <div class="${css("virtual-toolbar")}">
        <div><h2 style="margin:0 0 4px">Upcoming schedule</h2><span class="${css("virtual-hint")}">Scroll the list to reach patients further down the queue.</span></div>
        <span class="${css("count")}">${state.appointments.length} confirmed</span>
      </div>
      <div class="${css("virtual-viewport")}" tabindex="0" aria-label="Appointment list">
        <div class="${css("virtual-spacer")}" style="height:${state.appointments.length * 86}px"></div>
      </div>`;
    const viewport = content.querySelector(`.${css("virtual-viewport")}`);
    const spacer = content.querySelector(`.${css("virtual-spacer")}`);
    let scheduled = false;
    const paintWindow = () => {
      scheduled = false;
      const rowHeight = 86;
      const start = Math.max(0, Math.floor(viewport.scrollTop / rowHeight) - 2);
      const visibleCount = Math.ceil(viewport.clientHeight / rowHeight) + 4;
      const end = Math.min(state.appointments.length, start + visibleCount);
      const rows = state.appointments.slice(start, end).map(appointmentRow).join("");
      spacer.innerHTML = `<div class="${css("appointment-window")}" style="transform:translateY(${start * rowHeight}px)">${rows}</div>`;
      bindAppointmentRows(spacer);
    };
    viewport.addEventListener("scroll", () => {
      if (!scheduled) {
        scheduled = true;
        window.requestAnimationFrame(paintWindow);
      }
    });
    paintWindow();
  }

  function rowActions(appointment) {
    const menu = releaseNumber() >= 4;
    const rescheduled = releaseNumber() >= 3;
    const direct = `
      ${releaseNumber() < 4 ? `<button class="${css("danger")}" type="button" ${attr("cancel")}="${escapeHtml(appointment.id)}">Cancel</button>` : ""}
      ${!rescheduled ? `<button class="${css("primary")}" type="button" ${attr("reschedule")}="${escapeHtml(appointment.id)}">Reschedule</button>` : ""}`;
    if (!menu && !rescheduled) {
      return direct;
    }
    const menuItems = `
      ${rescheduled ? `<button type="button" role="menuitem" ${attr("reschedule")}="${escapeHtml(appointment.id)}">Reschedule</button>` : ""}
      ${releaseNumber() >= 4 ? `<button class="danger" type="button" role="menuitem" ${attr("cancel")}="${escapeHtml(appointment.id)}">Cancel</button>` : ""}`;
    return `${direct}<div class="${css("row-actions")}"><button class="${css("kebab")}" type="button" aria-label="Appointment actions" aria-haspopup="menu" aria-expanded="false">⋮</button><div class="${css("menu")}" role="menu" data-open="false">${menuItems}</div></div>`;
  }

  function appointmentRow(appointment) {
    return `
      <article class="${css("appointment-row")}" ${attr("appointment")}="${escapeHtml(appointment.id)}" ${attr("index")}="${escapeHtml(state.appointments.indexOf(appointment))}" tabindex="0">
        <div class="${css("patient")}"><span class="${css("patient-initials")}">${escapeHtml(initials(appointment.patient))}</span><div><strong>${escapeHtml(appointment.patient)}</strong><span>${escapeHtml(appointment.visit_type)} · ${escapeHtml(appointment.age)} yrs</span></div></div>
        <div><strong>${escapeHtml(appointment.doctor_name)}</strong><span class="${css("appointment-meta")}">${escapeHtml(appointment.specialty)}</span></div>
        <div><strong>${escapeHtml(formatDate(appointment.slot))}</strong><span class="${css("appointment-meta")}">${escapeHtml(appointment.id.toUpperCase())}</span></div>
        <div class="${css("row-actions")}">${rowActions(appointment)}</div>
      </article>`;
  }

  function bindAppointmentRows(root) {
    root.querySelectorAll(`.${css("kebab")}`).forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        const menu = button.nextElementSibling;
        const open = menu.dataset.open !== "true";
        closeAppointmentMenus();
        menu.dataset.open = String(open);
        button.setAttribute("aria-expanded", String(open));
      });
    });
    root.querySelectorAll(`[${attr("reschedule")}]`).forEach((button) => {
      button.addEventListener("click", () => openReschedule(button.getAttribute(attr("reschedule"))));
    });
    root.querySelectorAll(`[${attr("cancel")}]`).forEach((button) => {
      button.addEventListener("click", () => openCancellation(button.getAttribute(attr("cancel"))));
    });
  }

  function closeAppointmentMenus() {
    document.querySelectorAll(`.${css("menu")}`).forEach((menu) => { menu.dataset.open = "false"; });
    document.querySelectorAll(`.${css("kebab")}`).forEach((button) => button.setAttribute("aria-expanded", "false"));
  }

  document.addEventListener("click", (event) => {
    if (!event.target.closest(`.${css("row-actions")}`)) {
      closeAppointmentMenus();
    }
    if (!event.target.closest(`.${css("select")}`)) {
      document.querySelectorAll(`[${attr("dropdown")}]`).forEach((dropdown) => {
        dropdown.dataset.open = "false";
        dropdown.querySelector("button").setAttribute("aria-expanded", "false");
      });
    }
  });

  function openCancellation(appointmentId) {
    const appointment = state.appointments.find((item) => item.id === appointmentId);
    if (!appointment) {
      showToast("Appointment not found", true);
      return;
    }
    const dialog = openModal(`
      <div class="${css("modal-icon")}" aria-hidden="true">!</div>
      <h2>Cancel this appointment?</h2>
      <p>${escapeHtml(appointment.patient)}’s appointment with ${escapeHtml(appointment.doctor_name)} on ${escapeHtml(formatDate(appointment.slot))} will be removed.</p>
      <div class="${css("modal-actions")}"><button class="${css("secondary")}" type="button" data-modal-close>Keep appointment</button><button class="${css("danger")}" type="button" data-confirm-cancel>Yes, cancel</button></div>`,
      "Cancel appointment");
    dialog.querySelector("[data-confirm-cancel]").addEventListener("click", async (event) => {
      event.currentTarget.disabled = true;
      try {
        await api(`/api/appointments/${encodeURIComponent(appointmentId)}`, { method: "DELETE" });
        closeModal();
        showToast("Appointment cancelled");
        navigate("/app/appointments");
      } catch (requestError) {
        event.currentTarget.disabled = false;
        showToast(requestError.message, true);
      }
    });
  }

  async function openReschedule(appointmentId) {
    const appointment = state.appointments.find((item) => item.id === appointmentId);
    if (!appointment) {
      showToast("Appointment not found", true);
      return;
    }
    const dialog = openModal(`
      <div class="${css("modal-icon")}" style="background:#e2f5f5;color:#05616b" aria-hidden="true">↻</div>
      <h2>Reschedule appointment</h2>
      <p>Select a new future time for ${escapeHtml(appointment.patient)} with ${escapeHtml(appointment.doctor_name)}.</p>
      <div id="${name("reschedule-slots")}" class="${css("chip-grid")}" style="margin-top:20px"><span role="status">Loading times…</span></div>
      <div class="${css("modal-actions")}"><button class="${css("secondary")}" type="button" data-modal-close>Close</button><button id="${name("reschedule-confirm")}" class="${css("primary")}" type="button" disabled>Confirm new time</button></div>`,
      "Reschedule appointment");
    let selectedSlot = "";
    try {
      const slots = await api(`/api/slots?doctor_id=${encodeURIComponent(appointment.doctor_id)}`);
      const slotContainer = dialog.querySelector(`#${name("reschedule-slots")}`);
      slotContainer.innerHTML = slots.map((slot) => `
        <label class="${css("slot")}"><input type="radio" name="reschedule-slot" value="${escapeHtml(slot.value)}"><span>${escapeHtml(slot.label)}</span></label>`).join("");
      slotContainer.querySelectorAll("input").forEach((input) => {
        input.addEventListener("change", () => {
          selectedSlot = input.value;
          dialog.querySelector(`#${name("reschedule-confirm")}`).disabled = false;
        });
      });
    } catch (requestError) {
      showToast(requestError.message, true);
    }
    dialog.querySelector(`#${name("reschedule-confirm")}`).addEventListener("click", async (event) => {
      if (!selectedSlot) {
        return;
      }
      event.currentTarget.disabled = true;
      try {
        await api(`/api/appointments/${encodeURIComponent(appointmentId)}/reschedule`, {
          method: "PUT",
          body: JSON.stringify({ slot: selectedSlot }),
        });
        closeModal();
        showToast("Appointment rescheduled");
        navigate("/app/appointments");
      } catch (requestError) {
        event.currentTarget.disabled = false;
        showToast(requestError.message, true);
      }
    });
  }

  async function renderBilling(content, token) {
    try {
      const invoice = await api("/api/billing");
      if (token !== state.renderToken) {
        return;
      }
      content.innerHTML = `
        ${pageHeading("Revenue", "Billing", "A clear view of the current clinic invoice.")}
        <section class="${css("panel")}" style="padding:24px">
          <div class="${css("toolbar")}"><div><h2>Invoice ${escapeHtml(invoice.invoice_number)}</h2><span class="${css("virtual-hint")}">Issued for the current billing period</span></div><span class="${css("count")}">Paid</span></div>
          <div class="${css("table-wrap")}">
            <table class="${css("invoice-table")}">
              <thead><tr><th scope="col">Description</th><th scope="col">Amount</th></tr></thead>
              <tbody>
                ${invoice.line_items.map((item) => `<tr><td>${escapeHtml(item.description)}</td><td>${money(item.amount)}</td></tr>`).join("")}
                <tr class="${css("total-row")}"><td>TOTAL</td><td>${money(invoice.total)}</td></tr>
              </tbody>
            </table>
          </div>
        </section>`;
    } catch (requestError) {
      content.innerHTML = `<div class="${css("form-error")}" role="alert">${escapeHtml(requestError.message)}</div>`;
    }
  }

  async function renderSettings(content, token) {
    if (state.release === "r6") {
      content.innerHTML = `
        ${pageHeading("Administration", "Settings retired", "Clinic settings are now managed by your administrator in the admin portal.")}
        <button class="${css("primary")}" type="button" data-home>Return to overview</button>`;
      content.querySelector("[data-home]").addEventListener("click", () => navigate("/app/home"));
      return;
    }
    try {
      const settings = await api("/api/settings");
      if (token !== state.renderToken) {
        return;
      }
      content.innerHTML = `
        ${pageHeading("Clinic preferences", "Settings", "Keep patient-facing clinic details and reminders up to date.")}
        <section class="${css("panel")}" style="padding:28px">
          <div class="${css("settings-grid")}">
            <div class="${css("setting-card")}">
              <h2>Clinic name</h2>
              <p>Shown in patient messages and appointment reminders.</p>
              <label class="${css("field")}"><span>Display name</span><input id="${name("clinic-name")}" class="${css("input")}" value="${escapeHtml(settings.clinic_name)}"></label>
              <button id="${name("save-clinic")}" class="${css("primary")}" type="button" style="margin-top:14px">Save clinic name</button>
            </div>
            <div class="${css("setting-card")}">
              <h2>SMS reminders</h2>
              <p>Send appointment confirmations and schedule changes by text message.</p>
              <mq-toggle aria-label="SMS reminders"></mq-toggle>
            </div>
          </div>
        </section>`;
      const toggle = content.querySelector("mq-toggle");
      await customElements.whenDefined("mq-toggle");
      toggle.checked = settings.sms_reminders;
      toggle.addEventListener("change", async () => {
        try {
          await api("/api/settings", {
            method: "PUT",
            body: JSON.stringify({ sms_reminders: toggle.checked }),
          });
          showToast("SMS reminder preference saved");
        } catch (requestError) {
          toggle.checked = !toggle.checked;
          showToast(requestError.message, true);
        }
      });
      document.getElementById(name("save-clinic")).addEventListener("click", async (event) => {
        event.currentTarget.disabled = true;
        try {
          const clinicName = document.getElementById(name("clinic-name")).value;
          await api("/api/settings", { method: "PUT", body: JSON.stringify({ clinic_name: clinicName }) });
          showToast("Clinic name saved");
        } catch (requestError) {
          showToast(requestError.message, true);
        } finally {
          event.currentTarget.disabled = false;
        }
      });
    } catch (requestError) {
      content.innerHTML = `<div class="${css("form-error")}" role="alert">${escapeHtml(requestError.message)}</div>`;
    }
  }

  function openModal(content, label) {
    closeModal();
    const host = document.getElementById(name("modal-region"));
    host.innerHTML = `<div class="${css("modal-backdrop")}"><section class="${css("modal")}" role="dialog" aria-modal="true" aria-label="${escapeHtml(label)}">${content}</section></div>`;
    const dialog = host.querySelector('[role="dialog"]');
    dialog.querySelectorAll("[data-modal-close]").forEach((button) => button.addEventListener("click", closeModal));
    window.setTimeout(() => dialog.querySelector("button:not(:disabled), input")?.focus(), 0);
    return dialog;
  }

  function closeModal() {
    const host = document.getElementById(name("modal-region"));
    if (host) {
      host.innerHTML = "";
    }
  }

  function openReleaseModal() {
    if (sessionFlag("mq-release-modal-seen") === "true") {
      return;
    }
    const dialog = openModal(`
      <span class="${css("eyebrow")}">What’s new</span>
      <h2>A smoother booking journey</h2>
      <p>We’ve reorganised appointment booking and made common actions easier to reach from each visit.</p>
      <ul class="${css("release-list")}"><li>Updated booking sequence</li><li>New patient referral question</li><li>Compact appointment actions</li></ul>
      <div class="${css("modal-actions")}"><button class="${css("primary")}" type="button" data-dismiss-release>Got it</button></div>`,
      "What’s new");
    dialog.querySelector("[data-dismiss-release]").addEventListener("click", () => {
      setSessionFlag("mq-release-modal-seen", "true");
      closeModal();
    });
  }

  function showToast(message, error = false) {
    const host = document.getElementById(name("toast-region"));
    if (!host) {
      return;
    }
    const toast = document.createElement("div");
    toast.className = `${css("toast")}${error ? ` ${css("toast")} error` : ""}`;
    toast.setAttribute("role", error ? "alert" : "status");
    toast.textContent = message;
    host.replaceChildren(toast);
    window.setTimeout(() => toast.remove(), 4200);
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      const releaseDialog = document.querySelector(`#${name("modal-region")} [data-dismiss-release]`);
      if (!releaseDialog) {
        closeModal();
      }
    }
  });

  window.addEventListener("popstate", renderRoute);
  window.addEventListener("hashchange", renderRoute);

  async function start() {
    try {
      const remoteState = await api("/api/state");
      state.release = remoteState.release;
    } catch {
      state.release = initialRelease;
    }
    if (!window.location.hash) {
      history.replaceState({ route: "/login" }, "", "#/login");
    }
    await renderRoute();
  }

  start();
})();
