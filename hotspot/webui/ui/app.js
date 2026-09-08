/* WiFi 热点管理器 · 新界面交互
 * 后端通过 window.pywebview.api 暴露，全部调用返回 Promise。
 * 界面只读内存快照（每 1.2 秒拉一次），任何耗时操作都在 Python 侧的线程池里跑。
 */
(function () {
  "use strict";

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  let state = null;
  let passShown = false;
  let menuMac = null;
  let busy = false;
  let I18N = {};                       // en 翻译表（zh 模式为空表，原文即中文）

  const t = (key, vars) => {
    let s = I18N[key] || key;
    if (vars) for (const k in vars) s = s.split("{" + k + "}").join(vars[k]);
    return s;
  };

  /* 静态文案批量翻译：叶子文本节点 / title / placeholder 按原文查表 */
  function applyI18nStatic() {
    $$("body *").forEach((el) => {
      if (el.children.length === 0 && el.textContent && I18N[el.textContent.trim()]) {
        el.textContent = I18N[el.textContent.trim()];
      }
      if (el.title && I18N[el.title]) el.title = I18N[el.title];
      if (el.placeholder && I18N[el.placeholder]) el.placeholder = I18N[el.placeholder];
    });
  }

  const api = () => window.pywebview.api;

  function setText(el, v) {
    v = v == null ? "" : String(v);
    if (el && el.textContent !== v) el.textContent = v;
  }
  function setClass(el, name, on) {
    if (el) el.classList.toggle(name, !!on);
  }
  function copy(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      toast(t("已复制：{t}", { t: text }), "success");
    } catch (e) {
      toast(t("复制失败：{e}", { e: e }), "error");
    }
  }

  function toast(text, kind) {
    const box = $("#toasts");
    if (!box) return;
    const el = document.createElement("div");
    el.className = "toast " + (kind || "info");
    el.textContent = text;
    box.appendChild(el);
    setTimeout(() => {
      el.classList.add("out");
      setTimeout(() => el.remove(), 300);
    }, 3200);
  }

  const THEME_KEY = "whm-theme";
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    const btn = $("#btnTheme");
    if (btn) btn.textContent = t === "light" ? "☀️" : "🌙";
    try { localStorage.setItem(THEME_KEY, t); } catch (e) { /* 忽略 */ }
      try { window.pywebview && window.pywebview.api.set_theme(t); } catch (e) { /* 忽略 */ }
  }
  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) { /* 忽略 */ }
    applyTheme(saved === "light" ? "light" : "dark");
  }
  function toggleTheme(ev) {
    const cur = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    const next = cur === "light" ? "dark" : "light";
    const root = document.documentElement;
    // 圆形擦除转场：从点击位置扩散新主题色
    const wipe = document.createElement("div");
    wipe.className = "theme-wipe";
    const r = (ev && ev.currentTarget) ? ev.currentTarget.getBoundingClientRect() : null;
    const x = r ? (r.left + r.width / 2) : window.innerWidth / 2;
    const y = r ? (r.top + r.height / 2) : window.innerHeight / 2;
    wipe.style.setProperty("--wx", x + "px");
    wipe.style.setProperty("--wy", y + "px");
    wipe.style.setProperty("--wipe-color", next === "light" ? "#eef1f6" : "#0b0e14");
    document.body.appendChild(wipe);
    setTimeout(() => {
      applyTheme(next);                     // 铺满瞬间切换
      wipe.classList.add("slide-out");      // 上滑渐隐露出新主题
    }, 240);
    setTimeout(() => {
      wipe.remove();
      root.classList.add("theme-anim");     // 兜底平滑残余色差
      setTimeout(() => root.classList.remove("theme-anim"), 300);
    }, 620);
  }

  /* 数值变化时的闪动反馈 */
  function setTextFlash(el, v, flashClass) {
    const s = v == null ? "" : String(v);
    if (!el || el.textContent === s) return;
    el.textContent = s;
    if (flashClass) {
      el.classList.remove(flashClass);
      void el.offsetWidth;                       // 强制 reflow 重启动画
      el.classList.add(flashClass);
    }
  }

  function render(st) {
    state = st;
    busy = !!st.busy;

      const dial = $("#dial");
    const hs = st.hotspot || {};
    setClass(dial, "on", hs.active && !busy);
    setClass(dial, "busy", busy);
    setText($("#dialEmoji"), hs.active ? "📶" : "📡");
    setText($("#dialText"), busy ? t("处理中…") : (hs.active ? t("点击关闭") : t("点击开启")));
    setText($("#dialSub"), hs.active ? (hs.ssid || t("已开启")) : t("未运行"));

      const pill = $("#statusPill");
    pill.className = "pill " + (busy ? "busy" : (hs.active ? "on" : ""));
    pill.innerHTML = hs.active
      ? '<span class="pulse"></span>' + (busy ? t("切换中…") : t("运行中")) + (hs.uptime ? " · " + hs.uptime : "")
      : (busy ? t("切换中…") : t("未开启"));
    setText($("#uptimeText"), hs.max_clients ? t("上限 {n} 台", { n: hs.max_clients }) : "");
    setText($("#backendText"), hs.backend_label || "");

    const remain = (st.auto_stop || {}).remaining || 0;
    const stopEl = $("#autoStopText");
    if (remain > 0) {
      const mm = Math.floor(remain / 60), ss = remain % 60;
      setText(stopEl, t("⏱ {t} 后关闭", { t: "⏱ " + mm + ":" + String(ss).padStart(2, "0") }));
      stopEl.classList.remove("hidden");
    } else {
      stopEl.classList.add("hidden");
    }

      setText($("#ssidText"), hs.ssid || "—");
    setText($("#passText"), passShown ? (hs.passphrase || "") : "••••••••");
    $("#passText").classList.toggle("mono", true);

      const s = st.stats || {};
    setTextFlash($("#statOnline"), s.online || 0, "flash");
    setTextFlash($("#statDown"), s.down_text || "0 B/s", "flash");
    setTextFlash($("#statUp"), s.up_text || "0 B/s", "flash");
    pushSpark(s.down || 0, s.up || 0);
    drawSpark();

      const badge = $("#adminBadge");
    badge.className = "badge " + (st.admin ? "ok" : "warn");
    setText(badge, st.admin ? t("管理员") : t("非管理员"));

      const warn = $("#warnBanner");
    const caps = st.caps || {};
    if (!caps.can_host && !hs.active) {
      setText(warn, caps.block_reason || t("本机未检测到可用的热点能力"));
      warn.classList.remove("hidden");
    } else {
      warn.classList.add("hidden");
    }

      const p = st.portal || {};
    const chip = $("#portalChip");
    chip.classList.toggle("hidden", !p.running);
    chip.classList.toggle("on", !!p.running && !!p.dns);
    setText(chip, p.running ? (p.dns ? t("门户 · 劫持中") : t("门户 · 仅手动")) : t("门户 未启动"));

      renderDevices(st.devices || []);
    const cnt = $("#devCount");
    const n = (st.devices || []).filter((d) => d.online).length;
    if (cnt.textContent !== String(n)) {
      cnt.textContent = n;
      cnt.classList.remove("bump");
      void cnt.offsetWidth;
      cnt.classList.add("bump");
    }

      (st.toasts || []).forEach((t) => toast(t.text, t.kind));
  }

  function createDev(d) {
    const el = document.createElement("div");
    el.className = "dev";
    el.dataset.mac = d.mac;
    el.innerHTML =
      '<div class="dev-emoji"></div>' +
      '<div class="dev-main">' +
      '<div class="dev-name"><span class="nm"></span><span class="tag hidden"></span></div>' +
      '<div class="dev-sub"></div>' +
      '<div class="dev-rate"><span class="down"></span><span class="up"></span></div>' +
      "</div>" +
      '<button class="dev-more" title="' + t("更多") + '">⋯</button>';
    $(".dev-more", el).addEventListener("click", (ev) => {
      ev.stopPropagation();
      openMenu(d.mac, ev.currentTarget);
    });
    return el;
  }

  function updateDev(el, d) {
    setText($(".dev-emoji", el), d.emoji || "📶");
    setText($(".nm", el), d.name || d.mac);

    const parts = [];
    if (d.ip) parts.push(d.ip);
    parts.push(d.mac);
    if (!d.online && d.last_seen_text) parts.push(d.last_seen_text);
    setText($(".dev-sub", el), parts.join(" · "));

    setText($(".dev-rate .down", el), d.online ? "↓ " + (d.rate_down_text || "0 B/s") : "");
    setText($(".dev-rate .up", el), d.online ? "↑ " + (d.rate_up_text || "0 B/s") : "");

    const tag = $(".tag", el);
    if (d.blocked) {
      tag.className = "tag";
      setText(tag, t("已禁止"));
    } else if (!d.online) {
      tag.className = "tag hidden";
    } else if (d.allowed) {
      tag.className = "tag ok";
      setText(tag, t("已放行"));
    } else {
      tag.className = "tag hidden";
    }

    el.classList.toggle("offline", !d.online);
    el.classList.toggle("blocked", !!d.blocked);
  }

  function renderDevices(devices) {
    const box = $("#deviceList");
    $$(".skeleton", box).forEach((el) => el.remove());

    const seen = new Set();
    devices.forEach((d, idx) => {
      seen.add(d.mac);
      let el = box.querySelector('[data-mac="' + (window.CSS && CSS.escape ? CSS.escape(d.mac) : d.mac) + '"]');
      if (!el) {
        el = createDev(d);
        el.style.animationDelay = Math.min(idx * 40, 240) + "ms";
      }
      updateDev(el, d);
      const at = box.children[idx];
      if (at !== el) box.insertBefore(el, at || null);
    });

    Array.from(box.children).forEach((el) => {
      if (!seen.has(el.dataset.mac) && !el.classList.contains("leaving")) {
        el.classList.add("leaving");
        setTimeout(() => el.remove(), 280);
      }
    });

    const empty = $("#emptyState");
    empty.classList.toggle("hidden", devices.length > 0);
  }

  function openMenu(mac, anchor) {
    const menu = $("#devMenu");
    const dev = (state && state.devices || []).find((d) => d.mac === mac);
    if (!dev) return;
    menuMac = mac;
    setText($("#menuTitle"), (dev.name || mac) + (dev.ip ? " · " + dev.ip : ""));
    $("#menuBlock").textContent = dev.blocked ? t("✅ 恢复上网") : t("🚫 禁止上网");
    $("#menuPortal").textContent = dev.allowed ? t("⛔ 取消放行") : t("🌐 放行门户");

    const row = $("#emojiRow");
    row.innerHTML = "";
    (state.icons || []).forEach((ic) => {
      const b = document.createElement("button");
      b.textContent = ic.label.split(" ")[0];
      b.title = ic.label;
      if (ic.key === dev.type) b.style.background = "rgba(88,166,255,.22)";
      b.addEventListener("click", () => {
        api().set_device_type(mac, ic.key).then(() => { closeMenu(); toast(t("已更新设备类型"), "success"); });
      });
      row.appendChild(b);
    });

    menu.classList.remove("hidden");
    const r = anchor.getBoundingClientRect();
    const mw = 216, mh = menu.offsetHeight || 260;
    let left = Math.min(r.right - mw, window.innerWidth - mw - 12);
    let top = r.bottom + 6;
    if (top + mh > window.innerHeight - 12) top = Math.max(12, r.top - mh - 6);
    menu.style.left = Math.max(12, left) + "px";
    menu.style.top = top + "px";
  }

  function closeMenu() {
    const menu = $("#devMenu");
    if (menu.classList.contains("hidden")) return;
    menu.classList.add("closing");
    setTimeout(() => { menu.classList.add("hidden"); menu.classList.remove("closing"); }, 170);
    menuMac = null;
  }

  function openModal(id) { $("#" + id).classList.remove("hidden", "closing"); }
  function closeModal(id) {
    const m = $("#" + id);
    if (m.classList.contains("hidden")) return;
    m.classList.add("closing");
    setTimeout(() => { m.classList.add("hidden"); m.classList.remove("closing"); }, 190);
  }

  let promptResolve = null;
  function promptText(title, value) {
    $("#promptTitle").textContent = title;
    const input = $("#promptInput");
    input.value = value || "";
    openModal("promptModal");
    setTimeout(() => { input.focus(); input.select(); }, 60);
    return new Promise((resolve) => { promptResolve = resolve; });
  }
  function closePrompt(val) {
    closeModal("promptModal");
    if (promptResolve) { promptResolve(val); promptResolve = null; }
  }

  function fillSettings() {
    const c = state.config || {};
    $("#cfgSsid").value = c.ssid || "";
    $("#cfgPass").value = c.passphrase || "";
    $("#cfgBand").value = c.band || "auto";
    $("#cfgBackend").value = c.backend || "auto";
    $("#cfgAutoStart").checked = !!c.auto_start;
    $("#cfgStartWin").checked = !!c.start_with_windows;
    $("#cfgCloseTray").checked = !!c.close_to_tray;
    $("#cfgConfirmExit").checked = c.confirm_exit_hotspot !== false;
    $("#cfgLang").value = state.lang || "zh_CN";
    const tp = state.temp_password || {};
    const hint = $("#tempPwHint");
    if (tp.active) {
      const m = Math.floor((tp.remaining || 0) / 60);
      setText(hint, t("生效中：{pw} · 剩余 {m} 分钟", { pw: tp.password, m: m }));
      hint.classList.remove("hidden");
    } else {
      hint.classList.add("hidden");
    }
  }

  function fillPortal() {
    const c = state.config || {};
    const p = state.portal || {};
    $("#pfEnabled").checked = !!c.portal_enabled;
    $("#pfDns").checked = !!c.portal_dns;
    $("#pfTemplate").value = c.portal_template || "aurora";
    $("#pfTitle").value = c.portal_title || "";
    $("#pfNotice").value = c.portal_notice || "";
    $("#pfButton").value = c.portal_button || "";
    $("#pfPassword").value = c.portal_password || "";
    fillScheduleSelects(c.portal_schedule_start, c.portal_schedule_end);
    let txt = t("状态：{s}", { s: p.running ? t("运行中") : t("未启动") });
    if (p.need_password) txt += " " + t("需访问密码");
    if (p.running) txt += "\n" + t("DNS 劫持：{s}", { s: p.dns ? t("生效中") : t("未生效 — {r}", { r: p.dns_error || t("未知原因") }) });
    if (p.running) txt += "\n" + t("已放行设备：{n} 台　劫持查询：{q} 次", { n: p.allowed || 0, q: p.hijacked || 0 });
    if (p.running && p.schedule_open === false) txt += "\n" + t("不在放行时段（{s}），新设备无法通过门户", { s: p.schedule || "" });
    txt += "\n" + t("门户地址：{u}", { u: p.url || "" });
    setText($("#pfStatus"), txt);
  }

  function fillScheduleSelects(sv, ev) {
    const s = $("#pfSchStart"), e = $("#pfSchEnd");
    if (!s.options.length) {
      for (let h = 0; h <= 23; h++) {
        const o1 = new Option(String(h).padStart(2, "0") + ":00", h);
        const o2 = new Option(String(h).padStart(2, "0") + ":00", h === 23 ? 24 : h + 1);
        s.add(o1); e.add(o2);
      }
      e.add(new Option(t("24:00（全天）"), 24));
    }
    s.value = String(sv == null ? 0 : sv);
    e.value = String(ev == null ? 24 : ev);
  }

  /* ---- 实时网速曲线：环形缓冲 + 纯 canvas 双线（下=蓝 上=紫） ---- */
  const SPARK_N = 90;                       // 90 个采样点 ≈ 最近 2 分钟
  const sparkBuf = { down: [], up: [] };
  function pushSpark(down, up) {
    sparkBuf.down.push(down); sparkBuf.up.push(up);
    if (sparkBuf.down.length > SPARK_N) sparkBuf.down.shift();
    if (sparkBuf.up.length > SPARK_N) sparkBuf.up.shift();
  }
  function drawSpark() {
    const cv = $("#sparkline");
    if (!cv) return;
    const ctx = cv.getContext("2d");
    const W = cv.width, H = cv.height;
    ctx.clearRect(0, 0, W, H);
    const down = getComputedStyle(document.documentElement).getPropertyValue("--accent-2").trim() || "#58A6FF";
    const up = getComputedStyle(document.documentElement).getPropertyValue("--purple").trim() || "#a371f7";
    const grid = getComputedStyle(document.documentElement).getPropertyValue("--border").trim() || "rgba(255,255,255,.08)";
    // 横向网格（3 条）
    ctx.strokeStyle = grid; ctx.lineWidth = 1;
    for (let i = 1; i <= 3; i++) {
      const y = H * i / 4 + 0.5;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }
    const peak = Math.max(1, ...sparkBuf.down, ...sparkBuf.up);
    const draw = (arr, color) => {
      if (arr.length < 2) return;
      ctx.beginPath();
      arr.forEach((v, i) => {
        const x = W - (arr.length - 1 - i) * (W / (SPARK_N - 1));
        const y = H - 4 - (v / peak) * (H - 10);
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.strokeStyle = color; ctx.lineWidth = 1.6;
      ctx.lineJoin = "round"; ctx.lineCap = "round";
      ctx.stroke();
    };
    draw(sparkBuf.down, down);
    draw(sparkBuf.up, up);
  }

  function renderStats(r) {
    if (!r || !r.ok) { toast((r && r.msg) || t("统计读取失败"), "error"); return; }
    setText($("#stTotal"), (r.grand && r.grand.total) || "0 B");
    setText($("#stRx"), (r.grand && r.grand.rx) || "0 B");
    setText($("#stTx"), (r.grand && r.grand.tx) || "0 B");
    const box = $("#statDaily");
    const daily = r.daily || [];
    const maxV = Math.max(1, ...daily.map((d) => d.rx + d.tx));
    box.innerHTML = "";
    daily.forEach((d) => {
      const col = document.createElement("div");
      col.className = "bar-col";
      const total = d.rx + d.tx;
      const val = document.createElement("span");
      val.className = "bar-val";
      val.textContent = total > 0 ? d.total_text : "";
      const track = document.createElement("div");
      track.className = "bar-track";
      const bar = document.createElement("div");
      bar.className = "bar-fill";
      bar.style.height = (total / maxV * 100).toFixed(1) + "%";
      track.appendChild(bar);
      const label = document.createElement("span");
      label.className = "bar-day";
      label.textContent = d.day.slice(5);
      col.appendChild(val); col.appendChild(track); col.appendChild(label);
      box.appendChild(col);
    });
    const top = $("#statTop");
    top.innerHTML = "";
    (r.top || []).forEach((t, i) => {
      const el = document.createElement("div");
      el.className = "top-item";
      el.innerHTML = '<span class="top-rank">' + (i + 1) + "</span>" +
        '<span class="top-name"></span><span class="size"></span>';
      setText($(".top-name", el), t.name);
      setText($(".size", el), t.total_text);
      top.appendChild(el);
    });
    if (!(r.top || []).length) top.innerHTML = '<div class="hint">暂无数据</div>';
    const dom = $("#statDomains");
    dom.innerHTML = "";
    (r.top_domains || []).forEach((d) => {
      const el = document.createElement("div");
      el.className = "top-item";
      el.innerHTML = '<span class="top-name"></span><span class="size"></span>';
      setText($(".top-name", el), d.domain);
      setText($(".size", el), t("{n} 次", { n: d.c }));
      dom.appendChild(el);
    });
    if (!(r.top_domains || []).length) dom.innerHTML = '<div class="hint">' + t("暂无记录（需开启强制门户的 DNS 劫持）") + "</div>";
    const q = $("#statQueries");
    q.innerHTML = "";
    (r.queries || []).slice(0, 40).forEach((it) => {
      const el = document.createElement("div");
      el.className = "query-item";
      el.innerHTML = '<span class="q-time"></span><span class="top-name"></span><span class="size"></span>';
      setText($(".q-time", el), it.time_text || "");
      setText($(".top-name", el), it.domain);
      setText($(".size", el), it.name || it.ip || "");
      q.appendChild(el);
    });
    if (!(r.queries || []).length) q.innerHTML = '<div class="hint">' + t("暂无记录") + "</div>";
  }

  function fillTools() {
    const st = state || {};
    const s = st.share || {};
    setText($("#shareStatus"),
      s.running ? (t("运行中") + " · " + (s.url || "")) : t("未启动"));
    const list = $("#pfList");
    list.innerHTML = "";
    (st.port_fwd || []).forEach((r) => {
      const el = document.createElement("div");
      el.className = "top-item";
      el.innerHTML = '<span class="top-name"></span><span class="size"></span>' +
        '<button class="mini danger-mini" title="' + t("删除") + '">🗑️</button>';
      setText($(".top-name", el), r.name + "：" + r.listen_port + " → " + r.connect_ip + ":" + r.connect_port);
      $("button", el).addEventListener("click", () =>
        api().pf_remove(r.name).then((res) => { toast(res.msg, res.ok ? "success" : "error"); fillTools(); }));
      list.appendChild(el);
    });
    if (!(st.port_fwd || []).length) list.innerHTML = '<div class="hint">暂无转发规则</div>';
  }

  function bind() {
    $("#btnMin").addEventListener("click", () => api().minimize());
    $("#btnClose").addEventListener("click", () => api().close());

    $("#btnTheme").addEventListener("click", toggleTheme);

    // 藏主窗而非 destroy：destroy 会连带 shutdown 杀掉浮窗
    $("#btnMini").addEventListener("click", () => {
      api().open_mini().then(() => api().hide_main());
    });

    $("#btnStats").addEventListener("click", () => {
      openModal("statsModal");
      api().get_stats_report().then(renderStats);
    });

    $("#btnTools").addEventListener("click", () => { fillTools(); openModal("toolsModal"); });
    $("#btnSpeedTest").addEventListener("click", () => {
      toast(t("正在测速…"), "info");
      api().speed_test();
    });
    $("#btnShareStart").addEventListener("click", () =>
      api().share_start().then(() => setTimeout(fillTools, 600)));
    $("#btnShareStop").addEventListener("click", () =>
      api().share_stop().then(() => setTimeout(fillTools, 600)));
    $("#btnShareFolder").addEventListener("click", () => api().share_open_folder());
    $("#btnPfAdd").addEventListener("click", () => {
      const name = $("#pfName").value.trim();
      const lp = parseInt($("#pfLPort").value, 10);
      const ip = $("#pfIp").value.trim();
      const cp = parseInt($("#pfCPort").value, 10);
      if (!lp || !ip || !cp) { toast(t("请填写完整端口转发信息"), "error"); return; }
      api().pf_add(name, lp, ip, cp).then((r) => {
        toast(r.msg, r.ok ? "success" : "error");
        if (r.ok) { $("#pfName").value = $("#pfLPort").value = $("#pfIp").value = $("#pfCPort").value = ""; fillTools(); }
      });
    });

    $("#btnQr").addEventListener("click", () => {
      api().wifi_qrcode().then((r) => {
        if (r.ok) {
          $("#qrImage").src = r.data;
          openModal("qrModal");
        } else {
          toast(r.msg, "error");
        }
      });
    });

    $("#btnAutoStop").addEventListener("click", () => {
      const m = parseInt($("#cfgAutoStop").value, 10) || 0;
      api().schedule_stop(m).then((r) => toast(r.msg, r.ok ? "success" : "error"));
    });
    $("#dial").addEventListener("click", (ev) => {
      if (busy) return;
      const dial = $("#dial");
      const r = dial.getBoundingClientRect();
      const rip = document.createElement("span");
      rip.className = "ripple";
      rip.style.left = (ev.clientX - r.left) + "px";
      rip.style.top = (ev.clientY - r.top) + "px";
      rip.style.width = rip.style.height = "180px";
      dial.appendChild(rip);
      setTimeout(() => rip.remove(), 640);
      api().toggle();
    });

    $("#copySsid").addEventListener("click", () => copy((state.hotspot || {}).ssid || ""));
    $("#copyPass").addEventListener("click", () => copy((state.hotspot || {}).passphrase || ""));
    $("#togglePass").addEventListener("click", () => {
      passShown = !passShown;
      setText($("#passText"), passShown ? ((state.hotspot || {}).passphrase || "") : "••••••••");
    });

    $("#btnRefresh").addEventListener("click", () => api().refresh());
    $("#btnSettings").addEventListener("click", () => { fillSettings(); openModal("settingsModal"); });
    $("#btnPortal").addEventListener("click", () => { fillPortal(); openModal("portalModal"); });
    $("#btnDiag").addEventListener("click", () => {
      setText($("#diagText"), t("正在收集…"));
      openModal("diagModal");
      api().diagnose().then(() => {
        setTimeout(() => api().get_diagnose().then((r) => {
          setText($("#diagText"), (r.lines || []).join("\n") || t("无输出"));
        }), 2500);
      });
    });

    $$("[data-close]").forEach((b) => b.addEventListener("click", () => closeModal(b.dataset.close)));
    $$(".modal").forEach((m) => m.addEventListener("mousedown", (ev) => {
      if (ev.target === m) closeModal(m.id);
    }));
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") { closeMenu(); $$(".modal").forEach((m) => closeModal(m.id)); }
    });
    document.addEventListener("mousedown", (ev) => {
      const menu = $("#devMenu");
      if (!menu.classList.contains("hidden") && !menu.contains(ev.target) && !ev.target.classList.contains("dev-more")) {
        closeMenu();
      }
    });

    $$("#devMenu .menu-item").forEach((b) => b.addEventListener("click", () => {
      const act = b.dataset.act;
      const mac = menuMac;
      closeMenu();
      if (!mac) return;
      if (act === "rename") {
        const dev = (state.devices || []).find((d) => d.mac === mac);
        promptText(t("重命名设备"), (dev && dev.name) || "").then((v) => {
          if (v !== null && v.trim()) api().set_device_name(mac, v.trim()).then((r) => toast(r.msg, r.ok ? "success" : "error"));
        });
      } else if (act === "block") {
        const dev = (state.devices || []).find((d) => d.mac === mac);
        (dev && dev.blocked ? api().unblock_device(mac) : api().block_device(mac))
          .then((r) => toast(r.msg, r.ok ? "success" : "error"));
      } else if (act === "portal") {
        const dev = (state.devices || []).find((d) => d.mac === mac);
        (dev && dev.allowed ? api().portal_revoke(mac) : api().portal_allow(mac))
          .then((r) => toast(r.msg, r.ok ? "success" : "error"));
      } else if (act === "forget") {
        api().forget_device(mac).then((r) => toast(r.msg, r.ok ? "success" : "error"));
      }
    }));

    $("#promptOk").addEventListener("click", () => closePrompt($("#promptInput").value));
    $("#promptCancel").addEventListener("click", () => closePrompt(null));
    $("#promptInput").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") closePrompt(ev.target.value);
    });

    window.__confirmExit = () => openModal("exitModal");
    $("#btnExitCancel").addEventListener("click", () => {
      closeModal("exitModal");
      api().cancel_exit();
    });
    $("#btnExitOk").addEventListener("click", () => {
      closeModal("exitModal");
      api().confirm_exit().then(() => api().close());
    });

    $("#btnTempPw").addEventListener("click", () => {
      const h = parseFloat($("#cfgTempHours").value) || 1;
      api().temp_password_start(h).then((r) => {
        toast(r.msg, r.ok ? "success" : "error");
        if (r.ok) { setText($("#tempPwHint"), t("生效中：{pw}", { pw: r.password })); $("#tempPwHint").classList.remove("hidden"); }
      });
    });
    $("#btnTempStop").addEventListener("click", () =>
      api().temp_password_stop().then((r) => {
        toast(r.msg, "success");
        $("#tempPwHint").classList.add("hidden");
      }));

    $("#btnExportCsv").addEventListener("click", () =>
      api().export_csv().then((r) => toast(r.msg, r.ok ? "success" : "error")));

    $("#btnExport").addEventListener("click", () => api().export_config().then((r) => toast(r.msg, r.ok ? "success" : "error")));
    $("#btnImport").addEventListener("click", () => api().import_config().then((r) => toast(r.msg, r.ok ? "success" : "error")));

    // 语言切换：立即生效（后端持久化 + 前端重载翻译），不必点保存
    $("#cfgLang").addEventListener("change", () => {
      api().set_language($("#cfgLang").value).then((r) => {
        if (r.ok) window.location.reload();
      });
    });

    $("#btnSave").addEventListener("click", () => {
      api().save_config({
        ssid: $("#cfgSsid").value,
        passphrase: $("#cfgPass").value,
        band: $("#cfgBand").value,
        backend: $("#cfgBackend").value,
        auto_start: $("#cfgAutoStart").checked,
        start_with_windows: $("#cfgStartWin").checked,
        close_to_tray: $("#cfgCloseTray").checked,
        confirm_exit_hotspot: $("#cfgConfirmExit").checked,
        hotkey_enabled: $("#cfgHotkey").checked,
      }).then((r) => { toast(r.ok ? t("设置已保存") : r.msg, r.ok ? "success" : "error"); if (r.ok) closeModal("settingsModal"); });
    });
    $("#btnApply").addEventListener("click", () => {
      api().apply_now().then(() => toast(t("正在下发配置…"), "info"));
    });

    const portalPatch = () => ({
      portal_enabled: $("#pfEnabled").checked,
      portal_dns: $("#pfDns").checked,
      portal_template: $("#pfTemplate").value,
      portal_title: $("#pfTitle").value,
      portal_notice: $("#pfNotice").value,
      portal_button: $("#pfButton").value,
      portal_password: $("#pfPassword").value.trim(),
      portal_schedule_start: parseInt($("#pfSchStart").value, 10) || 0,
      portal_schedule_end: parseInt($("#pfSchEnd").value, 10) || 24,
    });
    $("#btnPfSave").addEventListener("click", () => {
      api().save_config(portalPatch()).then((r) => toast(r.ok ? t("已保存") : r.msg, r.ok ? "success" : "error"));
    });
    $("#btnPfStart").addEventListener("click", () => {
      api().save_config(portalPatch()).then(() => api().portal_start().then(() => toast(t("正在启动门户…"), "info")));
    });
    $("#btnPfStop").addEventListener("click", () => {
      api().portal_stop().then(() => toast(t("门户已停止"), "info"));
    });
  }

  async function tick() {
    try {
      const st = await api().get_state();
      render(st);
    } catch (e) {
      console.warn("get_state failed", e);
    }
    setTimeout(tick, 1200);
  }

  async function boot() {
    initTheme();
    bind();
    // 加载翻译表（en 模式非空）并翻译 index.html 里的静态文案
    try {
      const [dic, st] = await Promise.all([api().get_i18n(), api().get_state()]);
      I18N = dic || {};
      if (Object.keys(I18N).length) applyI18nStatic();
      state = st;
      render(st);
      tick();
    } catch (e) {
      tick();   // 字典加载失败不阻塞主循环
    }
    try { window.pywebview.api.boot_ready(); } catch (e) {}   // 上报就绪：后端据此恢复浮窗
  }

  if (window.pywebview && window.pywebview.api) boot();
  else window.addEventListener("pywebviewready", boot);
})();
