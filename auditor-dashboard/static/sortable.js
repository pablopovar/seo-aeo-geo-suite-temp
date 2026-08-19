(() => {
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

  function value(cell, type) {
    const raw = (cell.dataset.sortValue ?? cell.textContent ?? "").trim();
    if (type === "number") {
      if (!raw || raw === "—" || raw === "-") return null;
      const n = Number(raw.replace(/,/g, "").replace(/%$/, ""));
      return Number.isFinite(n) ? n : null;
    }
    return raw;
  }

  function compare(a, b, type, dir) {
    const av = value(a, type), bv = value(b, type);
    if (av === null || av === "") return (bv === null || bv === "") ? 0 : 1;
    if (bv === null || bv === "") return -1;
    const r = type === "number" ? av - bv : collator.compare(String(av), String(bv));
    return dir === "asc" ? r : -r;
  }

  document.querySelectorAll("table[data-sortable]").forEach(table => {
    const tbody = table.tBodies[0];
    if (!tbody) return;

    table.querySelectorAll("thead th[data-sort]").forEach((th, index) => {
      th.classList.add("sortable-header");
      th.tabIndex = 0;
      const indicator = document.createElement("span");
      indicator.className = "sort-indicator";
      indicator.textContent = "↕";
      th.appendChild(indicator);

      const sort = () => {
        const oldIndex = Number(table.dataset.sortIndex ?? -1);
        const oldDir = table.dataset.sortDirection ?? "asc";
        const dir = oldIndex === index && oldDir === "asc" ? "desc" : "asc";
        const type = th.dataset.sort || "text";
        const rows = Array.from(tbody.rows);

        rows.sort((a, b) => compare(a.cells[index], b.cells[index], type, dir));
        rows.forEach(r => tbody.appendChild(r));

        Array.from(tbody.rows).forEach((r, i) => {
          const c = r.querySelector("[data-row-number]");
          if (c) c.textContent = String(i + 1);
        });

        table.dataset.sortIndex = String(index);
        table.dataset.sortDirection = dir;

        table.querySelectorAll("thead th[data-sort]").forEach((h, i) => {
          const x = h.querySelector(".sort-indicator");
          h.classList.toggle("sort-active", i === index);
          if (x) x.textContent = i === index ? (dir === "asc" ? "▲" : "▼") : "↕";
        });
      };

      th.addEventListener("click", sort);
      th.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          sort();
        }
      });
    });
  });
})();