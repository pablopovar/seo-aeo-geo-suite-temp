(() => {
  if (!/\/keywords\/?$/.test(location.pathname)) return;
  const dm = location.pathname.match(/^\/d\/([^/]+)/);
  if (!dm) return;
  document.querySelectorAll("table").forEach(table => {
    const hs=[...table.querySelectorAll("thead th")].map(x=>x.textContent.trim().toLowerCase());
    const i=hs.indexOf("keyword"); if(i<0)return;
    table.querySelectorAll("tbody tr").forEach(row=>{
      const cells=row.querySelectorAll(":scope > td"), cell=cells[i];
      if(!cell || cell.querySelector("a,input,form,select")) return;
      const kw=cell.textContent.trim(); if(!kw)return;
      const a=document.createElement("a");
      a.href=`/d/${dm[1]}/keyword?keyword=${encodeURIComponent(kw)}`;
      a.className="keyword-workspace-link"; a.textContent=kw;
      cell.textContent=""; cell.appendChild(a);
    });
  });
})();