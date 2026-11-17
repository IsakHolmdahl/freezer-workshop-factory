---
name: output-discipline
description: >-
  How every agent writes chat output, reports and handoffs: terse, no narration,
  references instead of echoed content, without losing correctness.
---

# Output Discipline

Be terse. Output tokens are expensive; thinking and tool calls are not restricted.
Nobody reads your terminal: text written there is wasted. Everything a human or agent
must see goes through `scion message` or `bd` comments.

- Do not narrate plans or restate the task. Act, then report.
- No preambles, summaries of what you are about to do, or closing pleasantries.
- Between tool calls, write nothing unless a decision needs explaining (one line).
- Do not echo file contents, diffs, command output or skill text back; cite paths
  and line numbers instead. Quote only the lines that matter.
- Final reports and handoffs (sent with `scion message`, never as a terminal reply):
  result, evidence references, blockers, next action. Short bullets, no headings for
  small reports. After `sciontool status task_completed`, stop without a text reply.
- Do not repeat information already recorded in a Beads comment or earlier message;
  reference it.
- Never trade correctness for brevity: still run required checks, preserve exact
  identifiers, errors and acceptance evidence, and state uncertainty plainly.
