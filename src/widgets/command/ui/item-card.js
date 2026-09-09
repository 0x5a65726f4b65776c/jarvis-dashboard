// Command Item Card — renders one attention item.
// Renders only. It does not rank, re-order, filter, derive a severity, or
// decide what matters — the rollup did all of that and wrote the answer down.
// Returns: { createItemCard }

const { el, T, isNarrow, addHoverEffect } = ctx;

// Severity is the one closed vocabulary in the whole cross-stack contract, so
// the two values have to be unmistakable at a glance. T.orange sits too close
// to T.red on this theme -- gold reads as clearly distinct without inventing a
// colour outside the palette.
const SEVERITY = {
  red:    { color: T.red,  label: "RED" },
  yellow: { color: T.gold, label: "YELLOW" },
};

// Anything the contract doesn't define renders visibly wrong rather than
// silently as normal — an unknown severity is a contract violation upstream
// and should look like one.
function severityOf(raw) {
  return SEVERITY[raw] || { color: T.purple, label: String(raw || "?").toUpperCase() };
}

function daysBetween(iso, now) {
  if (!iso) return null;
  const then = Date.parse(iso + (iso.length === 10 ? "T00:00:00Z" : ""));
  if (Number.isNaN(then)) return null;
  return Math.floor((now - then) / 86400000);
}

function ageLabel(since, now) {
  const d = daysBetween(since, now);
  if (d == null) return null;
  if (d <= 0) return "today";
  if (d === 1) return "1 day";
  return `${d} days`;
}

function dueLabel(due, now) {
  const d = daysBetween(due, now);
  if (d == null) return null;
  if (d < 0) return { text: `due in ${-d}d`, urgent: -d <= 2 };
  if (d === 0) return { text: "due today", urgent: true };
  return { text: `${d}d overdue`, urgent: true };
}

function createItemCard(item, index, now) {
  const sev = severityOf(item.severity);

  const card = el("div", {
    position: "relative",
    display: "flex",
    gap: "14px",
    padding: isNarrow ? "14px" : "16px 18px",
    background: T.panelBg,
    border: `1px solid ${T.panelBorder}`,
    borderLeft: `3px solid ${sev.color}`,
    borderRadius: "8px",
    marginBottom: "10px",
  });
  addHoverEffect?.(card);

  // Rank number. The order came from doctrine/rollup.md; showing it makes the
  // ranking legible rather than implicit, and makes a wrong order obvious.
  const rank = el("div", {
    flexShrink: "0",
    width: "22px",
    fontSize: "18px",
    fontWeight: "600",
    lineHeight: "1.2",
    color: sev.color,
    fontVariantNumeric: "tabular-nums",
  }, String(index + 1));
  card.appendChild(rank);

  const body = el("div", { flex: "1", minWidth: "0" });
  card.appendChild(body);

  // ── Meta line: severity, stack, category ──
  const meta = el("div", {
    display: "flex",
    flexWrap: "wrap",
    alignItems: "center",
    gap: "8px",
    marginBottom: "6px",
  });
  body.appendChild(meta);

  meta.appendChild(el("span", {
    fontSize: "10px",
    fontWeight: "700",
    letterSpacing: "0.08em",
    color: sev.color,
  }, sev.label));

  if (item.source_stack) {
    meta.appendChild(el("span", {
      fontSize: "10px",
      letterSpacing: "0.06em",
      color: T.textMuted,
      textTransform: "uppercase",
    }, item.source_stack));
  }

  // Category is free text by contract — whatever the source stack calls this
  // kind of thing. Never map it to a fixed vocabulary here; that's the whole
  // point of the contract.
  if (item.category) {
    meta.appendChild(el("span", {
      fontSize: "10px",
      color: T.textDim,
      padding: "1px 6px",
      border: `1px solid ${T.panelBorder}`,
      borderRadius: "3px",
    }, item.category));
  }

  // ── Summary ──
  body.appendChild(el("div", {
    fontSize: isNarrow ? "13px" : "14px",
    lineHeight: "1.45",
    color: T.text,
    marginBottom: "8px",
  }, item.summary || "(no summary)"));

  // ── Footer: age, due, source ──
  const footer = el("div", {
    display: "flex",
    flexWrap: "wrap",
    alignItems: "center",
    gap: "10px",
    fontSize: "11px",
    color: T.textMuted,
  });
  body.appendChild(footer);

  const age = ageLabel(item.since, now);
  if (age) footer.appendChild(el("span", {}, `open ${age}`));

  const due = dueLabel(item.due, now);
  if (due) {
    footer.appendChild(el("span", {
      color: due.urgent ? sev.color : T.textMuted,
      fontWeight: due.urgent ? "600" : "400",
    }, due.text));
  }

  if (item.source) {
    footer.appendChild(el("span", {
      color: T.textDim,
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
      maxWidth: isNarrow ? "100%" : "340px",
    }, item.source));
  }

  return card;
}

return { createItemCard };
