(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const apiBase = (document.querySelector('meta[name="api-base-url"]')?.content || "").replace(/\/$/, "");
  const apiUrl = path => `${apiBase}${path}`;
  const num = (value, digits = 0) => Number(value || 0).toLocaleString("en-US", {minimumFractionDigits: digits, maximumFractionDigits: digits});
  const money = value => `${Number(value || 0) < 0 ? "-" : "+"}$${Math.abs(Number(value || 0)).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
  const plainMoney = value => `$${Number(value || 0).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const state = {summary:null, me:null, lastGood:null, filter:"all", duration:.5, risk:"moderate", ws:null, reconnect:0, countdownTarget:null, chat:[]};
  const snapshotKey = "foas-dashboard-snapshot-v2";

  function setLoading(active, text = "Synchronizing") {
    $("loaderText").textContent = text;
    $("loader").classList.toggle("active", active);
  }
  async function api(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Number(options.timeoutMs || 12000));
    try {
      const response = await fetch(apiUrl(path), {
        credentials:"include", cache:"no-store", ...options,
        signal:options.signal || controller.signal,
        headers:{"Cache-Control":"no-cache", ...(options.headers || {})}
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
      return data;
    } catch (error) {
      if (error?.name === "AbortError") throw new Error("The service took too long to respond.");
      throw error;
    } finally { clearTimeout(timer); }
  }
  function showModal(id) { $(id).hidden = false; document.body.style.overflow = "hidden"; }
  function hideModal(id) { $(id).hidden = true; document.body.style.overflow = ""; }
  document.querySelectorAll("[data-close-modal]").forEach(btn => btn.addEventListener("click", () => hideModal(btn.dataset.closeModal)));
  $("disclaimerBtn")?.addEventListener("click", () => showModal("disclaimerModal"));
  $("settingsBtn")?.addEventListener("click", () => { populateSettings(); showModal("settingsModal"); });
  $("menuBtn")?.addEventListener("click", () => { populateSettings(); showModal("settingsModal"); });

  function setSigned(el, value) {
    const n = Number(value || 0);
    el.textContent = money(n);
    el.classList.toggle("positive", n >= 0);
    el.classList.toggle("negative", n < 0);
  }
  function renderPeriods(system) {
    const periods = [["Yesterday", system.yesterday || {}], ["This Week", system.week || {}], ["This Month", system.month || {}]];
    $("performanceGrid").innerHTML = periods.map(([label, p]) => {
      const mg = Number(p.observed_martingale_pnl ?? p.martingale_pnl ?? 0);
      const flat = Number(p.fixed_pnl || 0);
      return `<article class="period-card"><div class="period-head">▣ ${label}</div><div class="period-body"><div class="period-side"><span>With Martingale</span><strong class="${mg < 0 ? "negative" : "positive"}">${money(mg)}</strong></div><div class="period-side"><span>Without Martingale</span><strong class="${flat < 0 ? "negative" : "positive"}">${money(flat)}</strong></div></div></article>`;
    }).join("");
  }
  function renderSummary(data) {
    if (!data?.system_performance || data.snapshot_unavailable) return;
    state.summary = data;
    state.lastGood = data;
    try { localStorage.setItem(snapshotKey, JSON.stringify(data)); } catch (_) {}
    const system = data.system_performance || {};
    const today = system.today || {};
    const status = String(data.status || "UNKNOWN").toUpperCase();
    const active = ["RUNNING", "ACTIVE"].includes(status);
    $("onlinePill").querySelector("span").textContent = active ? "BOT ONLINE" : status.replaceAll("_", " ");
    $("botLabel").textContent = data.ai_activity_label || (active ? "Bot online" : "Bot status");
    $("botMessage").textContent = data.ai_activity_message || (active ? "Scanning all configured markets" : "Trading engine is not active");
    $("botDetail").textContent = data.ai_activity_detail || "Status received from the live worker.";
    $("updatedAt").textContent = `Updated: ${new Date(data.generated_at || Date.now()).toLocaleString("en-US")}`;
    $("registeredTraders").textContent = num(data.total_traders);
    $("tradingNow").textContent = num(data.active_traders);
    $("totalTradesToday").textContent = num(today.total_trades);
    $("openTrades").textContent = num(data.open_trades);
    state.countdownTarget = system.next_session_close_at ? new Date(system.next_session_close_at).getTime() : null;
    setSigned($("todayMgPnl"), today.observed_martingale_pnl ?? today.martingale_pnl);
    setSigned($("todayFlatPnl"), today.fixed_pnl);
    $("todayMaxStake").textContent = plainMoney(today.observed_maximum_stake ?? today.maximum_martingale_stake ?? .5);
    $("todayFlatStake").textContent = plainMoney(today.flat_stake ?? .5);
    $("todayTrades").textContent = num(today.total_trades);
    $("todayWins").textContent = num(today.wins);
    $("todayLosses").textContent = num(today.losses);
    const wr = Number(today.win_rate || 0);
    $("todayWinRate").textContent = `${num(wr <= 1 ? wr * 100 : wr, 2)}%`;
    $("todayWinStreak").textContent = num(today.longest_win_streak);
    $("todayLossStreak").textContent = num(today.longest_loss_streak);
    renderPeriods(system);
    renderAdvisor();
    renderReview();
    $("refreshWarning").hidden = true;
  }
  function updateCountdown() {
    if (!state.countdownTarget) { $("dayCountdown").textContent = "--:--:--"; return; }
    const seconds = Math.max(0, Math.ceil((state.countdownTarget - Date.now()) / 1000));
    const h = String(Math.floor(seconds / 3600)).padStart(2,"0");
    const m = String(Math.floor((seconds % 3600) / 60)).padStart(2,"0");
    const s = String(seconds % 60).padStart(2,"0");
    $("dayCountdown").textContent = `${h}:${m}:${s}`;
  }
  setInterval(updateCountdown, 1000);

  function renderAdvisor() {
    const today = state.summary?.system_performance?.today || {};
    const balance = Math.max(1, Number($("advisorBalance").value || state.me?.balance || 100));
    const riskPct = {conservative:.0025, moderate:.005, aggressive:.01}[state.risk];
    const durationHours = state.duration === "session" ? null : Number(state.duration);
    const recommended = Math.floor(Math.max(.35, Math.min(balance * riskPct, balance * .015)) * 100) / 100;
    const observedMax = Math.max(.5, Number(today.observed_maximum_stake ?? today.maximum_martingale_stake ?? .5));
    const worst = Math.max(0, Number(today.longest_loss_streak || 0));
    const recoveryRatio = Math.max(1, observedMax / Math.max(.5, Number(today.flat_stake || .5)));
    const stress = Math.max(recommended * (worst + 4), recommended * recoveryRatio * 1.5);
    const start = new Date(today.start || Date.now()).getTime();
    const elapsedHours = Math.max(.5, (Date.now() - start) / 36e5);
    const tradesPerHour = Number(today.total_trades || 0) / elapsedHours;
    const estimated = durationHours == null ? Number(today.total_trades || 0) : Math.max(1, Math.round(tradesPerHour * durationHours));
    const winRate = Number(today.win_rate || 0) * (Number(today.win_rate || 0) <= 1 ? 100 : 1);
    let rating = "MODERATE";
    if (worst >= 8 || winRate < 45 || stress > balance * .7) rating = "HIGH";
    else if (worst <= 4 && winRate >= 52 && stress < balance * .35) rating = "LOW";
    $("advisorStake").textContent = plainMoney(recommended);
    $("advisorStress").textContent = plainMoney(stress);
    $("advisorMaxStake").textContent = plainMoney(observedMax);
    $("advisorLossStreak").textContent = num(worst);
    $("advisorRisk").textContent = rating;
    $("advisorRisk").style.color = rating === "HIGH" ? "var(--red)" : rating === "LOW" ? "var(--green)" : "var(--amber)";
    $("advisorSample").textContent = `${num(estimated)} trades`;
  }
  $("advisorBalance")?.addEventListener("input", renderAdvisor);
  document.querySelectorAll("[data-duration]").forEach(btn => btn.addEventListener("click", () => {
    document.querySelectorAll("[data-duration]").forEach(x => x.classList.remove("active"));
    btn.classList.add("active");
    state.duration = btn.dataset.duration === "session" ? "session" : Number(btn.dataset.duration);
    renderAdvisor();
  }));
  document.querySelectorAll("[data-risk]").forEach(btn => btn.addEventListener("click", () => {
    document.querySelectorAll("[data-risk]").forEach(x => x.classList.remove("active"));
    btn.classList.add("active");
    state.risk = btn.dataset.risk;
    renderAdvisor();
  }));
  function renderReview() {
    const data = state.summary || {};
    const today = data.system_performance?.today || {};
    const winRate = Number(today.win_rate || 0) * (Number(today.win_rate || 0) <= 1 ? 100 : 1);
    const lossStreak = Number(today.longest_loss_streak || 0);
    $("reviewStatus").textContent = String(data.status || "UNKNOWN").replaceAll("_", " ");
    $("reviewPressure").textContent = lossStreak >= 8 ? "High loss-streak pressure" : lossStreak >= 5 ? "Moderate pressure" : "Contained";
    $("reviewPressure").className = lossStreak >= 8 ? "negative" : lossStreak <= 4 ? "positive" : "";
    $("reviewDuration").textContent = lossStreak >= 7 ? "30–60 min" : "1–2 hours";
    $("reviewRecommendation").textContent = winRate < 48 ? "Use conservative stake and shorter sessions" : winRate >= 53 ? "Maintain current filters; monitor drawdown" : "Keep conditions unchanged; collect more evidence";
    $("reviewVersion").textContent = data.strategy_name || "RF-PUT5 AI";
  }

  async function loadMe() {
    try { state.me = await api("/me"); } catch (_) { state.me = {authenticated:false}; }
    const me = state.me;
    if (!me.authenticated) {
      $("loginBtn").hidden = false; $("userChip").hidden = true; $("logoutBtn").hidden = true;
      $("personalBalance").textContent = "Connect Deriv"; $("personalMode").textContent = "▣ Not connected";
      $("autoTradeBtn").disabled = true; $("tokenNote").hidden = true;
      return;
    }
    $("loginBtn").hidden = true; $("userChip").hidden = false; $("logoutBtn").hidden = false;
    $("headerUser").textContent = me.label || "FoAS Trader";
    $("headerUserMode").textContent = `${String(me.account_type || "demo").replace(/^./, x => x.toUpperCase())} Account`;
    $("headerMode").textContent = $("headerUserMode").textContent;
    $("personalBalance").textContent = `${num(me.balance, 2)} ${me.currency || "USD"}`;
    $("personalMode").textContent = `▣ ${String(me.account_type || "demo").toUpperCase()} ACCOUNT`;
    $("personalTrades").textContent = num(me.stats?.trades); $("personalWins").textContent = num(me.stats?.wins); $("personalLosses").textContent = num(me.stats?.losses);
    const profit = Number(me.stats?.profit || 0);
    $("personalProfit").textContent = `${profit < 0 ? "-" : ""}${plainMoney(Math.abs(profit))}`;
    $("personalProfit").className = profit < 0 ? "negative" : "positive";
    const button = $("autoTradeBtn");
    button.disabled = false; button.textContent = me.enabled ? "◉ Stop Auto Trading" : "▶ Join Auto Trading"; button.className = `btn ${me.enabled ? "stop" : "start"}`;
    $("tokenNote").hidden = Boolean(me.has_trading_api_token);
    if (!me.has_trading_api_token) button.disabled = true;
    $("advisorBalance").value = Number(me.balance || 100).toFixed(2);
    renderAdvisor();
  }
  function populateSettings() {
    const settings = state.me?.settings || {};
    $("settingStake").value = Number(settings.stake_amount ?? .5).toFixed(2);
    $("settingTakeProfit").value = Number(settings.take_profit ?? 0).toFixed(2);
    $("settingStopLoss").value = Number(settings.stop_loss ?? 0).toFixed(2);
    $("settingMartingale").checked = settings.martingale_enabled ?? true;
    $("tokenField").hidden = Boolean(state.me?.has_trading_api_token);
    $("saveTokenBtn").hidden = Boolean(state.me?.has_trading_api_token);
    $("settingsNotice").textContent = "";
  }
  $("autoTradeBtn")?.addEventListener("click", async () => {
    if (!state.me?.authenticated) return;
    const enable = !state.me.enabled;
    setLoading(true, enable ? "Joining auto trading" : "Stopping auto trading");
    try { await api("/me/auto-trade", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({enabled:enable})}); await loadMe(); }
    catch (e) { alert(e.message); }
    finally { setLoading(false); }
  });
  $("saveSettingsBtn")?.addEventListener("click", async () => {
    if (!state.me?.authenticated) { $("settingsNotice").textContent = "Connect Deriv first."; return; }
    try {
      const result = await api("/me/trading-settings", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({stake_amount:Number($("settingStake").value), take_profit:Number($("settingTakeProfit").value), stop_loss:Number($("settingStopLoss").value), martingale_enabled:$("settingMartingale").checked})});
      state.me.settings = result.settings; $("settingsNotice").textContent = "Settings saved. Changes apply from the next contract."; await loadMe();
    } catch (e) { $("settingsNotice").textContent = e.message; }
  });
  $("saveTokenBtn")?.addEventListener("click", async () => {
    const token = $("apiTokenInput").value.trim();
    if (!token) { $("settingsNotice").textContent = "Enter an API token."; return; }
    try { await api("/me/api-token", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({api_token:token})}); $("apiTokenInput").value = ""; $("settingsNotice").textContent = "Token verified and stored securely."; await loadMe(); populateSettings(); }
    catch (e) { $("settingsNotice").textContent = e.message; }
  });
  $("logoutBtn")?.addEventListener("click", async () => { try { await api("/me/logout", {method:"POST"}); } catch (_) {} location.reload(); });
  $("accountModeBtn")?.addEventListener("click", async () => {
    const modes = state.me?.available_account_types || [];
    if (!state.me?.authenticated || modes.length < 2) return;
    const target = state.me.account_type === "real" ? "demo" : "real";
    setLoading(true, `Switching to ${target} account`);
    try { await api("/me/switch-account", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({account_type:target})}); await loadMe(); await refreshSummary(); }
    catch (e) { alert(e.message); }
    finally { setLoading(false); }
  });

  async function loadContracts() {
    try {
      const mode = state.me?.account_type || "demo";
      const data = await api(`/metrics/recent-trades?limit=50&activity_type=${encodeURIComponent(state.filter)}&mode=${encodeURIComponent(mode)}`, {timeoutMs:12000});
      const rows = Array.isArray(data.trades) ? data.trades : [];
      $("contractsBody").innerHTML = rows.length ? rows.map(row => {
        const virtual = String(row.mode || row.activity_type).toUpperCase().includes("VIRTUAL");
        const outcome = String(row.outcome || "OPEN").toUpperCase();
        const badge = virtual ? "virtual" : outcome === "WIN" ? "win" : outcome === "LOSS" ? "loss" : "virtual";
        const when = row.purchase_time ? new Date(row.purchase_time).toLocaleTimeString("en-US", {hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "—";
        return `<tr><td>${escapeHtml(row.symbol || "—")}</td><td>${escapeHtml(row.contract_type || "—")}</td><td>${virtual ? plainMoney(row.simulated_stake || 0) : plainMoney(row.buy_price || 0)}</td><td>${virtual ? "—" : plainMoney(row.payout || 0)}</td><td><span class="status-badge ${badge}">${escapeHtml(virtual ? outcome.replace("VIRTUAL_", "") + " · Virtual" : outcome)}</span></td><td>${when}</td></tr>`;
      }).join("") : `<tr><td colspan="6" style="text-align:center;color:var(--muted)">No recent activity available.</td></tr>`;
    } catch (_) { $("contractsBody").innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--muted)">Recent contracts are temporarily unavailable.</td></tr>`; }
  }
  document.querySelectorAll("[data-filter]").forEach(btn => btn.addEventListener("click", () => {
    document.querySelectorAll("[data-filter]").forEach(x => x.classList.remove("active"));
    btn.classList.add("active"); state.filter = btn.dataset.filter; loadContracts();
  }));

  function defaultChat() { return [{role:"ai", text:"I use only safe, aggregated dashboard statistics. Ask about today’s performance, staking, loss streaks, recovery pressure, or session duration."}]; }
  function renderChat() {
    $("chatMessages").innerHTML = state.chat.map(m => `<div class="message ${m.role === "ai" ? "ai" : ""}"><div class="message-avatar">${m.role === "ai" ? "AI" : "You"}</div><div class="bubble"><b>${m.role === "ai" ? "AI Advisor" : "You"}</b>${escapeHtml(m.text)}</div></div>`).join("");
    $("chatMessages").scrollTop = $("chatMessages").scrollHeight;
  }
  function advisorAnswer(question) {
    const q = question.toLowerCase();
    const t = state.summary?.system_performance?.today || {};
    if (q.includes("stake") || q.includes("balance")) return `For the selected ${state.risk} profile, the current deterministic recommendation is ${$("advisorStake").textContent}. The stress-test reserve is ${$("advisorStress").textContent}. This is a risk estimate, not a profit guarantee.`;
    if (q.includes("today") || q.includes("doing") || q.includes("performance")) return `Today the model shows ${num(t.total_trades)} trades, ${num(t.wins)} wins, ${num(t.losses)} losses, observed Martingale P/L of ${money(t.observed_martingale_pnl ?? t.martingale_pnl)}, and flat P/L of ${money(t.fixed_pnl)}.`;
    if (q.includes("loss") || q.includes("risk") || q.includes("drawdown")) return `The longest observed loss streak today is ${num(t.longest_loss_streak)}. The largest observed Martingale stake is ${plainMoney(t.observed_maximum_stake ?? t.maximum_martingale_stake ?? .5)}. Shorter sessions and a smaller base stake reduce exposure but cannot eliminate loss risk.`;
    if (q.includes("time") || q.includes("duration") || q.includes("hour")) return `Based on current loss-streak pressure, the advisor favours ${$("reviewDuration").textContent} sessions. Stop when your planned time, take-profit, or stop-loss is reached.`;
    if (q.includes("version") || q.includes("strategy")) return `The active dashboard strategy is ${state.summary?.strategy_name || "RF-PUT5 AI"}. Strategy changes should be versioned, reviewed, and activated deliberately rather than changed during a live session.`;
    return "I can explain current performance, stake guidance, recovery pressure, loss streaks, and session duration using aggregated VPS statistics. I cannot expose private account data, credentials, server design, or source code.";
  }
  $("chatForm")?.addEventListener("submit", event => {
    event.preventDefault();
    const text = $("chatInput").value.trim();
    if (!text) return;
    state.chat.push({role:"user", text}); $("chatInput").value = ""; renderChat();
    setTimeout(() => { state.chat.push({role:"ai", text:advisorAnswer(text)}); renderChat(); }, 220);
  });
  $("clearChatBtn")?.addEventListener("click", () => { state.chat = defaultChat(); renderChat(); });
  $("aiFab")?.addEventListener("click", () => {
    const chat = $("desktopChat");
    if (matchMedia("(max-width: 880px)").matches) {
      chat.classList.toggle("open");
      if (chat.classList.contains("open")) setTimeout(() => $("chatInput").focus(), 180);
    } else { $("chatInput").focus(); chat.scrollIntoView({behavior:"smooth", block:"center"}); }
  });

  async function refreshSummary() {
    try { renderSummary(await api(`/metrics/summary?mode=${encodeURIComponent(state.me?.account_type || "demo")}`, {timeoutMs:5000})); }
    catch (_) { if (state.lastGood) { $("refreshWarning").hidden = false; renderSummary(state.lastGood); } }
  }
  function connectWs() {
    if (document.hidden || state.ws?.readyState <= 1) return;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const base = apiBase ? apiBase.replace(/^http/, "ws") : `${protocol}//${location.host}`;
    try { state.ws = new WebSocket(`${base}/ws/dashboard`); }
    catch (_) { scheduleReconnect(); return; }
    state.ws.onopen = () => { state.reconnect = 0; };
    state.ws.onmessage = event => { try { const message = JSON.parse(event.data); if (message.type === "snapshot" && message.data) renderSummary(message.data); } catch (_) {} };
    state.ws.onclose = () => { state.ws = null; scheduleReconnect(); };
    state.ws.onerror = () => { try { state.ws.close(); } catch (_) {} };
  }
  function scheduleReconnect() {
    if (document.hidden) return;
    const wait = Math.min(30000, 1000 * 2 ** Math.min(state.reconnect++, 5));
    setTimeout(connectWs, wait);
  }
  async function init() {
    state.chat = defaultChat(); renderChat();
    try { const saved = JSON.parse(localStorage.getItem(snapshotKey) || "null"); if (saved?.system_performance) { state.lastGood = saved; renderSummary(saved); } } catch (_) {}
    await loadMe(); await refreshSummary(); loadContracts(); connectWs(); setLoading(false);
    setInterval(() => { if (!document.hidden) { refreshSummary(); loadMe(); loadContracts(); } }, 30000);
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { connectWs(); refreshSummary(); }
    else if (state.ws) { try { state.ws.close(); } catch (_) {} }
  });
  init();
})();
