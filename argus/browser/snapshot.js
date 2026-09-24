/**
 * Argus DOM distiller — injected via page.evaluate(JS, opts).
 * Returns a plain object shaped like PageSnapshot (signature computed in Python).
 * Must run in < 150 ms on a 1 000-node page. No external libraries. Never mutates the DOM.
 * opts = { maxElements: 350 }
 */
(opts) => {
  const MAX_ELEMENTS = (opts && opts.maxElements) || 350;

  // ── helpers ──────────────────────────────────────────────────────────────────

  function trunc(s, n) {
    s = (s || "").replace(/\s+/g, " ").trim();
    return s.length > n ? s.slice(0, n) : s;
  }

  function isVisible(el) {
    try {
      if (el.checkVisibility) {
        return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
      }
    } catch (_) {}
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || parseFloat(style.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 || r.height > 0;
  }

  function pageRect(el) {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.left + window.scrollX),
      y: Math.round(r.top + window.scrollY),
      w: Math.round(r.width),
      h: Math.round(r.height),
    };
  }

  // ── ARIA implicit role map ────────────────────────────────────────────────────

  function implicitRole(el) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "a" && el.hasAttribute("href")) return "link";
    if (tag === "button") return "button";
    if (tag === "input") {
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "submit" || type === "button" || type === "reset") return "button";
      if (type === "number") return "spinbutton";
      if (type === "range") return "slider";
      if (type === "search") return "searchbox";
      return "textbox";
    }
    if (tag === "textarea") return "textbox";
    if (tag === "select") return "combobox";
    if (tag === "summary") return "button";
    if (tag === "h1") return "heading";
    if (tag === "h2") return "heading";
    if (tag === "h3") return "heading";
    if (tag === "h4") return "heading";
    if (tag === "h5") return "heading";
    if (tag === "h6") return "heading";
    return "";
  }

  function getRole(el) {
    return el.getAttribute("role") || implicitRole(el) || el.tagName.toLowerCase();
  }

  // ── Accessible name computation ───────────────────────────────────────────────

  function getAccessibleName(el) {
    // aria-labelledby
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const ids = labelledBy.trim().split(/\s+/);
      const texts = ids.map(id => {
        const ref = document.getElementById(id);
        return ref ? (ref.innerText || ref.textContent || "") : "";
      }).filter(Boolean);
      if (texts.length) return trunc(texts.join(" "), 80);
    }
    // aria-label
    const ariaLabel = el.getAttribute("aria-label");
    if (ariaLabel && ariaLabel.trim()) return trunc(ariaLabel.trim(), 80);
    // associated label (label[for] or wrapping label)
    const id = el.getAttribute("id");
    if (id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (lbl) {
        // exclude the control's own value from the label text
        const clone = lbl.cloneNode(true);
        const nested = clone.querySelector("input,select,textarea,button");
        if (nested) nested.remove();
        const txt = (clone.innerText || clone.textContent || "").replace(/\s+/g, " ").trim();
        if (txt) return trunc(txt, 80);
      }
    }
    // wrapping label
    const parentLabel = el.closest("label");
    if (parentLabel) {
      const clone = parentLabel.cloneNode(true);
      const nested = clone.querySelector("input,select,textarea,button");
      if (nested) nested.remove();
      const txt = (clone.innerText || clone.textContent || "").replace(/\s+/g, " ").trim();
      if (txt) return trunc(txt, 80);
    }
    // placeholder
    const placeholder = el.getAttribute("placeholder");
    if (placeholder && placeholder.trim()) return trunc(placeholder.trim(), 80);
    // title
    const title = el.getAttribute("title");
    if (title && title.trim()) return trunc(title.trim(), 80);
    // innerText (for buttons, links, etc.)
    const inner = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
    if (inner) return trunc(inner, 80);
    // value for submit/button inputs
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "input" && (type === "submit" || type === "button" || type === "reset")) {
      const val = el.getAttribute("value");
      if (val) return trunc(val, 80);
    }
    // alt of child img
    const img = el.querySelector("img[alt]");
    if (img && img.getAttribute("alt")) return trunc(img.getAttribute("alt"), 80);
    return "";
  }

  function getLabelText(el) {
    const id = el.getAttribute("id");
    if (id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (lbl) {
        const clone = lbl.cloneNode(true);
        const nested = clone.querySelector("input,select,textarea,button");
        if (nested) nested.remove();
        return trunc((clone.innerText || clone.textContent || "").replace(/\s+/g, " ").trim(), 80);
      }
    }
    const parentLabel = el.closest("label");
    if (parentLabel) {
      const clone = parentLabel.cloneNode(true);
      const nested = clone.querySelector("input,select,textarea,button");
      if (nested) nested.remove();
      return trunc((clone.innerText || clone.textContent || "").replace(/\s+/g, " ").trim(), 80);
    }
    return "";
  }

  // ── Context computation ───────────────────────────────────────────────────────

  function getDialogTitle(el) {
    const dlg = el.closest('dialog[open], [role="dialog"]');
    if (!dlg) return "";
    const h = dlg.querySelector("h1,h2,h3,h4,h5,h6,[role=heading]");
    if (h) return trunc((h.innerText || h.textContent || "").trim(), 60);
    const legend = dlg.querySelector("legend");
    if (legend) return trunc((legend.innerText || legend.textContent || "").trim(), 60);
    return "";
  }

  function getTableRowContext(el) {
    const tr = el.closest("tr");
    if (!tr) return "";
    const rowText = (tr.innerText || tr.textContent || "").replace(/\s+/g, " ").trim();
    const elText = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
    let ctx = rowText.replace(elText, "").replace(/\s+/g, " ").trim();
    return trunc(ctx, 60);
  }

  function getNearestHeading(el) {
    let node = el.parentElement;
    while (node && node !== document.body) {
      const heading = node.querySelector(":scope > h1,:scope > h2,:scope > h3,:scope > h4,:scope > h5,:scope > h6,:scope > legend,:scope > [role=heading]");
      if (heading) return trunc((heading.innerText || heading.textContent || "").trim(), 60);
      node = node.parentElement;
    }
    return "";
  }

  function getNearestLandmark(el) {
    const landmarks = ["nav", "header", "main", "aside", "footer", "form", 'section[aria-label]'];
    let node = el.parentElement;
    while (node && node !== document.body) {
      for (const sel of landmarks) {
        if (node.matches && node.matches(sel)) {
          const ariaLabel = node.getAttribute("aria-label");
          if (ariaLabel) return trunc(ariaLabel.trim(), 60);
          return node.tagName.toLowerCase();
        }
      }
      node = node.parentElement;
    }
    return "";
  }

  function getContext(el) {
    const dlgTitle = getDialogTitle(el);
    if (dlgTitle) return dlgTitle;
    const rowCtx = getTableRowContext(el);
    if (rowCtx) return rowCtx;
    // nearest card/section/li/form ancestor's first heading
    const cardSelectors = ["[class*=card]", "[class*=Card]", "section", "li", "form", "article"];
    let node = el.parentElement;
    while (node && node !== document.body) {
      const isCard = cardSelectors.some(sel => { try { return node.matches(sel); } catch (_) { return false; } });
      if (isCard) {
        const hs = node.querySelectorAll("h1,h2,h3,h4,h5,h6,legend,[role=heading]");
        for (const h of hs) {
          if (!isVisible(h)) continue;
          const t = trunc((h.innerText || h.textContent || "").trim(), 60);
          if (t) return t;
        }
      }
      node = node.parentElement;
    }
    return getNearestLandmark(el) || "";
  }

  // ── Neighbor text ─────────────────────────────────────────────────────────────

  function getNeighborText(el) {
    const parent = el.parentElement;
    if (!parent) return "";
    const siblings = Array.from(parent.children);
    const texts = [];
    for (const sib of siblings) {
      if (sib === el) continue;
      const t = (sib.innerText || sib.textContent || "").replace(/\s+/g, " ").trim();
      if (t) texts.push(t);
      if (texts.length >= 3) break;
    }
    if (texts.length < 3 && parent.parentElement) {
      const parentSiblings = Array.from(parent.parentElement.children);
      for (const sib of parentSiblings) {
        if (sib === parent) continue;
        const t = (sib.innerText || sib.textContent || "").replace(/\s+/g, " ").trim();
        if (t) texts.push(t);
        if (texts.length >= 3) break;
      }
    }
    return trunc(texts.slice(0, 3).join(" | "), 80);
  }

  // ── XPath computation ─────────────────────────────────────────────────────────

  function getXPath(el) {
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE) {
      const tag = node.tagName.toLowerCase();
      let idx = 1;
      let sib = node.previousSibling;
      while (sib) {
        if (sib.nodeType === Node.ELEMENT_NODE && sib.tagName.toLowerCase() === tag) idx++;
        sib = sib.previousSibling;
      }
      parts.unshift(`${tag}[${idx}]`);
      node = node.parentNode;
    }
    return "/" + parts.join("/");
  }

  // ── CSS path computation ──────────────────────────────────────────────────────

  function getCSSPath(el) {
    // id-anchored if ancestor has unique id
    let node = el;
    const path = [];
    while (node && node !== document.documentElement) {
      const id = node.getAttribute && node.getAttribute("id");
      if (id && document.querySelectorAll(`#${CSS.escape(id)}`).length === 1) {
        path.unshift(`#${CSS.escape(id)}`);
        return path.join(" > ");
      }
      const tag = node.tagName.toLowerCase();
      const siblings = Array.from(node.parentNode ? node.parentNode.children : []).filter(s => s.tagName === node.tagName);
      if (siblings.length > 1) {
        const idx = siblings.indexOf(node) + 1;
        path.unshift(`${tag}:nth-of-type(${idx})`);
      } else {
        path.unshift(tag);
      }
      node = node.parentNode;
    }
    return path.join(" > ");
  }

  // ── Attributes whitelist ──────────────────────────────────────────────────────

  const ATTR_WHITELIST = new Set([
    "id", "name", "type", "class", "href", "placeholder",
    "aria-label", "title", "alt", "value",
    "data-testid", "data-test", "data-test-id", "data-cy", "data-qa",
    "for", "required", "min", "max", "maxlength", "pattern",
    "disabled", "readonly", "aria-expanded", "aria-selected", "aria-checked",
  ]);

  function getAttrs(el) {
    const result = {};
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    for (const attr of el.attributes) {
      if (!ATTR_WHITELIST.has(attr.name)) continue;
      // Never expose password values
      if (attr.name === "value" && type === "password") continue;
      // For text inputs, only include value if non-empty, max 40 chars
      if (attr.name === "value" && tag === "input" && type !== "submit" && type !== "button" && type !== "reset") {
        const v = (attr.value || "").trim();
        if (!v) continue;
        result[attr.name] = v.slice(0, 40);
        continue;
      }
      result[attr.name] = attr.value;
    }
    // Live value (what the user typed/selected), not the HTML attribute. Passwords are never exposed.
    const isText = (tag === "input" && !["submit", "button", "reset", "checkbox", "radio", "password", "file"].includes(type))
      || tag === "textarea" || tag === "select";
    if (isText) {
      const live = (el.value || "").trim();
      if (live) result["value"] = live.slice(0, 40); else delete result["value"];
    }
    return result;
  }

  // ── Enabled / editable / checked ─────────────────────────────────────────────

  function isEnabled(el) {
    if (el.disabled) return false;
    if (el.getAttribute("aria-disabled") === "true") return false;
    const fieldset = el.closest("fieldset[disabled]");
    if (fieldset) return false;
    return true;
  }

  function isEditable(el) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "textarea") return true;
    if (tag === "input" && !["submit", "button", "reset", "checkbox", "radio"].includes(type)) return true;
    if (el.getAttribute("contenteditable") === "true") return true;
    return false;
  }

  function getChecked(el) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "input" && (type === "checkbox" || type === "radio")) return el.checked;
    const ariaChecked = el.getAttribute("aria-checked");
    if (ariaChecked !== null) return ariaChecked === "true";
    return null;
  }

  function isInDialog(el) {
    return !!(el.closest('dialog[open]') || el.closest('[role="dialog"]'));
  }

  // ── Interactive selector ──────────────────────────────────────────────────────

  const INTERACTIVE_ROLES = new Set([
    "button", "link", "tab", "menuitem", "checkbox", "radio", "switch",
    "option", "combobox", "textbox", "searchbox", "slider", "spinbutton",
  ]);

  function isInteractive(el) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "a" && el.hasAttribute("href")) return true;
    if (tag === "button") return true;
    if (tag === "input" && type !== "hidden") return true;
    if (tag === "select") return true;
    if (tag === "textarea") return true;
    if (tag === "summary") return true;
    if (el.getAttribute("contenteditable") === "true") return true;
    if (el.hasAttribute("onclick")) return true;
    const tabindex = el.getAttribute("tabindex");
    if (tabindex !== null && tabindex !== "-1") return true;
    const role = el.getAttribute("role");
    if (role && INTERACTIVE_ROLES.has(role)) return true;
    return false;
  }

  // ── Collect elements (also shadow roots) ─────────────────────────────────────

  const INTERACTIVE_SELECTORS = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary',
    '[contenteditable=true]', '[onclick]', '[tabindex]:not([tabindex="-1"])',
    '[role=button]', '[role=link]', '[role=tab]', '[role=menuitem]', '[role=checkbox]',
    '[role=radio]', '[role=switch]', '[role=option]', '[role=combobox]',
    '[role=textbox]', '[role=searchbox]', '[role=slider]', '[role=spinbutton]',
  ];

  const ANCHOR_SELECTORS = [
    'h1', 'h2', 'h3', 'h4',
    '[role=alert]', '[role=status]', '[aria-live]',
    'dialog[open]', '[role=dialog]',
  ];

  function collectFromRoot(root, refs, elements) {
    // Interactive
    const interactiveQuery = INTERACTIVE_SELECTORS.join(",");
    let interactiveEls = [];
    try { interactiveEls = Array.from(root.querySelectorAll(interactiveQuery)); } catch (_) {}
    for (const el of interactiveEls) {
      if (!isVisible(el)) {
        // Custom-styled radio/checkbox cards hide the native input; the visible label is the target.
        const proxy = hiddenControlLabel(el);
        if (!proxy) continue;
        proxies.set(el, proxy);
      }
      refs.push(el);
      elements.push({ el, interactive: true });
    }

    // Anchor (context) elements
    let anchorEls = [];
    try { anchorEls = Array.from(root.querySelectorAll(ANCHOR_SELECTORS.join(","))); } catch (_) {}
    for (const el of anchorEls) {
      if (!isVisible(el)) continue;
      // Also include elements with error/invalid/toast/alert classes that have text
      const cls = el.className || "";
      refs.push(el);
      elements.push({ el, interactive: false });
    }

    // Elements with error/toast/alert classes
    try {
      const errorEls = root.querySelectorAll('[class*="error"],[class*="invalid"],[class*="toast"],[class*="alert"]');
      for (const el of errorEls) {
        const t = (el.innerText || el.textContent || "").trim();
        if (!t) continue;
        if (!isVisible(el)) continue;
        refs.push(el);
        elements.push({ el, interactive: false });
      }
    } catch (_) {}

    // Shadow roots
    const allEls = Array.from(root.querySelectorAll("*"));
    for (const el of allEls) {
      if (el.shadowRoot) {
        collectFromRoot(el.shadowRoot, refs, elements);
      }
    }
  }

  function hiddenControlLabel(el) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag !== "input" || (type !== "radio" && type !== "checkbox")) return null;
    let lab = el.closest("label");
    if (!lab && el.id) lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    return lab && isVisible(lab) ? lab : null;
  }

  const proxies = new Map();
  const refs = [];
  const rawElements = [];
  collectFromRoot(document, refs, rawElements);

  // De-duplicate (same element can match multiple selectors)
  const seen = new Set();
  const dedupedElements = [];
  for (const item of rawElements) {
    if (!seen.has(item.el)) {
      seen.add(item.el);
      dedupedElements.push(item);
    }
  }

  // Store references in window.__argus
  window.__argus = window.__argus || {};
  window.__argus.refs = [];
  const refIndexMap = new Map();
  for (const item of dedupedElements) {
    const idx = window.__argus.refs.length;
    window.__argus.refs.push(item.el);
    refIndexMap.set(item.el, idx);
  }

  // ── Build element objects ─────────────────────────────────────────────────────

  const resultElements = [];
  let count = 0;
  for (const { el, interactive } of dedupedElements) {
    if (count >= MAX_ELEMENTS) break;
    count++;

    const idx = refIndexMap.get(el);
    const ref = "e" + idx;
    const tag = el.tagName.toLowerCase();
    const role = getRole(el);
    const name = getAccessibleName(el);
    const text = trunc((el.innerText || el.textContent || "").replace(/\s+/g, " ").trim(), 80);
    const attrs = getAttrs(el);
    const label = getLabelText(el);
    const context = getContext(el);
    const neighbor_text = getNeighborText(el);
    const xpath = getXPath(el);
    const css = getCSSPath(el);
    const proxy = proxies.get(el);
    const bbox = pageRect(proxy || el);
    const visible = proxy ? true : isVisible(el);
    const enabled = isEnabled(el);
    const editable = isEditable(el);
    const checked = getChecked(el);
    const in_dialog = isInDialog(el);

    resultElements.push({
      ref, tag, role, name, text, attrs, label, context, neighbor_text,
      xpath, css, bbox, visible, enabled, editable,
      checked: checked === null ? undefined : checked,
      interactive, in_dialog,
    });
  }

  // ── Page-level data ───────────────────────────────────────────────────────────

  const headingEls = Array.from(document.querySelectorAll("h1,h2,h3")).filter(isVisible).slice(0, 20);
  const headings = headingEls.map(h => trunc((h.innerText || h.textContent || "").trim(), 120));

  const alertEls = Array.from(document.querySelectorAll('[role=alert],[role=status],[aria-live]')).filter(isVisible).slice(0, 10);
  const alerts = alertEls.map(a => trunc((a.innerText || a.textContent || "").replace(/\s+/g, " ").trim(), 120)).filter(Boolean);

  const bodyText = trunc((document.body.innerText || document.body.textContent || "").replace(/\s+/g, " ").trim(), 1500);

  const viewport = { w: window.innerWidth, h: window.innerHeight };

  return {
    url: window.location.href,
    title: document.title || "",
    elements: resultElements,
    headings,
    alerts,
    text_digest: bodyText,
    viewport,
  };
}
