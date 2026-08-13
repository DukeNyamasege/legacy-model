(() => {
  "use strict";
  let scheduled = false;
  const MODES = [["last_digit","Last digit"],["most_appearing","Most appearing"],["second_most_appearing","Second most appearing"]];
  function enhance(){
    scheduled=false;
    const side=document.querySelector('select[data-builder="trade.side"]');
    const input=document.querySelector('input[data-builder="trade.prediction"]');
    if(!side||!input)return;
    const active=["matches","differs"].includes(String(side.value||"").toLowerCase());
    const source=input.closest("label.field")||input.parentElement;
    let field=document.querySelector("[data-final-prediction-field]");
    if(!active){if(source){source.hidden=false;source.style.removeProperty("display");}field?.remove();return;}
    if(source){source.hidden=true;source.style.setProperty("display","none","important");}
    document.querySelectorAll("[data-last-digit-prediction-field]").forEach((node)=>node.remove());
    if(!field){field=document.createElement("label");field.className="field";field.dataset.finalPredictionField="true";source?.after(field);}
    if(!field.querySelector("select")){
      const fixed=String(Math.max(0,Math.min(9,Number(input.value||0))));
      const options=[...MODES,...Array.from({length:10},(_,i)=>[String(i),String(i)])].map(([v,l])=>`<option value="${v}" ${v===fixed?"selected":""}>${l}</option>`).join("");
      field.innerHTML=`<span>Prediction</span><select data-final-prediction>${options}</select>`;
    }
  }
  function schedule(){if(scheduled)return;scheduled=true;requestAnimationFrame(enhance);}
  new MutationObserver(schedule).observe(document.documentElement,{childList:true,subtree:true});
  document.addEventListener("change",schedule,true);
  document.readyState==="loading"?document.addEventListener("DOMContentLoaded",schedule,{once:true}):schedule();
})();