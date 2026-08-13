(() => {
  "use strict";
  if (window.__FOA_RESULT_ROUTING_PERSISTENCE_SUMMARY__) return;
  window.__FOA_RESULT_ROUTING_PERSISTENCE_SUMMARY__ = true;

  const PREFIX = "foa-result-routing-edit-draft-v2";
  const PREDICTED = new Set(["over", "under", "matches", "differs"]);
  const CMP = {">":"greater than","<":"less than","==":"equal to",">=":"greater than or equal to","<=":"less than or equal to",all_same:"all the same",all_even:"all even",all_odd:"all odd"};
  const DIR = {rising:"up ticks",falling:"down ticks",no_move:"no-move ticks"};
  const TARGET = {even:"even digits",odd:"odd digits",over:"digits over",under:"digits under",digit:"exact digit",rise:"up ticks",fall:"down ticks",no_move:"no-move ticks"};
  let scheduled = false;
  const q = (s, r=document) => r.querySelector(s);
  const qa = (s, r=document) => Array.from(r.querySelectorAll(s));
  const num = (v, f=0) => Number.isFinite(Number(v)) ? Number(v) : f;
  const whole = (v, f, lo, hi) => Math.round(Math.max(lo, Math.min(hi, num(v, f))));

  function me() { return window.FOA_NETLIFY_LIVE_CACHE?.me || window.FOA_BOOT_SESSION || {}; }
  function accountKey() {
    const a = me();
    const mode = String(a.account_type || "demo").toLowerCase() === "real" ? "real" : "demo";
    const id = String(a.account_id_masked || a.account_id || a.label || q(".account-pill")?.textContent || "account").trim();
    return `${mode}:${id}`;
  }
  function key() { return `${PREFIX}:${accountKey()}`; }
  function getDraft() {
    try { const x = JSON.parse(localStorage.getItem(key()) || "null"); return x?.result_routing ? x : null; }
    catch (_) { return null; }
  }
  function setDraft(result_routing) {
    try { localStorage.setItem(key(), JSON.stringify({saved_at:Date.now(), result_routing})); } catch (_) {}
  }
  function clearDraft() { try { localStorage.removeItem(key()); } catch (_) {} }
  function bv(path, fallback="") {
    const f = q(`[data-builder="${path}"]`); if (!f) return fallback;
    return f.type === "checkbox" ? f.checked : f.value;
  }
  function rv(path, fallback="") {
    const f = q(`[data-result-route="${path}"]`); if (!f) return fallback;
    return f.type === "checkbox" ? f.checked : f.value;
  }

  function routeFromDom() {
    const trade = String(rv("tradeType", "over"));
    const mode = String(rv("analysisMode", "last_digit"));
    const conditions = [];
    if (mode === "last_digit" || mode === "combined") {
      const op = String(rv("lastRule.operator", ">="));
      conditions.push({kind:"digit_compare",window:whole(rv("lastRule.window",5),5,1,1000),operator:op,value:["all_same","all_even","all_odd"].includes(op)?null:whole(rv("lastRule.value",3),3,0,9)});
    }
    if (mode === "percentage" || mode === "combined") {
      const target = String(rv("percentageRule.target", "even"));
      const c = {kind:"percentage",window:whole(rv("percentageRule.window",500),500,1,1000),target,operator:String(rv("percentageRule.operator",">=")),threshold:Math.max(0,Math.min(100,num(rv("percentageRule.threshold",70),70)))};
      if (["over","under","digit"].includes(target)) c.value = whole(rv("percentageRule.value",5),5,0,9);
      conditions.push(c);
    }
    if (Boolean(rv("tickDirectionRule.enabled", false))) conditions.push({kind:"direction",window:whole(rv("tickDirectionRule.window",3),3,1,1000),direction:String(rv("tickDirectionRule.direction","rising"))});
    return {trade_type:trade,prediction:PREDICTED.has(trade)?whole(rv("prediction",2),2,0,9):null,duration_ticks:whole(rv("durationTicks",1),1,1,100),conditions,match:"all"};
  }
  function routingFromDom() {
    const toggle = q("#result-routing-enabled");
    if (!toggle) return null;
    return toggle.checked ? {enabled:true,after_loss:routeFromDom()} : {enabled:false};
  }
  function capture() { const x = routingFromDom(); if (x) setDraft(x); }

  function cloneJsonResponse(response, payload) {
    const headers = new Headers(response.headers); headers.set("Content-Type", "application/json");
    return new Response(JSON.stringify(payload), {status:response.status,statusText:response.statusText,headers});
  }
  function installFetchAuthority() {
    if (window.__FOA_RESULT_ROUTING_PERSISTENCE_FETCH__) return;
    window.__FOA_RESULT_ROUTING_PERSISTENCE_FETCH__ = true;
    const underlying = window.fetch.bind(window);
    window.fetch = async (input, init={}) => {
      const url = typeof input === "string" ? input : String(input?.url || "");
      const method = String(init?.method || input?.method || "GET").toUpperCase();
      const custom = url.includes("/me/custom-strategy") || url.includes("/api/me/custom-strategy");
      const draft = custom ? getDraft() : null;
      let next = init;
      if (custom && method === "POST" && draft && typeof init.body === "string") {
        try { const body = JSON.parse(init.body); body.result_routing = draft.result_routing; next = {...init,body:JSON.stringify(body)}; } catch (_) {}
      }
      const response = await underlying(input, next);
      if (!custom || !response.ok) return response;
      if (method === "POST" && draft) { clearDraft(); return response; }
      if (method === "GET" && draft) {
        try { const payload = await response.clone().json(); payload.result_routing = draft.result_routing; return cloneJsonResponse(response, payload); } catch (_) {}
      }
      return response;
    };
  }

  function conditionText(c) {
    if (c.kind === "digit_compare") return ["all_same","all_even","all_odd"].includes(c.operator) ? `last ${c.window} digits are ${CMP[c.operator]}` : `last ${c.window} digits are ${CMP[c.operator]||c.operator} ${c.value}`;
    if (c.kind === "percentage") return `${TARGET[c.target]||c.target}${["over","under","digit"].includes(c.target)?` ${c.value}`:""} in the past ${c.window} ticks is ${CMP[c.operator]||c.operator} ${c.threshold}%`;
    if (c.kind === "direction") return `last ${c.window} tick directions are ${DIR[c.direction]||c.direction}`;
    return "";
  }
  function primaryConditions() {
    const mode = String(q("[data-strategy-mode].active")?.dataset?.strategyMode || "last_digit");
    const c = [];
    if (mode === "last_digit" || mode === "combined") {
      const op = String(bv("lastRule.operator", ">=")); const w = whole(bv("lastRule.window",5),5,1,1000);
      c.push(["all_same","all_even","all_odd"].includes(op)?`last ${w} digits are ${CMP[op]}`:`last ${w} digits are ${CMP[op]||op} ${whole(bv("lastRule.value",3),3,0,9)}`);
    }
    if (mode === "percentage" || mode === "combined") {
      const t = String(bv("percentageRule.target","even"));
      c.push(`${TARGET[t]||t}${["over","under","digit"].includes(t)?` ${whole(bv("percentageRule.value",5),5,0,9)}`:""} in the past ${whole(bv("percentageRule.window",500),500,1,1000)} ticks is ${CMP[String(bv("percentageRule.operator",">="))]||bv("percentageRule.operator",">=")} ${num(bv("percentageRule.threshold",70),70)}%`);
    }
    if (Boolean(bv("tickDirectionRule.enabled",false))) c.push(`last ${whole(bv("tickDirectionRule.window",3),3,1,1000)} tick directions are ${DIR[String(bv("tickDirectionRule.direction","rising"))]||bv("tickDirectionRule.direction","rising")}`);
    return c.join(" AND ") || "configured conditions";
  }
  function markets() {
    const mode = String(q("[data-market-mode].active")?.dataset?.marketMode || "selected");
    if (mode === "all") return "all supported markets";
    const chips = qa(".market-chips .market-chip.active").map(x=>String(x.textContent||"").replace(/x\s*$/i,"").trim()).filter(Boolean);
    if (mode === "single") return chips[0] || String(q("[data-market-select]")?.value || "selected market");
    return chips.length ? chips.join(", ") : "selected markets";
  }
  function primaryTrade() {
    const side = String(bv("trade.side","over")); const label = side.charAt(0).toUpperCase()+side.slice(1);
    let prediction = "";
    if (PREDICTED.has(side)) {
      const dynamic = ["matches","differs"].includes(side) ? q("[data-last-digit-prediction]") : null;
      const map = {last_digit:"last digit",most_appearing:"most appearing digit",second_most_appearing:"second most appearing digit"};
      prediction = dynamic ? (map[String(dynamic.value)] || String(dynamic.value)) : String(whole(bv("trade.prediction",2),2,0,9));
    }
    const ticks = whole(bv("money.ticks",1),1,1,100);
    return `${label}${prediction?` ${prediction}`:""} for ${ticks} tick${ticks===1?"":"s"}`;
  }
  function reanalysis() {
    const mode = String(bv("reanalyze.mode","after_every_trade")); const l=whole(bv("reanalyze.losses",1),1,1,1000), w=whole(bv("reanalyze.wins",1),1,1,1000);
    if (mode === "custom") return `after ${l} loss${l===1?"":"es"} or ${w} win${w===1?"":"s"}`;
    if (mode === "after_loss") return `after ${l} loss${l===1?"":"es"}`;
    if (mode === "after_win") return `after ${w} win${w===1?"":"s"}`;
    return "after every trade";
  }
  function recovery() {
    const style = String(q("#recovery-style")?.value || "multiplier");
    if (style !== "split") return `current Martingale multiplier ×${num(bv("money.martingale",1),1)}`;
    const count = whole(q("#recovery-split-count-input")?.value || q("#recovery-split-count")?.value || 2,2,1,3);
    return `Martingale Spread; recover outstanding loss equally across ${count} successful recovery ${count===1?"run":"runs"}`;
  }
  function resultRouting() {
    const r = routingFromDom() || getDraft()?.result_routing;
    if (!r?.enabled) return "OFF; primary strategy remains in use after losses";
    const a = r.after_loss || {}, side=String(a.trade_type||"over"), label=side.charAt(0).toUpperCase()+side.slice(1), ticks=whole(a.duration_ticks,1,1,100);
    const conditions=(a.conditions||[]).map(conditionText).filter(Boolean).join(" AND ") || "configured after-loss conditions";
    return `ON; after an actual loss wait for ${conditions}, then trade ${label}${PREDICTED.has(side)&&a.prediction!==null?` ${a.prediction}`:""} for ${ticks} tick${ticks===1?"":"s"}, while recovery debt remains`;
  }
  function virtualHook() {
    if (!Boolean(bv("virtualHook.enabled",false))) return "OFF";
    const l=whole(bv("virtualHook.enterAfterLosses",2),2,1,50), w=whole(bv("virtualHook.exitAfterConsecutiveWins",1),1,1,50);
    return `ON; enter after ${l} actual loss${l===1?"":"es"}, exit after ${w} consecutive virtual win${w===1?"":"s"}`;
  }
  function summaryText() {
    return `Primary conditions: ${primaryConditions()}. Markets: ${markets()}. Primary trade: ${primaryTrade()}. Re-analysis: ${reanalysis()}. Money management: stake $${num(bv("money.stake",0),0).toFixed(2)}, TP $${num(bv("money.takeProfit",0),0).toFixed(2)}, SL $${Math.abs(num(bv("money.stopLoss",0),0)).toFixed(2)}, Martingale ×${num(bv("money.martingale",1),1)}. Recovery plan: ${recovery()}. Result-based trading: ${resultRouting()}. Virtual Hook: ${virtualHook()}.`;
  }

  function enhance() {
    scheduled = false;
    const draft = getDraft()?.result_routing, toggle = q("#result-routing-enabled"), box = q("#result-routing-section .result-routing-recovery-box");
    if (draft && toggle) { toggle.checked = Boolean(draft.enabled); if (box) box.hidden = !draft.enabled; }
    const summary = q(".live-summary p"); if (summary) { const t=summaryText(); if (summary.textContent!==t) summary.textContent=t; }
  }
  function schedule() { if (!scheduled) { scheduled=true; requestAnimationFrame(enhance); } }

  installFetchAuthority();
  document.addEventListener("change", e => { if (e.target?.matches?.("#result-routing-enabled,[data-result-route]")) capture(); setTimeout(schedule,0); }, true);
  document.addEventListener("input", e => { if (e.target?.matches?.("[data-result-route]")) capture(); setTimeout(schedule,0); }, true);
  new MutationObserver(schedule).observe(document.documentElement,{childList:true,subtree:true,characterData:true});
  window.addEventListener("pageshow",schedule); window.addEventListener("focus",schedule);
  document.addEventListener("visibilitychange",()=>{if(!document.hidden)schedule();});
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded",schedule,{once:true}) : schedule();
  window.FOA_RESULT_ROUTING_PERSISTENCE_SUMMARY_VERSION = "20260813-1";
})();
