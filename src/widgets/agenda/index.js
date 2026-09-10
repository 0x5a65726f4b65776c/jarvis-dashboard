// Agenda Widget — Orchestrator
// What today looks like and what is owed today, read from
// -jarvis-orchestrator/facts/agenda.json.
//
// THIS WIDGET RENDERS. IT DOES NOT DECIDE.
// The rules for what belongs on a day's agenda live once, in the orchestrator's
// doctrine, and the daily rollup applies them and writes the answer down. This
// file must never sort, filter, drop a past event, merge duplicates, cap, pad,
// or derive a due date. If the list looks wrong, the bug is upstream — fix it
// there so every reader gets the fix.
//
// There is no checkbox, by decision rather than omission: see
// Dev-Work/specs/agenda_record.md. Nick already has several task managers; a
// fourth one living inside a dashboard would be a list nobody updates.
// Returns: HTMLElement

const { el, T, config, isNarrow, createSectionTitle, registerPausable } = ctx;

const cfg = config.widgets?.agenda || {};
const REFRESH_MS = cfg.refreshMs || 900000;   // 15 min

const { fetchAgenda } = await (await ctx.loadModule("widgets/agenda/core/agenda-source.js"))(ctx);
const { createEventRow } = await (await ctx.loadModule("widgets/agenda/ui/event-row.js"))(ctx);
const { createTodoRow } = await (await ctx.loadModule("widgets/agenda/ui/todo-row.js"))(ctx);

const section = el("div", { position: "relative", zIndex: "2" });
section.appendChild(createSectionTitle("Today", { marginBottom: "6px" }));

const subtitle = el("div", {
  fontSize: "11px",
  color: T.textDim,
  marginBottom: "16px",
}, "Schedule and what is owed — published by the daily rollup");
section.appendChild(subtitle);

const body = el("div", {});
section.appendChild(body);

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function notice(text, color, detail) {
  const box = el("div", {
    padding: "16px 18px",
    background: T.panelBg,
    border: `1px solid ${T.panelBorder}`,
    borderLeft: `3px solid ${color}`,
    borderRadius: "8px",
  });
  box.appendChild(el("div", {
    fontSize: "13px",
    color: T.text,
    marginBottom: detail ? "6px" : "0",
  }, text));
  if (detail) {
    box.appendChild(el("div", { fontSize: "11px", color: T.textMuted, lineHeight: "1.5" }, detail));
  }
  return box;
}

function subhead(text) {
  return el("div", {
    fontSize: "10px",
    fontWeight: "700",
    letterSpacing: "0.1em",
    textTransform: "uppercase",
    color: T.textMuted,
    margin: "4px 0 8px",
  }, text);
}

// The viewer's local calendar date. Compared against for_date, which the
// contract defines in Nick's local timezone -- so this is the like-for-like
// comparison, and deliberately not a UTC one.
function localDateString(now) {
  const d = new Date(now);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// Staleness is measured against for_date, not generated_at. The failure that
// matters here is showing the WRONG DAY, and a rollup that ran late still
// produces a file whose for_date gives it away -- whereas a fresh
// generated_at on yesterday's agenda would read as current.
function renderStaleBanner(forDate, now) {
  if (!forDate) {
    return notice(
      "Agenda has no date",
      T.orange,
      "for_date is missing, so there is no way to tell which day this describes. Treat it as unverified."
    );
  }
  const today = localDateString(now);
  if (forDate === today) return null;

  return notice(
    `This agenda is for ${forDate}, not today`,
    T.red,
    `Today is ${today}. The daily rollup has not run for today, so what follows is a different day's schedule. Check the Jarvis daily rollup routine.`
  );
}

function render(result, now) {
  clear(body);

  if (result.state === "problem") {
    const copy = {
      unconfigured: ["Not configured", T.textMuted],
      auth:         ["GitHub rejected the token", T.red],
      missing:      ["agenda.json not found", T.red],
      offline:      ["Could not reach GitHub", T.orange],
      http:         ["GitHub error", T.orange],
      malformed:    ["agenda.json is malformed", T.red],
    }[result.kind] || ["Could not load", T.orange];

    body.appendChild(notice(copy[0], copy[1], result.detail));
    return;
  }

  const data = result.data;

  const stale = renderStaleBanner(data.for_date, now);
  if (stale) {
    stale.style.marginBottom = "14px";
    body.appendChild(stale);
  }

  // ── Schedule ──
  body.appendChild(subhead("Schedule"));
  if (data.events.length === 0) {
    // An empty day is a complete answer, not an error and not an empty state
    // to apologise for.
    body.appendChild(notice(
      "Nothing scheduled",
      T.green,
      "The calendar was read and today is clear."
    ));
  } else {
    // File order, always. The rollup wrote the order.
    data.events.forEach((event) => body.appendChild(createEventRow(event, now)));
  }

  // ── Owed today ──
  const todoHead = subhead("Owed today");
  todoHead.style.marginTop = "18px";
  body.appendChild(todoHead);

  if (data.todos.length === 0) {
    body.appendChild(notice(
      "Nothing owed today",
      T.green,
      "No item is due today, overdue, or due inside two days. Standing attention items are in Command."
    ));
  } else {
    data.todos.forEach((todo) => body.appendChild(createTodoRow(todo, now)));
  }

  const stamp = el("div", {
    marginTop: "12px",
    fontSize: "10px",
    color: T.textDim,
    textAlign: isNarrow ? "left" : "right",
  }, `rollup ${data.generated_at || "(no timestamp)"}`);
  body.appendChild(stamp);
}

body.appendChild(notice("Loading…", T.accentDim));

async function refresh() {
  const result = await fetchAgenda();
  render(result, Date.now());
}

refresh();

let timer = null;
function start() {
  if (timer) return;
  timer = setInterval(refresh, REFRESH_MS);
  ctx.intervals.push(timer);
}
function stop() {
  if (!timer) return;
  clearInterval(timer);
  timer = null;
}
start();
registerPausable?.(start, stop);
ctx.cleanups.push(stop);

return section;
