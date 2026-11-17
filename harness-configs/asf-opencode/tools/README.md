# Tool layers

Each folder is an optional layer on top of the `asf-opencode` image. Variants are
named after the layers they contain (`asf-opencode-pnpm-python`, ...) and built by
`scripts/build-harness.py --tools pnpm,python`.

Add a tool:

1. `mkdir tools/<name>` with a `Dockerfile` that starts
   `ARG BASE_IMAGE` / `FROM ${BASE_IMAGE}` / `USER root`, installs the tool
   (pin versions, verify checksums), and ends with a version check.
2. Add a `# provides: <command> ...` line: these commands become the variant's
   `required_image_tools`, so Scion refuses an image that lacks them.
3. Write `TOOLS.md`: short, agent-facing notes (versions, how to pick the
   right version, caches, pitfalls) and `COPY TOOLS.md /etc/scion-tools/<name>.md`.
   Every agent gets all `/etc/scion-tools/*.md` in its instructions at start.
4. `python3 scripts/build-harness.py --tools <name>` and start an agent with
   `--harness-config asf-opencode-<name>`.

`build-harness.py` refuses a layer without `# provides:` or `TOOLS.md`.
Keep tool-specific environment (PATH, defaults) in the Dockerfile `ENV`, not in
templates.
