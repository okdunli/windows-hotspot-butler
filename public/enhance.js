/* ============================================================
   增强脚本（非原版文件）
   由 server.py 注入到 /public/index.html（在原版 app.js 之后执行）：
     1. 设置弹窗底部显示版本号与开源地址
     2. 按窗口宽高计算 --whm-scale，驱动 enhance.css 里的控件缩放
   原版 index.html / app.js 保持 1:1，不改动。
   ============================================================ */
(function () {
  "use strict";

  /* ---- 1. 版本号 + 开源地址 ----
     常显：标题栏品牌名右侧的小徽标 v1.2.0（一眼可见，无需打开设置）。
     设置弹窗底部：完整版本行 + 可点击的开源仓库链接。 ---- */
  function versionText() {
    return (window.__WHM_VERSION || "1.2.0").replace(/^v/, "");
  }
  function repoUrl() {
    return window.__WHM_REPO || "https://github.com/okdunli/windows-hotspot-butler";
  }

  function addTitleVersion() {
    const brand = document.querySelector(".titlebar .brand");
    if (!brand || brand.querySelector(".app-ver")) return;
    const tag = document.createElement("span");
    tag.className = "app-ver";
    tag.textContent = "v" + versionText();
    brand.appendChild(tag);
  }

  function addVersion() {
    const card = document.querySelector("#settingsModal .modal-body");
    if (!card || card.querySelector(".version-row")) return;
    const row = document.createElement("div");
    row.className = "version-row";
    const v = versionText();
    const url = repoUrl();
    // 文本全部用 textContent / 属性赋值，避免把 URL 拼进 innerHTML
    row.appendChild(document.createTextNode("Windows热点管理器 "));
    const vs = document.createElement("span");
    vs.className = "v";
    vs.textContent = "v" + v;
    row.appendChild(vs);
    row.appendChild(document.createTextNode(" · "));
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.className = "repo-link";
    a.textContent = "开源地址";
    row.appendChild(a);
    card.appendChild(row);
  }

  /* ---- 3. 控件整体缩放系数：按窗口宽/高同时缩放 -------------------
     基准 1000×730（宽=原版窗口 1000；高=680+50：标题栏 46 + layout
     padding 32 + 余量，保证最小窗口 880×600 时左栏内容完整显示、无需滚动）。
     scale = min(vw/1000, vh/730)，下限 0.62（再小靠左栏隐藏滚动条兜底），
     上限 1（不放大超过原版）。写入 CSS 变量，enhance.css 里所有尺寸按它 clamp()。 ---- */
  function updateScale() {
    const s = Math.min(
      window.innerWidth / 1000,
      window.innerHeight / 730
    );
    const scale = Math.max(0.62, Math.min(1, s));
    document.documentElement.style.setProperty("--whm-scale", scale.toFixed(3));
  }

  /* ---- 4. 无边框窗口边缘拖拽缩放（Windows / pywebview） -----------------
     WebView2 覆盖整个客户区并吞掉鼠标消息，顶层窗体的系统缩放命中永远收不到，
     因此由前端在窗口边缘 8px 捕获拖拽，走本项目统一的 HTTP /api/op 调
     window.resize()。与 pywebview 内置 easy_drag 同构；捕获阶段必须
     stopImmediatePropagation，否则 easy_drag 的全局 mousedown 会把窗口整体拖走。
     注意：本项目 UI 不用 window.pywebview.api（那是异步注入的，且 app.js
     自己实现了 fetch 桥），所以这里绝不能依赖 js_api —— 早期版本就是
     因此 Windows 上完全不生效。 ---- */
  function edgeResize() {
    const B = 8;  // 边缘热区（逻辑 px）
    let drag = null;
    let inflight = false;
    let pending = null;   // 拖拽中只保留最新位移，避免高频 POST 乱序

    function op(name, args) {
      return fetch("/api/op/" + name, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ args: args || [] })
      });
    }

    function pump() {
      if (inflight || !pending) return;
      inflight = true;
      const a = pending;
      pending = null;
      let p;
      try { p = op("resizeWindow", a); } catch (_) { inflight = false; return; }
      p.catch(function () {}).then(function () {
        inflight = false;
        pump();
      });
    }

    function edgeAt(e) {
      const x = e.clientX, y = e.clientY;
      const w = window.innerWidth, h = window.innerHeight;
      let edge = 0;
      if (x < B) edge |= 1;            // 左
      else if (x >= w - B) edge |= 2;  // 右
      if (y < B) edge |= 4;            // 上
      else if (y >= h - B) edge |= 8;  // 下
      return edge;
    }

    window.addEventListener("mousedown", function (e) {
      const t = e.target;
      if (e.button !== 0 || (t && t.closest && t.closest("button, a, input, select, textarea, .dial"))) return;
      const edge = edgeAt(e);
      if (!edge) return;
      // 拦住 easy_drag（window 冒泡阶段）和页面自身的拖拽/选中
      e.preventDefault();
      e.stopImmediatePropagation();
      drag = { edge, x0: e.screenX, y0: e.screenY };
      try { op("resizeBegin").catch(function () {}); } catch (_) {}
    }, true);  // 捕获阶段：早于 pywebview customize.js 的 window mousedown

    window.addEventListener("mousemove", function (e) {
      if (!drag) return;
      const dx = e.screenX - drag.x0, dy = e.screenY - drag.y0;
      pending = [dx, dy, drag.edge];
      pump();
    }, true);

    function endDrag() { drag = null; pending = null; }
    window.addEventListener("mouseup", endDrag, true);
    window.addEventListener("blur", endDrag);

    // 光标提示（原版 style.css 不动，仅在边缘时临时覆盖）
    window.addEventListener("mousemove", function (e) {
      if (drag) return;
      const edge = edgeAt(e);
      let c = "";
      if (edge === 1 || edge === 2 || edge === 13 || edge === 14) c = "ew-resize";
      else if (edge === 4 || edge === 8 || edge === 7 || edge === 11) c = "ns-resize";
      else if (edge === 5 || edge === 10) c = "nwse-resize";
      else if (edge === 6 || edge === 9) c = "nesw-resize";
      document.body.style.cursor = c;
    });
  }

  function boot() {
    try { addTitleVersion(); } catch (e) {}
    try { addVersion(); } catch (e) {}
    updateScale();
    window.addEventListener("resize", updateScale);
    try { edgeResize(); } catch (e) {}
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
