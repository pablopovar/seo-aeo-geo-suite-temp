(() => {
  const dm = location.pathname.match(/^\/d\/([^/]+)/);
  if (!dm) return;
  const domain = decodeURIComponent(dm[1]);

  async function post(url, data) {
    const body = new URLSearchParams();
    Object.entries(data).forEach(([k,v]) => Array.isArray(v) ? v.forEach(x => body.append(k,x)) : body.append(k,v ?? ""));
    const r = await fetch(url, {method:"POST", headers:{"Content-Type":"application/x-www-form-urlencoded;charset=UTF-8"}, body});
    if (!r.ok) throw new Error();
    return r.json();
  }

  function info(table) {
    const hs=[...table.querySelectorAll("thead th")].map(x=>x.textContent.trim().toLowerCase());
    if (hs.includes("keyword")) return {kind:"keyword", index:hs.indexOf("keyword")};
    if (hs.includes("page")) return {kind:"page", index:hs.indexOf("page")};
    return null;
  }

  function valueFor(row, inf) {
    const cells=row.querySelectorAll(":scope > td");
    if (!cells[inf.index]) return "";
    if (inf.kind==="page") {
      const a=row.querySelector("a[href*='/pages/']");
      const m=a?.getAttribute("href")?.match(/\/pages\/(\d+)/);
      return m ? m[1] : "";
    }
    return cells[inf.index].textContent.trim();
  }

  async function enhance(table) {
    if (table.dataset.taxonomyReady==="1") return;
    const inf=info(table); if (!inf) return;
    table.dataset.taxonomyReady="1";

    if (inf.kind==="keyword") {
      table.querySelectorAll("tbody tr").forEach(row=>{
        const cells=row.querySelectorAll(":scope > td");
        const cell=cells[inf.index];
        if (!cell || cell.querySelector("a") || cell.querySelector("input,form,select")) return;
        const kw=cell.textContent.trim(); if (!kw) return;
        const a=document.createElement("a");
        a.className="keyword-workspace-link";
        a.href=`/d/${encodeURIComponent(domain)}/keyword?keyword=${encodeURIComponent(kw)}`;
        a.textContent=kw; cell.textContent=""; cell.appendChild(a);
      });
    }

    const rows=[...table.querySelectorAll("tbody tr")];
    const values=rows.map(r=>valueFor(r,inf)).filter(Boolean);
    if (!values.length) return;

    let data;
    try { data=await post(`/d/${encodeURIComponent(domain)}/taxonomy/lookup`, {kind:inf.kind, values}); } catch { return; }

    const hr=table.querySelector("thead tr");
    const th=document.createElement("th");
    th.textContent=inf.kind==="page"?"Page Tags":"Keyword Tags";
    hr.appendChild(th);

    rows.forEach(row=>{
      const v=valueFor(row,inf); if (!v) return;
      const td=document.createElement("td");
      td.className="taxonomy-cell";
      const tags=data.assignments[v]||[];
      if (!tags.length) td.innerHTML='<span class="keyword-untagged">Untagged</span>';
      else tags.forEach(t=>{const b=document.createElement("span");b.className="keyword-tag";b.textContent=t.name;td.appendChild(b);});
      row.appendChild(td);

      const first=row.querySelector("td");
      if (first && !row.querySelector(".taxonomy-select")) {
        let cb=first.querySelector("input[type=checkbox]");
        if (!cb) { cb=document.createElement("input"); cb.type="checkbox"; first.prepend(cb); }
        cb.classList.add("taxonomy-select");
        cb.dataset.value=v;
      }
    });

    const toolbar=document.createElement("div");
    toolbar.className="taxonomy-toolbar";
    toolbar.innerHTML=`
      <button type="button" class="button select-page-taxonomy">Select page</button>
      <select class="taxonomy-tag"><option value="">Choose ${inf.kind} tag…</option>${data.tags.map(t=>`<option value="${t.id}">${t.name}</option>`).join("")}</select>
      <input class="taxonomy-new-tag" placeholder="or new ${inf.kind} tag">
      <button type="button" class="button add-taxonomy">Add tag</button>
      <button type="button" class="button remove-taxonomy">Remove tag</button>
      <select class="taxonomy-filter"><option value="">Tag filter…</option><option value="show">Show selected tag</option><option value="hide">Hide selected tag</option><option value="clear">Clear tag filter</option></select>`;
    table.parentNode.insertBefore(toolbar, table);

    toolbar.querySelector(".select-page-taxonomy").onclick=()=>{
      const boxes=[...table.querySelectorAll(".taxonomy-select")], next=!boxes.every(x=>x.checked);
      boxes.forEach(x=>x.checked=next);
    };

    async function apply(action){
      const vals=[...table.querySelectorAll(".taxonomy-select:checked")].map(x=>x.dataset.value);
      if (!vals.length) return;
      const tag_id=toolbar.querySelector(".taxonomy-tag").value;
      const new_tag=toolbar.querySelector(".taxonomy-new-tag").value.trim();
      if (action==="add" && !tag_id && !new_tag) return;
      if (action==="remove" && !tag_id) return;
      await post(`/d/${encodeURIComponent(domain)}/taxonomy/apply`, {kind:inf.kind, action, values:vals, tag_id, new_tag});
      location.reload();
    }
    toolbar.querySelector(".add-taxonomy").onclick=()=>apply("add");
    toolbar.querySelector(".remove-taxonomy").onclick=()=>apply("remove");

    toolbar.querySelector(".taxonomy-filter").onchange=e=>{
      const mode=e.target.value;
      if (mode==="clear") { rows.forEach(r=>r.hidden=false); return; }
      const id=toolbar.querySelector(".taxonomy-tag").value;
      const tag=data.tags.find(t=>String(t.id)===String(id));
      if (!tag) return;
      rows.forEach(r=>{
        const names=(data.assignments[valueFor(r,inf)]||[]).map(t=>t.name.toLowerCase());
        const has=names.includes(tag.name.toLowerCase());
        r.hidden=mode==="show"?!has:has;
      });
    };
  }

  document.querySelectorAll("table").forEach(enhance);
})();
