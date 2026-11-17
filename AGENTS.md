# Agent Instructions

## Know the project first

Read `docs/project.md` before changing anything: what ASF is, the layout, where to
change what and how changes reach agents. Then the doc for your area:
`docs/scion-primer.md` (Scion concepts), `docs/opencode-harness.md` (agent start and
configuration), `docs/templates-and-skills.md` (roles and skills),
`docs/agent-roles.md` (role behaviour and smoke tests). Do not restate these in
other files; link to them, and update them when you change how things work.

## Prefer popular libraries over custom implementations

Do not hand-write functionality that a well-established library already provides. Before writing any non-trivial utility or infrastructure code, look for an existing library first.

### Rules

1. **Search first.** Check the existing dependencies (`package.json`, lockfiles, `requirements.txt`, `go.mod`, etc.) for something that already solves the problem. Reuse it.
2. **Then look for a popular library.** If nothing is installed, pick a widely used, actively maintained one (high download count/stars, recent releases, good docs, permissive license). Prefer the de facto standard in the ecosystem.
3. **Only write it yourself when:**
   - The logic is trivial (a few lines, e.g. a single helper with no edge cases), or
   - No suitable library exists, or
   - Adding a dependency is clearly disproportionate (huge package for one tiny function), or
   - The user explicitly asks for a custom implementation.
4. **Explain the choice.** When adding a dependency, state briefly which library and why. When writing custom code instead, state why no library was used.
5. **Install properly.** Use the project's package manager (this repo uses `pnpm`) so the lockfile is updated. Never vendor or copy library code by hand.

### Typical areas where a library must be used

- Date/time handling, parsing, formatting
- Schema validation and config parsing (e.g. zod, pydantic)
- HTTP clients, retries, backoff
- CLI argument parsing
- Logging
- YAML/JSON/TOML/CSV/Markdown parsing
- Cryptography, hashing, auth, JWT (never roll your own)
- UUIDs, slugs, ID generation
- Concurrency limiting, queues, scheduling
- Testing, mocking, assertions
- Deep clone/merge, collection utilities

### Avoid

- Reimplementing parsers with regexes where a parser library exists.
- Writing your own retry/cache/rate-limit/queue logic.
- Pulling in obscure, unmaintained, or single-maintainer packages when a mainstream alternative exists.
