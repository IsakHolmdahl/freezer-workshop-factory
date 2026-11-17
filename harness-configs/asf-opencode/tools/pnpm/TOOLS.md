### pnpm

- pnpm 12 on Node 22 (`pnpm`, `node`, `npm`).
- Use the version in `package.json` `packageManager` when it differs: `npx pnpm@<version> <command>`.
- Shared store: `NPM_CONFIG_STORE_DIR` (and the npm cache) point at the project build cache, so installs are cached across agents.
