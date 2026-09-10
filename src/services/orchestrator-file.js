// Orchestrator File Source — fetches one JSON file from the orchestrator repo.
//
// Reads GitHub directly from the client. There is no companion server in this
// path by design: the daily rollup writes the file, GitHub stores it, the
// widget reads it. Nothing else runs.
//
// Shared by the Command and Agenda widgets. Both read a rollup-written JSON
// file out of the same private repo with the same token, so the auth handling
// and the six failure shapes live here once. A second copy would drift, and
// the drift would show up as one widget reporting "offline" where the other
// reports "auth" for the same 403.
// Returns: { createSource }

// Distinguishes "the file says nothing" from "we could not ask." Collapsing
// those two is the failure this whole pipeline exists to avoid, so the source
// layer keeps them as different shapes and never invents empty data to paper
// over an error.
function ok(data) { return { state: "ok", data }; }
function problem(kind, detail) { return { state: "problem", kind, detail }; }

// `widgetKey` selects the config block; repo and token both fall back to the
// Command widget's settings, because every orchestrator file lives in the same
// repo behind the same token. Requiring a second copy of the same PAT would be
// a setup step that exists only to be got wrong.
function createSource(ctx, widgetKey, defaultPath, validate) {
  const cfg = ctx.config?.widgets?.[widgetKey] || {};
  const commandCfg = ctx.config?.widgets?.command || {};
  const local = ctx._localConfig || {};

  const repo = cfg.repo || commandCfg.repo || "";
  const path = cfg.path || defaultPath;
  const ref = cfg.ref || commandCfg.ref || "main";
  const token = local.orchestrator?.githubToken || local.command?.githubToken || "";

  async function fetchFile() {
    if (!repo) {
      return problem("unconfigured", `widgets.${widgetKey}.repo is not set in config.json (and widgets.command.repo is not set either)`);
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
      return problem("malformed", `${path} is not valid JSON`);
    }

    const complaint = validate(parsed);
    if (complaint) return problem("malformed", complaint);

    return ok(parsed);
  }

  return { fetchFile };
}

return { createSource };
