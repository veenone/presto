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
