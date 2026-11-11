/* Dashboard charts (ECharts 5.6.0, vendored).

   Loaded from the dashboard page body, so it runs on a full page load and again
   after every boosted (hx-boost) navigation to the dashboard. The chart root
   carries x-ignore until both this file and Alpine are ready; activate() then
   removes it and starts the "dashboardCharts" component on it. ECharts may load
   after this file on boosted navigation (htmx inserts scripts asynchronously),
   so the component waits for window.echarts before drawing. */
(function () {
  "use strict";

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }

  function palette() {
    var dark = document.documentElement.dataset.theme !== "light";
    return {
      text: cssVar("--fg", dark ? "#FFFFFF" : "#212121"),
      muted: dark ? "rgba(255,255,255,0.62)" : "rgba(0,0,0,0.6)",
      line: dark ? "rgba(255,255,255,0.14)" : "rgba(0,0,0,0.12)",
      tooltipBg: cssVar("--surface", dark ? "#1D1D1D" : "#FFFFFF"),
      inactive: dark ? "rgba(255,255,255,0.25)" : "rgba(0,0,0,0.25)",
    };
  }

  function donutOption(chart, c) {
    return {
      animationDuration: 400,
      tooltip: {
        trigger: "item", formatter: "{b}: {c} ({d}%)",
        backgroundColor: c.tooltipBg, borderColor: c.line, textStyle: { color: c.text, fontSize: 12 },
      },
      legend: {
        type: "scroll", orient: "horizontal", bottom: 4, left: "center", padding: [4, 24],
        textStyle: { color: c.text, fontSize: 11 },
        pageIconColor: c.text, pageIconInactiveColor: c.inactive,
        pageTextStyle: { color: c.muted, fontSize: 10 },
        itemGap: 12, itemWidth: 12, itemHeight: 8,
      },
      series: [{
        name: chart.name, type: "pie", radius: ["38%", "62%"], center: ["50%", "44%"],
        avoidLabelOverlap: true,
        label: { show: true, formatter: "{b}\n{c}", fontSize: 10, color: c.text },
        labelLine: { lineStyle: { color: c.muted } },
        emphasis: { label: { show: true, fontSize: 13, fontWeight: "bold" } },
        data: chart.data.map(function (d) {
          return { name: d.name, value: d.value, itemStyle: { color: d.color } };
        }),
      }],
    };
  }

  function barOption(chart, c) {
    return {
      animationDuration: 400,
      tooltip: {
        trigger: "axis", axisPointer: { type: "shadow" },
        backgroundColor: c.tooltipBg, borderColor: c.line, textStyle: { color: c.text, fontSize: 12 },
      },
      grid: { left: "3%", right: "8%", bottom: "3%", top: "6%", containLabel: true },
      xAxis: {
        type: "value", minInterval: 1,
        axisLabel: { color: c.muted, fontSize: 10 },
        splitLine: { lineStyle: { color: c.line } },
      },
      yAxis: {
        type: "category", inverse: true,
        data: chart.data.map(function (d) { return d.name; }),
        axisLabel: { color: c.text, fontSize: 10 },
        axisLine: { lineStyle: { color: c.muted } },
      },
      series: [{
        name: chart.name, type: "bar", barMaxWidth: 28,
        data: chart.data.map(function (d) { return { value: d.value, itemStyle: { color: d.color } }; }),
        label: { show: true, position: "right", color: c.text, fontSize: 10 },
      }],
    };
  }

  function optionFor(chart) {
    var c = palette();
    return chart.kind === "bar" ? barOption(chart, c) : donutOption(chart, c);
  }

  function readPayload() {
    var el = document.getElementById("dash-chart-data");
    if (!el) return [];
    try { return JSON.parse(el.textContent) || []; } catch (e) { return []; }
  }

  function whenECharts(cb, isAlive) {
    var tries = 0;
    (function poll() {
      if (!isAlive()) return;
      if (window.echarts) { cb(window.echarts); return; }
      if (++tries > 300) return; // ~15 s: give up quietly, the cards keep their aria labels
      setTimeout(poll, 50);
    })();
  }

  function component() {
    // Chart instances and listeners live in this closure, not on the Alpine data object:
    // Alpine wraps its data in reactive proxies, and ECharts breaks when called through one.
    var instances = [];
    var alive = true;
    var onResize = null, onTheme = null, observer = null;

    function resize() {
      instances.forEach(function (x) { if (!x.inst.isDisposed()) x.inst.resize(); });
    }
    function retheme() {
      // The theme store sets data-theme before dispatching, so CSS variables are current.
      instances.forEach(function (x) { if (!x.inst.isDisposed()) x.inst.setOption(optionFor(x.chart), true); });
    }
    function draw(root, echarts, charts) {
      charts.forEach(function (chart) {
        var el = root.querySelector('[data-chart="' + chart.id + '"]');
        if (!el) return;
        // A history-restored snapshot may hold a stale canvas from the previous visit.
        var old = echarts.getInstanceByDom(el);
        if (old) old.dispose();
        el.innerHTML = "";
        el.removeAttribute("_echarts_instance_");
        var inst = echarts.init(el, null, { renderer: "canvas" });
        inst.setOption(optionFor(chart));
        if (chart.nav) inst.on("click", function () { window.location.assign(chart.nav); });
        instances.push({ chart: chart, inst: inst });
      });
      onResize = resize;
      onTheme = retheme;
      window.addEventListener("resize", onResize);
      window.addEventListener("theme-changed", onTheme);
      if (window.ResizeObserver) {
        // The drawer toggle changes the content width without a window resize.
        observer = new ResizeObserver(resize);
        observer.observe(root);
      }
    }

    return {
      init: function () {
        var root = this.$el;
        var charts = readPayload();
        if (!charts.length) return;
        whenECharts(function (echarts) { draw(root, echarts, charts); }, function () { return alive; });
      },
      destroy: function () {
        alive = false;
        if (onResize) window.removeEventListener("resize", onResize);
        if (onTheme) window.removeEventListener("theme-changed", onTheme);
        if (observer) observer.disconnect();
        instances.forEach(function (x) { if (!x.inst.isDisposed()) x.inst.dispose(); });
        instances = [];
      },
    };
  }

  function register() {
    if (window.__prestoDashRegistered) return;
    window.Alpine.data("dashboardCharts", component);
    window.__prestoDashRegistered = true;
  }

  function activate() {
    if (!window.Alpine || !window.__prestoDashRegistered) return;
    document.querySelectorAll("[data-dash-charts][x-ignore]").forEach(function (el) {
      el.removeAttribute("x-ignore");
      // Alpine remembers a skipped x-ignore subtree on the element itself.
      delete el._x_ignore;
      delete el._x_ignoreSelf;
      window.Alpine.initTree(el);
    });
  }

  if (window.Alpine) {
    // Alpine already runs (boosted navigation, or this file ran after Alpine started).
    register();
    activate();
  } else {
    document.addEventListener("alpine:init", register);
    document.addEventListener("alpine:initialized", activate);
  }

  if (!window.__prestoDashListeners) {
    window.__prestoDashListeners = true;
    // Covers swaps where the page scripts are not run again (history restore).
    document.addEventListener("htmx:afterSettle", activate);
    document.addEventListener("htmx:historyRestore", activate);
  }
})();
