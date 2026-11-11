/* PRESTO v4 client glue: theme, drawer, toasts, htmx error handling.
   Loaded before Alpine (all scripts are deferred, in document order). */
(function () {
  "use strict";

  function storageGet(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function storageSet(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }
  function csrfToken() {
    try { return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRFToken"] || ""; }
    catch (e) { return ""; }
  }
  var MOBILE = window.matchMedia("(max-width: 1023px)");

  document.addEventListener("alpine:init", function () {
    var Alpine = window.Alpine;

    Alpine.store("theme", {
      value: document.documentElement.dataset.theme || "dark",
      toggle: function () { this.set(this.value === "dark" ? "light" : "dark"); },
      set: function (theme) {
        this.value = theme;
        document.documentElement.dataset.theme = theme;
        storageSet("presto-theme", theme);
        if (document.body.dataset.auth === "1") {
          fetch("/me/theme", {
            method: "POST", credentials: "same-origin",
            headers: { "X-CSRFToken": csrfToken(), "Content-Type": "application/x-www-form-urlencoded" },
            body: "theme=" + encodeURIComponent(theme),
          }).catch(function () { /* the local copy still applies */ });
        }
        window.dispatchEvent(new CustomEvent("theme-changed", { detail: theme }));
      },
    });

    Alpine.store("toasts", {
      items: [],
      push: function (message, level, ms) {
        var id = Date.now() + Math.random();
        this.items.push({ id: id, message: String(message || ""), level: level || "info" });
        var self = this;
        setTimeout(function () { self.dismiss(id); }, ms || (level === "error" ? 8000 : 4000));
      },
      dismiss: function (id) { this.items = this.items.filter(function (t) { return t.id !== id; }); },
    });

    Alpine.data("drawer", function () {
      return {
        open: MOBILE.matches ? false : storageGet("presto-drawer") !== "closed",
        mobile: MOBILE.matches,
        init: function () {
          var self = this;
          MOBILE.addEventListener("change", function (e) { self.mobile = e.matches; if (e.matches) self.open = false; });
        },
        toggle: function () {
          this.open = !this.open;
          if (!this.mobile) storageSet("presto-drawer", this.open ? "open" : "closed");
        },
      };
    });

    Alpine.data("navSection", function (key) {
      return {
        open: storageGet("presto-nav-" + key) !== "closed",
        toggle: function () {
          this.open = !this.open;
          storageSet("presto-nav-" + key, this.open ? "open" : "closed");
        },
      };
    });

    Alpine.data("passwordField", function () {
      return { shown: false };
    });

    // Django messages rendered into the page become toasts.
    var initial = document.getElementById("initial-messages");
    if (initial) {
      try {
        JSON.parse(initial.textContent).forEach(function (m) { Alpine.store("toasts").push(m.message, m.level); });
      } catch (e) { /* ignore malformed */ }
    }
  });

  // htmx: swap 422 responses (forms re-rendered with errors); other 4xx/5xx are errors.
  if (window.htmx) {
    window.htmx.config.responseHandling = [
      { code: "204", swap: false },
      { code: "[23]..", swap: true },
      { code: "422", swap: true },
      { code: "[45]..", swap: false, error: true },
      { code: "...", swap: false },
    ];
  }

  // Boosted navigation (sidebar links) to an error page shows that page, like a normal link would;
  // other htmx requests keep the default (no swap, error toast).
  document.addEventListener("htmx:beforeSwap", function (e) {
    var cfg = e.detail.requestConfig || {};
    if (cfg.boosted && e.detail.xhr.status >= 400 && !e.detail.xhr.getResponseHeader("HX-Redirect")) {
      e.detail.shouldSwap = true;
      e.detail.isError = false;
    }
  });

  // Keep GET URLs tidy (they are pushed to the address bar): drop empty and default list params.
  var LIST_DEFAULTS = { seg: "all", dir: "asc", page: "1" };
  document.addEventListener("htmx:configRequest", function (e) {
    if (e.detail.verb !== "get") return;
    var params = e.detail.parameters;
    Object.keys(params).forEach(function (k) {
      if (params[k] === "" || LIST_DEFAULTS[k] === params[k]) delete params[k];
    });
  });

  // Modal host (#modal): close on HX-Trigger "close-modal", Escape, backdrop or [data-close-modal].
  function closeModal() {
    var host = document.getElementById("modal");
    if (host && host.firstElementChild) {
      host.innerHTML = "";
      if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
    }
  }
  var lastFocus = null;
  // Deferred: htmx fires the remaining HX-Trigger events (e.g. "toast") on the form that
  // made the request, so it must still be attached when they bubble.
  document.addEventListener("close-modal", function () { setTimeout(closeModal, 0); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeModal(); });
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-close-modal]") || e.target.matches("[data-modal]")) closeModal();
  });
  document.addEventListener("htmx:beforeRequest", function (e) {
    if (e.detail.target && e.detail.target.id === "modal" && !document.getElementById("modal").firstElementChild) {
      lastFocus = document.activeElement;
    }
  });
  document.addEventListener("htmx:afterSwap", function (e) {
    if (e.detail.target.id !== "modal") return;
    var first = e.detail.target.querySelector("[aria-invalid=true], .modal input:not([type=hidden]), .modal select, .modal textarea, .modal button[type=submit]");
    if (first) first.focus();
  });

  // HX-Trigger: {"toast": {"message": "...", "level": "success"}}
  document.addEventListener("toast", function (e) {
    var d = e.detail || {};
    if (window.Alpine) window.Alpine.store("toasts").push(d.message || d.value, d.level);
  });

  // Failed htmx requests: show the server's detail like the v3 error banner.
  function errorText(xhr) {
    var text = xhr.responseText || "";
    try {
      var body = JSON.parse(text);
      if (typeof body.detail === "string") return body.detail;
      if (Array.isArray(body.detail)) {
        return body.detail.map(function (d) {
          var where = (d.loc || []).filter(function (p) { return p !== "body"; }).join(".");
          return (where ? where + ": " : "") + (d.msg || "");
        }).join("\n");
      }
    } catch (e) { /* not JSON */ }
    return xhr.status ? "Request failed (HTTP " + xhr.status + ")" : "The server did not respond.";
  }
  document.addEventListener("htmx:responseError", function (e) {
    var xhr = e.detail.xhr;
    if (xhr.getResponseHeader("HX-Redirect")) return; // session expired: htmx navigates to login
    if (window.Alpine) window.Alpine.store("toasts").push(errorText(xhr), "error");
  });
  document.addEventListener("htmx:sendError", function () {
    if (window.Alpine) window.Alpine.store("toasts").push("The server did not respond. Check your connection.", "error");
  });
})();
