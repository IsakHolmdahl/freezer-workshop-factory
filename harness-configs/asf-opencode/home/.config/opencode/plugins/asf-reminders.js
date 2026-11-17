// ASF plugin for OpenCode: cancel this agent's pending `scion schedule` reminders
// when it wakes up (a new prompt arrives) and when it reports task_completed.
// The role templates rely on this: an agent only creates a reminder right
// before it goes `blocked`, and never has to clean up stale ones.
import { spawn } from 'node:child_process';

const CANCEL = `for id in $(scion --non-interactive --format json schedule list --status pending 2>/dev/null | node -e '
  let d = "";
  process.stdin.on("data", c => d += c);
  process.stdin.on("end", () => {
    try {
      for (const e of JSON.parse(d)?.events ?? []) {
        const p = typeof e.payload === "string" ? JSON.parse(e.payload || "{}") : (e.payload || {});
        if (!p.agentName || p.agentName === process.env.SCION_AGENT_NAME) console.log(e.id);
      }
    } catch {}
  });
'); do scion --non-interactive schedule cancel "$id" >/dev/null 2>&1 || true; done`;

export function isTaskCompleted(input, output) {
  const command = output?.args?.command ?? input?.args?.command;
  return input?.tool === 'bash' && typeof command === 'string'
    && /\bsciontool\s+status\s+task_completed\b/.test(command);
}

function cancelPendingReminders() {
  if (!process.env.SCION_AGENT_NAME) return;
  try {
    spawn('sh', ['-c', CANCEL], { stdio: 'ignore', detached: true }).unref();
  } catch {
    // Best-effort: never break the agent over a reminder.
  }
}

export const AsfReminders = async () => ({
  'chat.message': async () => {
    cancelPendingReminders();
  },
  'tool.execute.before': async (input, output) => {
    if (isTaskCompleted(input, output)) cancelPendingReminders();
  },
});
