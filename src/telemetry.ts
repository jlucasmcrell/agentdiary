// The call log: what was called, by whom, how it went and how long it took. Never the content of a call.
import { Env } from "./util";

export function record(env: Env, row: { via: "rest" | "mcp"; action: string; status: number; error?: string | null; ms: number }) {
  const agentId = (env as any).AGENT_ID ?? null;   // set by core.authenticate when the call carried a valid key
  const p = env.DB.prepare(`INSERT INTO api_calls (at, agent_id, via, action, status, error, ms) VALUES (?, ?, ?, ?, ?, ?, ?)`)
    .bind(Date.now(), agentId, row.via, row.action.slice(0, 120), row.status, row.error ? row.error.slice(0, 200) : null, row.ms > 0 ? Math.round(row.ms) : null)   // 0: the clock didn't move (no network wait), so unknown
    .run().catch((e) => console.error("call log failed", String(e)));
  if (env.CTX) env.CTX.waitUntil(p);
}
