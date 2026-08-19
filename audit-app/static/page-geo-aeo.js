(() => {
  const m=location.pathname.match(/^\/d\/([^/]+)\/pages\/(\d+)\/?$/); if(!m)return;
  const domain=decodeURIComponent(m[1]), pageId=m[2], main=document.querySelector("main")||document.body;
  const panel=document.createElement("section"); panel.className="panel integrated-audit-panel";
  panel.innerHTML='<div class="heading"><div><h2>GEO / AEO Signals</h2><p class="research-help">Latest observations + editable workflow state.</p></div><button class="primary run-page-audit">Run page audit</button></div><div class="audit-panel-body">Loading…</div>';
  const note=main.querySelector(".page-note-panel"); if(note)main.insertBefore(panel,note); else main.appendChild(panel);
  const body=panel.querySelector(".audit-panel-body");
  const esc=v=>{const d=document.createElement("div"); d.textContent=v??""; return d.innerHTML};

  async function load(){
    const r=await fetch(`/d/${encodeURIComponent(domain)}/pages/${pageId}/geo-aeo.json`);
    if(!r.ok){body.textContent="Could not load GEO/AEO data.";return}
    const data=await r.json();
    if(!data.audit){body.innerHTML="<p>No stored audit for this page yet.</p>";return}
    body.innerHTML=`<div class="audit-score-row"><span>GEO <strong>${data.audit.geo_score??"—"}</strong></span><span>AEO <strong>${data.audit.aeo_score??"—"}</strong></span><span>Combined <strong>${data.audit.combined_score??"—"}</strong></span><span>Run #${data.audit.run_id}</span></div><div class="table-wrap"><table><thead><tr><th>Family</th><th>Signal</th><th>Observed</th><th>Severity</th><th>Evidence</th><th>Recommendation</th><th>Workflow</th><th>Priority</th><th>Note</th></tr></thead><tbody></tbody></table></div>`;
    const tbody=body.querySelector("tbody");
    data.signals.forEach(s=>{
      const tr=document.createElement("tr");
      tr.innerHTML=`<td>${esc(s.family)}</td><td><strong>${esc(s.title)}</strong><div class="signal-small">${esc(s.category)}</div></td><td>${esc(s.observed_status)}</td><td>${esc(s.severity)}</td><td class="signal-long">${esc(s.evidence)}</td><td class="signal-long">${esc(s.recommendation)}</td><td><select class="workflow">${["open","accepted","fixed","ignore"].map(x=>`<option ${x===s.workflow_status?"selected":""}>${x}</option>`).join("")}</select></td><td><select class="priority">${["","low","medium","high","critical"].map(x=>`<option value="${x}" ${x===s.priority?"selected":""}>${x||"—"}</option>`).join("")}</select></td><td><textarea class="signal-note">${esc(s.user_note||"")}</textarea><button class="button save-signal">Save</button></td>`;
      tr.querySelector(".save-signal").onclick=async()=>{
        const form=new URLSearchParams({family:s.family,signal_key:s.signal_key,workflow_status:tr.querySelector(".workflow").value,priority:tr.querySelector(".priority").value,user_note:tr.querySelector(".signal-note").value});
        const rr=await fetch(`/d/${encodeURIComponent(domain)}/pages/${pageId}/geo-aeo/state`,{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded;charset=UTF-8"},body:form});
        if(!rr.ok)alert("Could not save signal state.");
      };
      tbody.appendChild(tr);
    });
  }

  panel.querySelector(".run-page-audit").onclick=async()=>{
    const r=await fetch(`/d/${encodeURIComponent(domain)}/pages/${pageId}/geo-aeo/run`,{method:"POST"});
    if(!r.ok){alert("Could not start page audit.");return}
    setTimeout(()=>location.reload(),1800);
  };
  load();
})();
