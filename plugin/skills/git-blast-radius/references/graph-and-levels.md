# Blast radius reference

## Node kinds
`changed` (depth 0), `dependant` (imports/mentions a changed file), `test`, `interface` (route/handler/webhook/API schema), `schema` (migration/SQL/proto/graphql/openapi), `config`, `infra` (CI, Docker, terraform, k8s...).

## Edge sources
- JS/TS: `import`, `export ... from`, `require`, dynamic `import('literal')`, tsconfig/jsconfig `baseUrl` and `paths`, `index.*` barrels, extension resolution, `./x.js` mapped to `x.ts`.
- Python: `import`, `from x import y`, relative imports, packages (`__init__.py`).
- Other languages: filename-stem text search (`git grep`, with a bounded pure-Python fallback). Heuristic (`confidence: text-match`).
- Config/infra files that mention a changed path (CI workflows, Dockerfiles, manifests): `text-match`.
- Tests: naming convention (`test_x.py`, `x.test.ts`, `__tests__/`) plus tests that import the changed module (transitively); `test_paths` from `.claude/git-warp.local.md` also count as tests.

## Level rules (deterministic)
- major drivers: webhook handler touched; schema/migration touched; widely used module (>=10 direct dependants across >=3 modules); sensitive path changed with no tests found; deleted file still referenced.
- minor drivers: route/handler touched; config/infra/CI touched; no tests found for changed source; lockfile changed; public module (>=3 dependants across >=2 modules, or >=8); sensitive path changed.
- HIGH if >=2 major, or >=1 major and >=2 drivers total, or >=4 drivers. MEDIUM if >=1 driver. LOW otherwise.

## Limits
Files over 1 MB, generated/vendored directories and binaries are skipped. Depth defaults to 3, nodes to 200, runtime to 15 s; `truncated` says what was cut.
