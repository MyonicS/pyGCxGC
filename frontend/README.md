# pyGCxGC Studio frontend

React + TypeScript app served by the Python backend (`pyGCxGC.app`). The
specification is in [`specs/app.md`](../specs/app.md).

```bash
pnpm install
pnpm build          # writes ../src/pyGCxGC/app/static
cd .. && .venv/bin/python -m pyGCxGC.app --open
```

Development with hot reload: start the backend with a fixed port and token,
`python -m pyGCxGC.app --port 8765 --token dev`, then `pnpm dev` and open
<http://localhost:5173/?token=dev>.

| Command                       |                                                                                    |
| ----------------------------- | ---------------------------------------------------------------------------------- |
| `pnpm lint`, `pnpm typecheck` | ESLint + Prettier, strict TypeScript                                               |
| `pnpm test`                   | Vitest: unit tests and the shader tests in Chromium                                |
| `pnpm test:e2e`               | Playwright against the built app (both themes, screenshots)                        |
| `pnpm gen:api`                | TypeScript types from `openapi.json` (`python ../scripts/export_openapi.py` first) |

`CHROMIUM_PATH` selects a system Chromium for the browser tests; `PYTHON`
selects the interpreter of the end-to-end backend.
