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
      toast("已复制：" + text, "success");
    } catch (e) {
      toast("复制失败：" + e, "error");
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
    // 同步到后端配置，迷你悬浮窗等其它窗口跟随
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

    // --- 圆盘 ---
    const dial = $("#dial");
    const hs = st.hotspot || {};
    setClass(dial, "on", hs.active && !busy);
    setClass(dial, "busy", busy);
    setText($("#dialEmoji"), hs.active ? "📶" : "📡");
    setText($("#dialText"), busy ? "处理中…" : (hs.active ? "点击关闭" : "点击开启"));
    setText($("#dialSub"), hs.active ? (hs.ssid || "已开启") : "未运行");

    // --- 状态行 ---
    const pill = $("#statusPill");
    pill.className = "pill " + (busy ? "busy" : (hs.active ? "on" : ""));
    pill.innerHTML = hs.active
      ? '<span class="pulse"></span>' + (busy ? "切换中…" : "运行中") + (hs.uptime ? " · " + hs.uptime : "")
      : (busy ? "切换中…" : "未开启");
    setText($("#uptimeText"), hs.max_clients ? "上限 " + hs.max_clients + " 台" : "");
    setText($("#backendText"), hs.backend_label || "");

    // 定时关闭倒计时（显示在状态行）
    const remain = (st.auto_stop || {}).remaining || 0;
    const stopEl = $("#autoStopText");
    if (remain > 0) {
      const mm = Math.floor(remain / 60), ss = remain % 60;
      setText(stopEl, "⏱ " + mm + ":" + String(ss).padStart(2, "0") + " 后关闭");
      stopEl.classList.remove("hidden");
    } else {
      stopEl.classList.add("hidden");
    }

    // --- 凭据 ---
    setText($("#ssidText"), hs.ssid || "—");
    setText($("#passText"), passShown ? (hs.passphrase || "") : "••••••••");
    $("#passText").classList.toggle("mono", true);

    // --- 统计 ---
    const s = st.stats || {};
    setTextFlash($("#statOnline"), s.online || 0, "flash");
    setTextFlash($("#statDown"), s.down_text || "0 B/s", "flash");
    setTextFlash($("#statUp"), s.up_text || "0 B/s", "flash");

    // --- 管理员 ---
    const badge = $("#adminBadge");
    badge.className = "badge " + (st.admin ? "ok" : "warn");
    setText(badge, st.admin ? "管理员" : "非管理员");

    // --- 硬件能力（只在真的开不了时提示）---
    const warn = $("#warnBanner");
    const caps = st.caps || {};
    if (!caps.can_host && !hs.active) {
      setText(warn, caps.block_reason || "本机未检测到可用的热点能力");
      warn.classList.remove("hidden");
    } else {
      warn.classList.add("hidden");
    }

    // --- 门户 ---
    const p = st.portal || {};
    const chip = $("#portalChip");
    chip.classList.toggle("hidden", !p.running);
    chip.classList.toggle("on", !!p.running && !!p.dns);
    setText(chip, p.running ? (p.dns ? "门户 · 劫持中" : "门户 · 仅手动") : "门户 未启动");

    // --- 设备 ---
    renderDevices(st.devices || []);
    const cnt = $("#devCount");
    const n = (st.devices || []).filter((d) => d.online).length;
    if (cnt.textContent !== String(n)) {
      cnt.textContent = n;
      cnt.classList.remove("bump");
      void cnt.offsetWidth;
      cnt.classList.add("bump");
    }

    // --- toast（后端产生）---
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
      '<button class="dev-more" title="更多">⋯</button>';
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
      setText(tag, "已禁止");
    } else if (!d.online) {
      tag.className = "tag hidden";
    } else if (d.allowed) {
      tag.className = "tag ok";
      setText(tag, "已放行");
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
    $("#menuBlock").textContent = dev.blocked ? "✅ 恢复上网" : "🚫 禁止上网";
    $("#menuPortal").textContent = dev.allowed ? "⛔ 取消放行" : "🌐 放行门户";

    // emoji 选择行
    const row = $("#emojiRow");
    row.innerHTML = "";
    (state.icons || []).forEach((ic) => {
      const b = document.createElement("button");
      b.textContent = ic.label.split(" ")[0];
      b.title = ic.label;
      if (ic.key === dev.type) b.style.background = "rgba(88,166,255,.22)";
      b.addEventListener("click", () => {
        api().set_device_type(mac, ic.key).then(() => { closeMenu(); toast("已更新设备类型", "success"); });
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
    // 临时密码状态
    const tp = state.temp_password || {};
    const hint = $("#tempPwHint");
    if (tp.active) {
      const m = Math.floor((tp.remaining || 0) / 60);
      setText(hint, "生效中：" + tp.password + " · 剩余 " + m + " 分钟");
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
    let txt = "状态：" + (p.running ? "运行中" : "未启动");
    if (p.need_password) txt += " · 需访问密码";
    if (p.running) txt += "\nDNS 劫持：" + (p.dns ? "生效中" : "未生效 — " + (p.dns_error || "未知原因"));
    if (p.running) txt += "\n已放行设备：" + (p.allowed || 0) + " 台　劫持查询：" + (p.hijacked || 0) + " 次";
    if (p.running && p.schedule_open === false) txt += "\n不在放行时段（" + (p.schedule || "") + "），新设备无法通过门户";
    txt += "\n门户地址：" + (p.url || "");
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
      e.add(new Option("24:00（全天）", 24));
    }
    s.value = String(sv == null ? 0 : sv);
    e.value = String(ev == null ? 24 : ev);
  }

  function renderStats(r) {
    if (!r || !r.ok) { toast((r && r.msg) || "统计读取失败", "error"); return; }
    setText($("#stTotal"), (r.grand && r.grand.total) || "0 B");
    setText($("#stRx"), (r.grand && r.grand.rx) || "0 B");
    setText($("#stTx"), (r.grand && r.grand.tx) || "0 B");
    // 每日流量条形图（纯 CSS，无依赖）
    const box = $("#statDaily");
    const daily = r.daily || [];
    const maxV = Math.max(1, ...daily.map((d) => d.rx + d.tx));
    box.innerHTML = "";
    daily.forEach((d) => {
      const row = document.createElement("div");
      row.className = "bar-row";
      const total = d.rx + d.tx;
      const label = document.createElement("span");
      label.className = "bar-day";
      label.textContent = d.day.slice(5);
      const track = document.createElement("div");
      track.className = "bar-track";
      const bar = document.createElement("div");
      bar.className = "bar-fill";
      bar.style.width = (total / maxV * 100).toFixed(1) + "%";
      track.appendChild(bar);
      const val = document.createElement("span");
      val.className = "bar-val";
      val.textContent = total > 0 ? d.total_text : "";
      row.appendChild(label); row.appendChild(track); row.appendChild(val);
      box.appendChild(row);
    });
    // TOP 设备
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
    // 域名排行
    const dom = $("#statDomains");
    dom.innerHTML = "";
    (r.top_domains || []).forEach((d) => {
      const el = document.createElement("div");
      el.className = "top-item";
      el.innerHTML = '<span class="top-name"></span><span class="size"></span>';
      setText($(".top-name", el), d.domain);
      setText($(".size", el), d.c + " 次");
      dom.appendChild(el);
    });
    if (!(r.top_domains || []).length) dom.innerHTML = '<div class="hint">暂无记录（需开启强制门户的 DNS 劫持）</div>';
    // 最近访问
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
    if (!(r.queries || []).length) q.innerHTML = '<div class="hint">暂无记录</div>';
  }

  function fillTools() {
    const st = state || {};
    const s = st.share || {};
    setText($("#shareStatus"),
      s.running ? ("运行中 · " + (s.url || "")) : "未启动");
    // 端口转发列表
    const list = $("#pfList");
    list.innerHTML = "";
    (st.port_fwd || []).forEach((r) => {
      const el = document.createElement("div");
      el.className = "top-item";
      el.innerHTML = '<span class="top-name"></span><span class="size"></span>' +
        '<button class="mini danger-mini" title="删除">🗑️</button>';
      setText($(".top-name", el), r.name + "：" + r.listen_port + " → " + r.connect_ip + ":" + r.connect_port);
      $("button", el).addEventListener("click", () =>
        api().pf_remove(r.name).then((res) => { toast(res.msg, res.ok ? "success" : "error"); fillTools(); }));
      list.appendChild(el);
    });
    if (!(st.port_fwd || []).length) list.innerHTML = '<div class="hint">暂无转发规则</div>';
  }

  function bind() {
    // 窗口
    $("#btnMin").addEventListener("click", () => api().minimize());
    $("#btnClose").addEventListener("click", () => api().close());

    // 主题
    $("#btnTheme").addEventListener("click", toggleTheme);

    // 迷你悬浮窗：打开浮窗后把主窗口藏起来（绝不能 destroy——
    // destroy 会触发后端 shutdown + 托盘退出，连带杀掉刚出生的浮窗=闪退）。
    // "展开"按钮走 mini_restore_main() → show() 主窗口，链路闭环。
    $("#btnMini").addEventListener("click", () => {
      api().open_mini().then(() => api().hide_main());
    });

    // 统计
    $("#btnStats").addEventListener("click", () => {
      openModal("statsModal");
      api().get_stats_report().then(renderStats);
    });

    // 工具箱
    $("#btnTools").addEventListener("click", () => { fillTools(); openModal("toolsModal"); });
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
      if (!lp || !ip || !cp) { toast("请填写完整端口转发信息", "error"); return; }
      api().pf_add(name, lp, ip, cp).then((r) => {
        toast(r.msg, r.ok ? "success" : "error");
        if (r.ok) { $("#pfName").value = $("#pfLPort").value = $("#pfIp").value = $("#pfCPort").value = ""; fillTools(); }
      });
    });

    // WiFi 二维码
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

    // 定时关闭
    $("#btnAutoStop").addEventListener("click", () => {
      const m = parseInt($("#cfgAutoStop").value, 10) || 0;
      api().schedule_stop(m).then((r) => toast(r.msg, r.ok ? "success" : "error"));
    });
    // 圆盘
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

    // 凭据
    $("#copySsid").addEventListener("click", () => copy((state.hotspot || {}).ssid || ""));
    $("#copyPass").addEventListener("click", () => copy((state.hotspot || {}).passphrase || ""));
    $("#togglePass").addEventListener("click", () => {
      passShown = !passShown;
      setText($("#passText"), passShown ? ((state.hotspot || {}).passphrase || "") : "••••••••");
    });

    // 顶栏 / 底部按钮
    $("#btnRefresh").addEventListener("click", () => api().refresh());
    $("#btnSettings").addEventListener("click", () => { fillSettings(); openModal("settingsModal"); });
    $("#btnPortal").addEventListener("click", () => { fillPortal(); openModal("portalModal"); });
    $("#btnDiag").addEventListener("click", () => {
      setText($("#diagText"), "正在收集…");
      openModal("diagModal");
      api().diagnose().then(() => {
        setTimeout(() => api().get_diagnose().then((r) => {
          setText($("#diagText"), (r.lines || []).join("\n") || "无输出");
        }), 2500);
      });
    });

    // 模态关闭
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

    // 菜单动作
    $$("#devMenu .menu-item").forEach((b) => b.addEventListener("click", () => {
      const act = b.dataset.act;
      const mac = menuMac;
      closeMenu();
      if (!mac) return;
      if (act === "rename") {
        const dev = (state.devices || []).find((d) => d.mac === mac);
        promptText("重命名设备", (dev && dev.name) || "").then((v) => {
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

    // 输入弹窗
    $("#promptOk").addEventListener("click", () => closePrompt($("#promptInput").value));
    $("#promptCancel").addEventListener("click", () => closePrompt(null));
    $("#promptInput").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") closePrompt(ev.target.value);
    });

    // 退出确认（热点运行中点 ✕ → 后端触发 __confirmExit）
    window.__confirmExit = () => openModal("exitModal");
    $("#btnExitCancel").addEventListener("click", () => {
      closeModal("exitModal");
      api().cancel_exit();
    });
    $("#btnExitOk").addEventListener("click", () => {
      closeModal("exitModal");
      api().confirm_exit().then(() => api().close());
    });

    // 临时密码
    $("#btnTempPw").addEventListener("click", () => {
      const h = parseFloat($("#cfgTempHours").value) || 1;
      api().temp_password_start(h).then((r) => {
        toast(r.msg, r.ok ? "success" : "error");
        if (r.ok) { setText($("#tempPwHint"), "生效中：" + r.password); $("#tempPwHint").classList.remove("hidden"); }
      });
    });
    $("#btnTempStop").addEventListener("click", () =>
      api().temp_password_stop().then((r) => {
        toast(r.msg, "success");
        $("#tempPwHint").classList.add("hidden");
      }));

    // 配置备份
    $("#btnExport").addEventListener("click", () => api().export_config().then((r) => toast(r.msg, r.ok ? "success" : "error")));
    $("#btnImport").addEventListener("click", () => api().import_config().then((r) => toast(r.msg, r.ok ? "success" : "error")));

    // 设置保存
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
      }).then((r) => { toast(r.ok ? "设置已保存" : r.msg, r.ok ? "success" : "error"); if (r.ok) closeModal("settingsModal"); });
    });
    $("#btnApply").addEventListener("click", () => {
      api().apply_now().then(() => toast("正在下发配置…", "info"));
    });

    // 门户
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
      api().save_config(portalPatch()).then((r) => toast(r.ok ? "已保存" : r.msg, r.ok ? "success" : "error"));
    });
    $("#btnPfStart").addEventListener("click", () => {
      api().save_config(portalPatch()).then(() => api().portal_start().then(() => toast("正在启动门户…", "info")));
    });
    $("#btnPfStop").addEventListener("click", () => {
      api().portal_stop().then(() => toast("门户已停止", "info"));
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

  function boot() {
    initTheme();
    bind();
    tick();
  }

  if (window.pywebview && window.pywebview.api) boot();
  else window.addEventListener("pywebviewready", boot);
})();
