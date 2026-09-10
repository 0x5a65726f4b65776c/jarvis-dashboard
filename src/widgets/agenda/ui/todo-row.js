// Agenda Todo Row — renders one thing owed today.
// Renders only. No checkbox: items are marked done where the work lives, and
// the next rollup reflects it. That is a deliberate scope decision recorded in
// Dev-Work/specs/agenda_record.md, not an unfinished feature.
// Returns: { createTodoRow }

const { el, T, isNarrow, addHoverEffect } = ctx;

// Same closed vocabulary as the Status Item Record, and gold rather than
// orange for the same reason the Command card uses it: T.orange sits too
// close to T.red on this theme to be distinguishable at a glance.
const SEVERITY = {
  red:    T.red,
  yellow: T.gold,
};

// Calendar-date arithmetic, not instant arithmetic. See core/day-math.js for
// the bug this avoids -- an item due tomorrow rendering as "today".
const { calendarDaysUntil } = await (await ctx.loadModule("core/day-math.js"))(ctx);

function dueLabel(due, now) {
  if (!due) return null;
  const d = calendarDaysUntil(due, now);
  if (d == null) return null;
  if (d < 0) return { text: `${-d}d overdue`, urgent: true };
  if (d === 0) return { text: "today", urgent: true };
  if (d === 1) return { text: "tomorrow", urgent: true };
  return { text: `in ${d}d`, urgent: false };
}

function createTodoRow(todo, now) {
  // Severity is optional by contract -- the rollup omits it rather than
  // inventing one. An item without it renders in the neutral accent, which is
  // the honest reading: it is owed, and nobody graded it.
  const color = SEVERITY[todo.severity] || T.accentDim;

  const row = el("div", {
    display: "flex",
    gap: "12px",
    padding: isNarrow ? "10px 12px" : "11px 14px",
    background: T.panelBg,
    border: `1px solid ${T.panelBorder}`,
    borderLeft: `3px solid ${color}`,
    borderRadius: "8px",
    marginBottom: "8px",
  });
  addHoverEffect?.(row);

  const body = el("div", { flex: "1", minWidth: "0" });
  row.appendChild(body);

  body.appendChild(el("div", {
    fontSize: isNarrow ? "13px" : "14px",
    lineHeight: "1.45",
    color: T.text,
    marginBottom: "5px",
  }, todo.text || "(no description)"));

  const footer = el("div", {
    display: "flex",
    flexWrap: "wrap",
    alignItems: "center",
    gap: "10px",
    fontSize: "11px",
    color: T.textMuted,
  });
  body.appendChild(footer);

  const due = dueLabel(todo.due, now);
  if (due) {
    footer.appendChild(el("span", {
      color: due.urgent ? color : T.textMuted,
      fontWeight: due.urgent ? "600" : "400",
    }, due.text));
  }

  if (todo.source) {
    footer.appendChild(el("span", {
      color: T.textDim,
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
      maxWidth: isNarrow ? "100%" : "320px",
    }, todo.source));
  }

  return row;
}

return { createTodoRow };
