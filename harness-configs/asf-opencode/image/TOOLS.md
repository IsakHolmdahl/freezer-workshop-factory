### Base tools

- `node`, `npm`, `python3`, `git`, `gh`, `bd` (Beads), `dolt`, `rtk`, `rg`, `fd`, `ast-grep` (`sg`).
- Build caches shared by all agents of the project: `$ASF_BUILD_CACHE`
  (Gradle, pnpm, npm, pip, uv are already configured to use it).
- When `$DOCKER_HOST` is set (`/run/testcontainers/docker.sock`), Testcontainers
  integration tests work automatically against the host's rootless Docker daemon.
- Only the tools listed in this section exist in this image. If a build needs
  something else, do not install it ad hoc: record it (`bd remember`) and report
  it as a blocker so the right image variant can be chosen.
