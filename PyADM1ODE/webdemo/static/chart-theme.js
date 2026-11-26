/* 统一图表主题：所有页面的曲线用同一套颜色、单位、刻度与图例规则。
 *
 * 注意：不要给 Plotly 传值为 undefined 的键。之前 xaxis.title 写成
 * `o.xTitle ? {...} : undefined`，Plotly 会在内部对 undefined 做 'line' in 判断，
 * 直接抛 "Cannot use 'in' operator to search for 'line' in undefined"，
 * 整块图白屏。这里统一在最后把 undefined 的键删掉。
 */
(function () {
  const COLOR = {
    gas: "#38e0d0",
    ch4: "#8fe36f",
    ph: "#f2b344",
    vfa: "#7fb2ff",
    olr: "#c58cf0",
    hrt: "#7c8ea8",
    err: "#f2b344",
    muted: "#7c8ea8",
    grid: "#1b2028",
  };

  const SERIES = ["#38e0d0", "#f2b344", "#c58cf0", "#8fe36f", "#ef7070", "#7fb2ff"];
  const FONT = '"Cascadia Mono", Consolas, monospace';

  /** 递归删除 undefined 的键（Plotly 不接受显式 undefined） */
  function clean(obj) {
    if (Array.isArray(obj)) {
      return obj.map(clean).filter((v) => v !== undefined);
    }
    if (obj && typeof obj === "object") {
      const out = {};
      for (const k of Object.keys(obj)) {
        const v = clean(obj[k]);
        if (v !== undefined) out[k] = v;
      }
      return out;
    }
    return obj;
  }

  function tick(v) {
    if (v === null || v === undefined || !isFinite(v)) return "";
    const a = Math.abs(v);
    if (a >= 1000) return Math.round(v).toLocaleString("en-US");
    if (a >= 1) return String(Math.round(v * 100) / 100);
    return String(v);
  }

  function layout(o) {
    o = o || {};
    const out = {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: "#8b95a5", size: 11, family: FONT },
      margin: { l: 60, r: o.y2Title ? 58 : 18, t: o.title ? 48 : 18, b: 38 },
      hovermode: "x unified",
      hoverlabel: {
        bgcolor: "rgba(9,13,19,.94)",
        bordercolor: "rgba(190,215,240,.18)",
        font: { size: 11, family: FONT, color: "#dfe9f5" },
      },
      xaxis: { gridcolor: COLOR.grid, zeroline: false, tickfont: { size: 10 } },
      // 不要用 ",.0f"：pH 7.38 会被四舍五入成 7，VFA/TAC 0.06 变成 0。
      // ",.2~f" 保留两位并去掉多余的零：2,400 / 7.38 / 0.06 都正常。
      yaxis: { gridcolor: COLOR.grid, zeroline: false, tickfont: { size: 10 }, tickformat: ",.2~f" },
      legend: { orientation: "h", y: 1.12, x: 0, font: { size: 10 }, bgcolor: "rgba(0,0,0,0)" },
    };
    if (o.title) {
      out.title = {
        text: o.title,
        x: 0.012,
        xanchor: "left",
        y: 0.97,
        yanchor: "top",
        font: { size: 11.5, color: "#9fb2c8" },
      };
    }
    if (o.xTitle) out.xaxis.title = { text: o.xTitle, font: { size: 10 } };
    if (o.yTitle) out.yaxis.title = { text: o.yTitle, font: { size: 10 } };
    if (o.legendTitle) out.legend.title = { text: o.legendTitle, font: { size: 9 } };
    if (o.y2Title) {
      out.yaxis2 = {
        overlaying: "y",
        side: "right",
        gridcolor: "rgba(0,0,0,0)",
        tickfont: { size: 10 },
        title: { text: o.y2Title, font: { size: 10 } },
      };
    }
    if (o.height) out.height = o.height;
    return clean(out);
  }

  const CONFIG = { displayModeBar: false, responsive: true };

  function line(x, y, o) {
    o = o || {};
    const unit = o.unit ? " " + o.unit : "";
    const trace = {
      x: x,
      y: y,
      type: "scatter",
      mode: o.mode || "lines",
      name: o.name,
      yaxis: o.yaxis,
      line: { color: o.color || COLOR.gas, width: o.width || 2, dash: o.dash || "solid", shape: o.shape },
      hovertemplate: "%{y:,.2f}" + unit + "<extra>" + (o.name || "") + "</extra>",
      connectgaps: false,
    };
    if (o.mode === "markers") trace.marker = { size: o.size || 5, color: o.color };
    return clean(trace);
  }

  function limitLine(x, value, o) {
    o = o || {};
    return clean({
      x: x,
      y: x.map(function () {
        return value;
      }),
      type: "scatter",
      mode: "lines",
      name: o.name || "限值",
      line: { color: o.color || "#ef7070", width: 1, dash: "dot" },
      hovertemplate: (o.name || "限值") + ": " + value + "<extra></extra>",
    });
  }

  window.CHART = { COLOR: COLOR, SERIES: SERIES, layout: layout, config: CONFIG, line: line, limit: limitLine, tick: tick };
})();
