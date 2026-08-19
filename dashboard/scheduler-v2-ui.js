(() => {
  "use strict";
  if (window.__DERIVADMIN_SCHEDULER_V2_UI__) return;
  window.__DERIVADMIN_SCHEDULER_V2_UI__ = true;

  const style = document.createElement("style");
  style.id = "scheduler-v2-ui-style";
  style.textContent = `
    .schedule-clock-grid{
      display:grid!important;
      grid-template-columns:1.15fr .9fr .55fr 1.2fr!important;
      gap:10px!important;
      width:100%!important;
      min-width:0!important;
    }
    .schedule-clock-grid>label,
    .schedule-clock-grid input,
    .schedule-clock-grid select{min-width:0!important;width:100%!important;max-width:100%!important}
    .schedule-clock-grid #s-second{text-align:center!important;font-variant-numeric:tabular-nums}
    .schedule-history-title{margin-top:18px!important;padding-top:14px!important;border-top:1px solid rgba(126,197,255,.12)!important}
    .schedule-row .schedule-result{display:flex!important;flex-direction:column!important;gap:3px!important;margin-top:7px!important;grid-column:1/-1!important}
    .schedule-row .schedule-result>b{font-size:13px!important;font-variant-numeric:tabular-nums}
    .schedule-row .schedule-result>small{font-size:9px!important;color:#89a0b8!important;line-height:1.35!important}
    .schedule-row .schedule-reason{display:block!important;grid-column:1/-1!important;margin-top:5px!important;color:#bed4e9!important;font-size:9px!important;line-height:1.35!important}
    @media(max-width:700px){
      .schedule-clock-grid{grid-template-columns:minmax(0,1fr) minmax(0,.72fr)!important}
      .schedule-clock-grid>label{overflow:hidden!important}
      .schedule-clock-grid input,.schedule-clock-grid select{font-size:16px!important}
    }
    @media(max-width:390px){
      .schedule-clock-grid{grid-template-columns:minmax(0,1fr)!important}
    }
  `;
  document.head.appendChild(style);
})();
