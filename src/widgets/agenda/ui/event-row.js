// Agenda Event Row — renders one calendar event.
// Renders only. It does not sort, drop past events, merge duplicates, or
// derive an end time -- the rollup wrote the list and the order.
// Returns: { createEventRow }

const { el, T, isNarrow, addHoverEffect } = ctx;

// An all-day event carries a bare YYYY-MM-DD rather than an instant, because
// it does not have one. Forcing a timezone onto it is what produces the
// classic off-by-one-day bug at either end of the day.
function isAllDay(start) {
  return typeof start === "string" && start.length === 10;
}

function parseInstant(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

// Rendered in the viewer's local time, from a UTC instant. The file carries
// instants precisely so this conversion happens once, at the edge.
function clockLabel(iso) {
  const t = parseInstant(iso);
  if (t == null) return null;
  return new Date(t).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function timeLabel(event) {
  if (isAllDay(event.start)) return { primary: "All day", secondary: null };
  const start = clockLabel(event.start);
  if (!start) return { primary: "—", secondary: null };
  return { primary: start, secondary: event.end ? clockLabel(event.end) : null };
}

// "Past" is a presentation question, not a membership question: the event
// stays on the list all day (spec rule 5) and simply reads as done. An event
// with no end time is never treated as past, because we cannot know.
function isPast(event, now) {
  if (isAllDay(event.start)) return false;
  const end = parseInstant(event.end);
  if (end == null) return false;
  return end < now;
}

// In progress is worth its own treatment: it is the single most useful thing
// the panel can tell you at a glance when you open it mid-morning.
function isNow(event, now) {
  if (isAllDay(event.start)) return false;
  const start = parseInstant(event.start);
  const end = parseInstant(event.end);
  if (start == null || end == null) return false;
  return start <= now && now < end;
}

function createEventRow(event, now) {
  const past = isPast(event, now);
  const live = isNow(event, now);
  const accent = live ? T.accent : past ? T.textDim : T.purple;

  const row = el("div", {
    display: "flex",
    gap: isNarrow ? "10px" : "14px",
    padding: isNarrow ? "10px 12px" : "11px 14px",
    background: live ? T.accentFaint : T.panelBg,
    border: `1px solid ${live ? T.accentDim : T.panelBorder}`,
    borderLeft: `3px solid ${accent}`,
    borderRadius: "8px",
    marginBottom: "8px",
    opacity: past ? "0.5" : "1",
  });
  addHoverEffect?.(row);

  // ── Time column ──
  const time = timeLabel(event);
  const timeCol = el("div", {
    flexShrink: "0",
    width: isNarrow ? "58px" : "68px",
    textAlign: "right",
    fontVariantNumeric: "tabular-nums",
  });
  timeCol.appendChild(el("div", {
    fontSize: isNarrow ? "12px" : "13px",
    fontWeight: "600",
    color: live ? T.accent : T.text,
    lineHeight: "1.3",
  }, time.primary));
  if (time.secondary) {
    timeCol.appendChild(el("div", {
      fontSize: "10px",
      color: T.textMuted,
      lineHeight: "1.3",
    }, time.secondary));
  }
  row.appendChild(timeCol);

  const body = el("div", { flex: "1", minWidth: "0" });
  row.appendChild(body);

  // ── Title ──
  const titleLine = el("div", {
    display: "flex",
    alignItems: "baseline",
    flexWrap: "wrap",
    gap: "8px",
    marginBottom: "3px",
  });
  titleLine.appendChild(el("div", {
    fontSize: isNarrow ? "13px" : "14px",
    fontWeight: "500",
    color: T.text,
    lineHeight: "1.4",
  }, event.title || "(untitled)"));

  if (live) {
    titleLine.appendChild(el("span", {
      fontSize: "9px",
      fontWeight: "700",
      letterSpacing: "0.08em",
      color: T.accent,
      padding: "1px 5px",
      border: `1px solid ${T.accentDim}`,
      borderRadius: "3px",
    }, "NOW"));
  }
  body.appendChild(titleLine);

  // ── Meta: where, with ──
  const bits = [];
  if (event.where) bits.push(event.where);
  // Attendees are rendered as the calendar records them. Never inferred, never
  // reconciled against a contacts file -- that would be deciding.
  if (Array.isArray(event.with) && event.with.length) bits.push(event.with.join(", "));

  if (bits.length) {
    body.appendChild(el("div", {
      fontSize: "11px",
      color: T.textMuted,
      lineHeight: "1.4",
    }, bits.join("  ·  ")));
  }

  // A prep pointer is only ever a reference to a brief that already exists.
  // The contract forbids it being a promise of one.
  if (event.prep) {
    body.appendChild(el("div", {
      fontSize: "10px",
      color: T.textDim,
      marginTop: "3px",
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    }, `prep: ${event.prep}`));
  }

  return row;
}

return { createEventRow };
