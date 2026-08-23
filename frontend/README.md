# frontend

Nothing here is written by hand yet. The Vite + React + FSD application of
§9.3 arrives with plans 7 and 8; until then this tree holds exactly one
file, and that file is generated:

```
src/shared/api/contracts.ts    generated — do not edit
```

Regenerate it from the Pydantic models with:

```bash
cd backend && podvinsya export-types
```

It lives at the FSD `shared/` layer because §9.3 puts both surfaces — the
stage screen and the host console — in one application sharing the
generated types. CI runs `podvinsya export-types --check` on every push, so
a model changed without regenerating fails the build rather than reaching
the front end as a type that quietly disagrees with the server (§7.6, §11).
