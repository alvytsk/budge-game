# budge — front end

Vite + React + Feature-Sliced Design (§9.3). Two surfaces in one
application, sharing one set of generated types: the stage screen at
`/stage/:token` and the host console at `/host`.

## Running

```bash
pnpm install
pnpm dev          # proxies /api and /ws to http://127.0.0.1:8000
```

Point the proxy elsewhere with `BUDGE_BACKEND=http://host:port pnpm dev`.

## Checks

```bash
pnpm check        # biome + tsc + steiger
pnpm test         # vitest
pnpm build        # tsc + vite build
```

## Generated files — never edit by hand

| File | Written by |
| --- | --- |
| `src/shared/api/contracts.ts` | `cd backend && podvinsya export-types` |
| `src/app/routes/routeTree.gen.ts` | the TanStack Router Vite plugin |

CI runs `podvinsya export-types --check`, so a server model changed
without regenerating fails the build rather than reaching the front end as
a type that quietly disagrees with the server (§7.6, §11).
