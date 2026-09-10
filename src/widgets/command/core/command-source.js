// Command Source — fetches facts/command.json from the orchestrator repo.
// Shape contract: Dev-Work/specs/status_item_record.md (items), ranked and
// capped by the orchestrator's doctrine/rollup.md.
// Returns: { fetchCommand }

const { createSource } = await (await ctx.loadModule("services/orchestrator-file.js"))(ctx);

function validate(parsed) {
  if (!parsed || !Array.isArray(parsed.items)) return "command.json has no items array";
  return null;
}

const { fetchFile } = createSource(ctx, "command", "facts/command.json", validate);

return { fetchCommand: fetchFile };
