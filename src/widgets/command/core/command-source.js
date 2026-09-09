// Command Source — fetches facts/command.json from the orchestrator repo.
// Reads GitHub directly from the client. There is no companion server in this
// path by design: the rollup writes the file, GitHub stores it, the widget
// reads it. Nothing else runs.
// Returns: { fetchCommand }

const cfg = ctx.config?.widgets?.command || {};
const localCfg = ctx._localConfig?.command || {};

const repo = cfg.repo || "";
const path = cfg.path || "facts/command.json";
const ref = cfg.ref || "main";
const token = localCfg.githubToken || "";

// Distinguishes "the file says nothing needs attention" from "we could not ask."
// Collapsing those two is the failure this whole pipeline exists to avoid, so
// the source layer keeps them as different shapes and never invents an empty
// item list to paper over an error.
function ok(data) { return { state: "ok", data }; }
function problem(kind, detail) { return { state: "problem", kind, detail }; }

async function fetchCommand() {
  if (!repo) {
    return problem("unconfigured", "widgets.command.repo is not set in config.json");
  }
  if (!token) {
    return problem("unconfigured", "command.githubToken is not set in config.local.json");
  }

  const url = `https://api.github.com/repos/${repo}/contents/${path}?ref=${encodeURIComponent(ref)}`;

  let res;
  try {
    res = await fetch(url, {
      headers: {
        // raw gives us the file body directly; no base64 round-trip
        "Accept": "application/vnd.github.raw+json",
        "Authorization": `Bearer ${token}`,
        "X-GitHub-Api-Version": "2022-11-28",
      },
      cache: "no-store",
    });
  } catch (e) {
    return problem("offline", e?.message || "network request failed");
  }

  if (res.status === 401 || res.status === 403) {
    return problem("auth", `GitHub returned ${res.status} — token missing, expired, or lacking repo scope`);
  }
  if (res.status === 404) {
    return problem("missing", `${repo}/${path}@${ref} not found — wrong repo, path, or the token cannot see this private repo`);
  }
  if (!res.ok) {
    return problem("http", `GitHub returned ${res.status}`);
  }

  let parsed;
  try {
    parsed = JSON.parse(await res.text());
  } catch (e) {
    return problem("malformed", "command.json is not valid JSON");
  }

  if (!parsed || !Array.isArray(parsed.items)) {
    return problem("malformed", "command.json has no items array");
  }

  return ok(parsed);
}

return { fetchCommand };
