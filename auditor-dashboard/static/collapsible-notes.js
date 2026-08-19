(() => {
  const root = document.querySelector("main") || document.body;

  function nearestLabel(el, index) {
    const section = el.closest("section, .panel");
    if (section) {
      const heading = section.querySelector("h1,h2,h3");
      if (heading && heading.textContent.trim()) return heading.textContent.trim();
    }
    let prev = el.previousElementSibling;
    while (prev) {
      const heading = prev.matches?.("h1,h2,h3") ? prev : prev.querySelector?.("h1,h2,h3");
      if (heading && heading.textContent.trim()) return heading.textContent.trim();
      prev = prev.previousElementSibling;
    }
    return `List ${index + 1}`;
  }

  function makeCollapsible(target, index) {
    if (!target || target.dataset.collapsibleReady === "1") return;
    if (target.closest("nav,header,.tag-filter-menu,.keyword-tags,.bulk-toolbar")) return;

    target.dataset.collapsibleReady = "1";
    const label = nearestLabel(target, index);
    const key = ["auditor-collapsible", location.pathname, label, index].join("::");

    const wrapper = document.createElement("div");
    wrapper.className = "collapsible-list";
    wrapper.dataset.collapsed = localStorage.getItem(key) === "collapsed" ? "1" : "0";

    const bar = document.createElement("div");
    bar.className = "collapsible-list-bar";

    const title = document.createElement("span");
    title.className = "collapsible-list-title";
    title.textContent = label;

    const button = document.createElement("button");
    button.type = "button";
    button.className = "button collapsible-list-toggle";

    const body = document.createElement("div");
    body.className = "collapsible-list-body";

    target.parentNode.insertBefore(wrapper, target);
    wrapper.appendChild(bar);
    bar.appendChild(title);
    bar.appendChild(button);
    wrapper.appendChild(body);
    body.appendChild(target);

    function refresh() {
      const collapsed = wrapper.dataset.collapsed === "1";
      body.hidden = collapsed;
      button.textContent = collapsed ? "Expand" : "Collapse";
    }

    button.onclick = () => {
      wrapper.dataset.collapsed = wrapper.dataset.collapsed === "1" ? "0" : "1";
      localStorage.setItem(key, wrapper.dataset.collapsed === "1" ? "collapsed" : "expanded");
      refresh();
    };

    refresh();
  }

  const candidates = [];
  root.querySelectorAll(".table-wrap").forEach(el => candidates.push(el));
  root.querySelectorAll("table").forEach(el => {
    if (!el.closest(".table-wrap")) candidates.push(el);
  });
  root.querySelectorAll("ul,ol").forEach(el => {
    if (!el.closest("nav,header,.tag-filter-menu,.keyword-tags,.bulk-toolbar") && el.children.length > 1) {
      candidates.push(el);
    }
  });
  [...new Set(candidates)].forEach(makeCollapsible);

  const dm = location.pathname.match(/^\/d\/([^/]+)/);
  if (!dm) return;
  const domain = decodeURIComponent(dm[1]);

  function keywordColumnIndex(table) {
    return [...table.querySelectorAll("thead th")]
      .findIndex(th => th.textContent.trim().toLowerCase() === "keyword");
  }

  root.querySelectorAll("table").forEach(table => {
    const idx = keywordColumnIndex(table);
    if (idx < 0 || table.dataset.keywordNotesReady === "1") return;
    table.dataset.keywordNotesReady = "1";

    const hr = table.querySelector("thead tr");
    if (!hr) return;
    const th = document.createElement("th");
    th.textContent = "Note";
    hr.appendChild(th);

    table.querySelectorAll("tbody tr").forEach(row => {
      const cells = row.querySelectorAll(":scope > td");
      const td = document.createElement("td");
      row.appendChild(td);
      if (!cells[idx]) return;

      const keyword = cells[idx].textContent.trim();
      if (!keyword) return;

      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "button keyword-note-toggle";
      toggle.textContent = "Note";

      const editor = document.createElement("div");
      editor.className = "keyword-note-editor";
      editor.hidden = true;

      const textarea = document.createElement("textarea");
      textarea.className = "keyword-note-textarea";
      textarea.placeholder = "One note for this keyword...";

      const save = document.createElement("button");
      save.type = "button";
      save.className = "primary";
      save.textContent = "Save note";

      const status = document.createElement("span");
      status.className = "keyword-note-status";

      editor.append(textarea, save, status);
      td.append(toggle, editor);

      let loaded = false;

      toggle.onclick = async () => {
        editor.hidden = !editor.hidden;
        if (editor.hidden || loaded) return;
        status.textContent = "Loading…";
        try {
          const r = await fetch(`/d/${encodeURIComponent(domain)}/keyword-note?keyword=${encodeURIComponent(keyword)}`);
          const data = await r.json();
          textarea.value = data.content || "";
          loaded = true;
          toggle.textContent = textarea.value.trim() ? "Note ✓" : "Note";
          status.textContent = "";
        } catch {
          status.textContent = "Could not load note.";
        }
      };

      save.onclick = async () => {
        status.textContent = "Saving…";
        const body = new URLSearchParams({keyword, content: textarea.value});
        try {
          const r = await fetch(`/d/${encodeURIComponent(domain)}/keyword-note`, {
            method: "POST",
            headers: {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            body
          });
          if (!r.ok) throw new Error();
          toggle.textContent = textarea.value.trim() ? "Note ✓" : "Note";
          status.textContent = "Saved";
          setTimeout(() => status.textContent = "", 1200);
        } catch {
          status.textContent = "Could not save note.";
        }
      };
    });
  });
})();
