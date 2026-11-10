const fmt = (n) => {
  let f = n, u = "B";
  for (const x of ["KB","MB","GB"]) { if (f < 1024) break; f /= 1024; u = x; }
  return (f < 10 && u !== "B" ? f.toFixed(1) : Math.round(f)) + " " + u;
};
// ---- i18n：从后端拉翻译表（en 模式非空，zh 模式为空表 = 中文原文） ----
let I18N = {};
let lastUp = 0, lastDown = 0;
let lastLangKnown = null;             // 轮询检测语言变化 → 重拉字典热切换
const t = (k, vars) => {
  let s = I18N[k] || k;
  if (vars) for (const key in vars) s = s.split("{" + key + "}").join(vars[key]);
  return s;
};
// 字典到位后刷新静态文案与标题
function applyStatic() {
  const nl = document.getElementById("nlabel");
  if (nl) nl.textContent = t("台在线");
  const tg = document.getElementById("toggle");
  if (tg) tg.title = t("点击 开/关 热点");
  const ex = document.getElementById("expand");
  if (ex) ex.title = t("返回主界面");
  document.getElementById("up").innerHTML = t("上行 ↑ <b>{v}</b>", { v: fmt(lastUp) });
  document.getElementById("down").innerHTML = t("下行 ↓ <b>{v}</b>", { v: fmt(lastDown) });
}
async function refreshI18n() {
  try {
    const dic = await fetch('/api/op/getI18n',{method:'POST',headers:{'content-type':'application/json'},body:'{}'}).then(r=>r.json());
    I18N = dic || {};
    applyStatic();
  } catch (e) {}
}
async function initTheme() {
  try {
    const th = await fetch('/api/op/getTheme',{method:'POST',headers:{'content-type':'application/json'},body:'{}'}).then(r=>r.text()).then(s=>{try{return JSON.parse(s)}catch{return s.replace(/"/g,'')}});
    document.documentElement.setAttribute("data-theme", th === "light" ? "light" : "dark");
  } catch (e) {}
}
// 三态状态机：off / busy（开关过渡，~15s 超时自恢复）/ on
// active 与后端一致；busy 从点击时刻起，直到 mini_state 返回的 active 与目标一致。
let lastActive = null;      // 上次已知 active
let pendingTo = null;       // 点击后的目标状态（"on"/"off"），达成前保持 busy
let busySince = 0;

function applyState(active, busy) {
  const card = document.getElementById("card");
  card.classList.toggle("on", active && !busy);
  card.classList.toggle("busy", busy);
}
async function tick() {
  try {
    const st = await fetch('/api/mini').then(r=>r.json());
    const active = !!st.active;
    lastActive = active;
    document.documentElement.setAttribute("data-theme", st.theme === "light" ? "light" : "dark");
    // 语言热切换：mini_state.lang 与上次不同 → 重拉翻译表并刷新文案
    if (st.lang && lastLangKnown && st.lang !== lastLangKnown) await refreshI18n();
    lastLangKnown = st.lang || lastLangKnown;
    document.getElementById("n").textContent = st.online || 0;
    lastUp = st.up || 0; lastDown = st.down || 0;
    document.getElementById("down").innerHTML = t("下行 ↓ <b>{v}</b>", { v: fmt(st.down || 0) });
    document.getElementById("up").innerHTML = t("上行 ↑ <b>{v}</b>", { v: fmt(st.up || 0) });
    const busy = pendingTo !== null && active !== (pendingTo === "on")
                 && (Date.now() - busySince < 15000);  // 15s 超时兜底
    if (pendingTo !== null && (!busy || active === (pendingTo === "on"))) pendingTo = null;
    applyState(active, busy);
  } catch (e) {}
  setTimeout(tick, 1000);
}
// ---- 交互：左图标=开关，右按钮=回主界面 ----
document.getElementById("toggle").addEventListener("click", (e) => {
  e.stopPropagation();
  try {
    const target = !lastActive;
    pendingTo = target ? "on" : "off";
    busySince = Date.now();
    applyState(lastActive, true);   // 立即进 busy（旋转圈）
    fetch('/api/op/toggle',{method:'POST',headers:{'content-type':'application/json'},body:'{}'});
  } catch (e) {}
});
document.getElementById("expand").addEventListener("click", (e) => {
  e.stopPropagation();
  try { window.close(); window.opener&&window.opener.focus&&window.opener.focus(); } catch (e) {}
});
// ---- 拖拽：空白处按下即触发 Windows 原生拖拽（消息循环接管，轨迹=鼠标）； ----
// ---- 按钮上按下不拖（点击留给按钮） ----
// ---- popup 窗由系统标题栏拖动，无原生拖拽 ----
// ---- 启动：不再等 pywebviewready（浏览器里不会触发），直接初始化 ----
initTheme(); refreshI18n(); tick();
