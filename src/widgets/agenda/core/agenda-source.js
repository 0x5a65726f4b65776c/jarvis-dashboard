// Agenda Source — fetches facts/agenda.json from the orchestrator repo.
// Shape contract: Dev-Work/specs/agenda_record.md
// Returns: { fetchAgenda }

const { createSource } = await (await ctx.loadModule("services/orchestrator-file.js"))(ctx);

// Validation is deliberately shallow: it checks the two arrays the renderer
// indexes into, and nothing else. Rejecting a file for a missing optional
// field would turn a partial agenda into no agenda, which is the worse
// outcome -- the spec already says the rollup omits what it does not know.
function validate(parsed) {
  if (!parsed || typeof parsed !== "object") return "agenda.json is not an object";
  if (!Array.isArray(parsed.events)) return "agenda.json has no events array";
  if (!Array.isArray(parsed.todos)) return "agenda.json has no todos array";
  return null;
}

const { fetchFile } = createSource(ctx, "agenda", "facts/agenda.json", validate);

return { fetchAgenda: fetchFile };
