// Command Widget — Orchestrator
// The top five things that need Nick's attention across every stack, read from
// -jarvis-orchestrator/facts/command.json.
//
// THIS WIDGET RENDERS. IT DOES NOT DECIDE.
// The ranking rule lives once, in the orchestrator's doctrine/rollup.md, and the
// daily rollup applies it and writes the answer down. This file must never sort,
// filter, re-rank, cap, pad, or derive a severity. If the order looks wrong, the
// bug is in doctrine, not here — fix it there so every reader gets the fix.
// Returns: HTMLElement

const { el, T, config, isNarrow, createSectionTitle, registerPausable } = ctx;

const cfg = config.widgets?.command || {};
const REFRESH_MS = cfg.refreshMs || 900000;          // 15 min
const STALE_HOURS = cfg.staleAfterHours || 26;       // a daily rollup + 2h grace

// Sub-modules load via ctx.loadModule rather than ctx.nodeFs, so this widget runs
// in mobile mode as well as full. nodeFs only exists in full mode, and a widget
// that reaches for it is silently desktop-only.
const { fetchCommand } = await (await ctx.loadModule("widgets/command/core/command-source.js"))(ctx);
const { createItemCard } = await (await ctx.loadModule("widgets/command/ui/item-card.js"))(ctx);

const section = el("div", { position: "relative", zIndex: "2" });
section.appendChild(createSectionTitle("Command", { marginBottom: "6px" }));

// Subtitle carries the contract in one line, so anyone reading the dashboard
// knows an empty list is an answer rather than a missing feature.
const subtitle = el("div", {
  fontSize: "11px",
  color: T.textDim,
  marginBottom: "16px",
}, "Ranked by the daily rollup — at most five, never padded");
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

function hoursSince(iso, now) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return (now - t) / 3600000;
}

function renderStaleBanner(generatedAt, now) {
  const age = hoursSince(generatedAt, now);
  if (age == null) {
    return notice(
      "Rollup timestamp unreadable",
      T.orange,
      "generated_at is missing or malformed, so this list's age cannot be established. Treat it as unverified."
    );
  }
  if (age <= STALE_HOURS) return null;

  const days = Math.floor(age / 24);
  const label = days >= 1 ? `${days} day${days === 1 ? "" : "s"}` : `${Math.floor(age)} hours`;
  // A stale list rendered as current is the exact failure mode this whole
  // pipeline was built to avoid, so it gets a banner rather than a subtitle.
  return notice(
    `Stale — last rollup was ${label} ago`,
    T.red,
    "The daily rollup has not run since then, so what follows is history, not the current picture. Check the Jarvis daily rollup routine."
  );
}

function render(result, now) {
  clear(body);

  if (result.state === "problem") {
    const copy = {
      unconfigured: ["Not configured", T.textMuted],
      auth:         ["GitHub rejected the token", T.red],
      missing:      ["command.json not found", T.red],
      offline:      ["Could not reach GitHub", T.orange],
      http:         ["GitHub error", T.orange],
      malformed:    ["command.json is malformed", T.red],
    }[result.kind] || ["Could not load", T.orange];

    body.appendChild(notice(copy[0], copy[1], result.detail));
    return;
  }

  const data = result.data;
  const stale = renderStaleBanner(data.generated_at, now);
  if (stale) {
    stale.style.marginBottom = "12px";
    body.appendChild(stale);
  }

  if (data.items.length === 0) {
    // Empty is a real, complete answer — not an error and not an empty state to
    // apologise for. Every stack reported and none of them flagged anything.
    body.appendChild(notice(
      "Nothing flagged",
      T.green,
      "Every stack reported and none raised a red or yellow item. This is the whole answer, not a missing one."
    ));
  } else {
    data.items.forEach((item, i) => body.appendChild(createItemCard(item, i, now)));
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
  const result = await fetchCommand();
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
