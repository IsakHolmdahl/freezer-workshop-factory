---
name: structural-code-search
description: >-
  Use before searching or editing source code: when to use ast-grep instead of
  grep/find/sed or hand edits, with ready patterns and a fallback rule.
  Trigger: finding callers, definitions, imports, usages, or renaming/rewriting
  a construct in more than one place.
---

# Structural code search and edit

`ast-grep` (also `sg`) matches syntax, not text. It ignores comments, strings
and formatting, and matches across line breaks. Reach for it first for code;
use `grep`/`rg` only for plain text (logs, config values, prose, unknown language).

```sh
ast-grep run -p '<pattern>' [-l <lang>] [paths...]            # search
ast-grep run -p '<pattern>' -r '<rewrite>' [paths...]          # preview a rewrite (diff, changes nothing)
ast-grep run -p '<pattern>' -r '<rewrite>' -U [paths...]       # apply it
```

Quote patterns with single quotes so the shell leaves `$NAME` alone.

## Decide in five seconds

| You want to... | Use |
|---|---|
| Find callers/usages of a function or method | `ast-grep run -p 'foo($$$ARGS)'` / `'$OBJ.foo($$$ARGS)'` |
| Find a declaration | `ast-grep run -p 'function $NAME($$$ARGS) { $$$BODY }'` |
| Find imports of a module | `ast-grep run -p 'import $$$X from "mod"'` |
| Check a construct is absent/present (review, audit) | `ast-grep run -p ... --json=compact`, count matches |
| Change the same construct in 2+ places | `ast-grep run -p ... -r ...`, read the diff, then add `-U` |
| Change one spot in one file | normal `edit` |
| Search a literal string, log line, config key | `grep` |

## Patterns

- `$NAME` one node, `$_` any node (unbound), `$$$ARGS` zero or more nodes. Names UPPERCASE.
- A pattern must itself be valid code in the language. Set `-l <lang>` when unsure
  (`java`, `python`, `typescript`, `tsx`, `javascript`, `go`, `rust`, ...).
- Reusing a metavariable requires identical subtrees (`$A == $A`).
- Narrow the paths (`src tests`); pipe long results through `head`.

Recipes:

- Java: `$OBJ.getFoo()` ; `public $T $NAME($$$P) { $$$B }` (methods).
- Python: `def $NAME($$$ARGS): $$$BODY` ; `requests.$M($$$A)` ; `except: $$$B`.
- TS/JS: `console.log($$$A)` ; `await $F($$$A)` ; `useEffect($$$A)`.
- Security audit: `eval($X)`, `exec($CMD)`, `$DB.query($Q + $Y)`, `new Random()`.

## Editing

1. Run the search with the pattern and read the matches.
2. Add `-r '<rewrite>'`; check the printed diff is only what you intend.
3. Run it again with `-U`, then build/test. One concern per rewrite.

Example: `ast-grep run -p 'oldApi($A, $B)' -r 'newApi($B, $A)' -l java src`.

## Fallback

If a pattern fails to parse or returns nothing unexpectedly, simplify it once
(fewer nodes, set `-l`). Still nothing: use `grep` and carry on; do not spend
more than two attempts.
