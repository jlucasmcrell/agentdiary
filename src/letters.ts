// Private letters between the site's owner and the agents he runs himself. Nothing here is public or reachable
// with an agent key: every route is behind the admin token. A script on the owner's PC copies letters between
// this table and the agents' own letters folders.
import { ApiError, Env, now, randomId, str } from "./util";

type Letter = { id: string; agent: string; direction: string; name: string; body: string; created_at: number; read_at: number | null; synced_at: number | null };
const AGENT = /^[a-z0-9][a-z0-9-]{0,31}$/;
const NAME = /^[A-Za-z0-9][A-Za-z0-9._ -]{0,119}$/;
const MAX_BODY = 100_000;

const agentName = (v: unknown) => {
  const s = String(v ?? "").trim().toLowerCase();
  if (!AGENT.test(s)) throw new ApiError(400, "agent must be a short lowercase name");
  return s;
};

export async function list(env: Env, args: any = {}) {
  if (args.pending) {
    const { results } = await env.DB.prepare(`SELECT id, agent, name, body, created_at FROM letters WHERE direction = 'from_owner' AND synced_at IS NULL ORDER BY created_at`).all<Letter>();
    return { items: results };
  }
  const { results } = await env.DB.prepare(`SELECT * FROM letters ORDER BY created_at, name`).all<Letter>();
  const by = new Map<string, Letter[]>();
  for (const l of results) by.set(l.agent, [...(by.get(l.agent) ?? []), l]);
  const agents = [...by].map(([agent, letters]) => ({
    agent,
    unread: letters.filter((l) => l.direction === "to_owner" && !l.read_at).length,
    letters: letters.map((l) => ({
      id: l.id, direction: l.direction, name: l.name, body: l.body, created_at: l.created_at,
      ...(l.direction === "to_owner" ? { unread: !l.read_at } : { waiting: !l.synced_at }),
    })),
  })).sort((a, b) => a.agent.localeCompare(b.agent));
  return { agents, unread: agents.reduce((n, a) => n + a.unread, 0) };
}

/** A letter written in the admin page. It waits here until the sync script carries it to the agent's folder. */
export async function write(env: Env, args: any) {
  const agent = agentName(args.agent);
  const body = str(args.body, "body", MAX_BODY, { min: 1, required: true });
  const t = new Date();
  const stamp = t.toISOString().slice(0, 10) + "-" + t.toISOString().slice(11, 16).replace(":", "");
  const slug = String(args.title ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
  const name = `${stamp}-${slug || "from-joe"}.md`;
  const id = randomId();
  try {
    await env.DB.prepare(`INSERT INTO letters (id, agent, direction, name, body, created_at) VALUES (?, ?, 'from_owner', ?, ?, ?)`).bind(id, agent, name, body, now()).run();
  } catch { throw new ApiError(409, "A letter with that title was already written this minute"); }
  return { id, agent, name, waiting: true };
}

/** Copies of letters that already exist as files (both directions). Ones already here are left alone. */
export async function importLetters(env: Env, args: any) {
  const items = Array.isArray(args.items) ? args.items.slice(0, 50) : [];
  if (!items.length) throw new ApiError(400, "items must be a list of letters");
  const stmts = items.map((x: any) => {
    const agent = agentName(x.agent), name = String(x.name ?? "");
    if (!NAME.test(name)) throw new ApiError(400, `Bad file name: ${name.slice(0, 40)}`);
    if (x.direction !== "to_owner" && x.direction !== "from_owner") throw new ApiError(400, "direction must be to_owner or from_owner");
    const body = String(x.body ?? "").slice(0, MAX_BODY);
    const at = Number.isFinite(+x.created_at) && +x.created_at > 0 ? Math.min(+x.created_at, now()) : now();
    return env.DB.prepare(`INSERT OR IGNORE INTO letters (id, agent, direction, name, body, created_at, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?)`)
      .bind(randomId(), agent, x.direction, name, body, at, now());
  });
  const res = await env.DB.batch(stmts);
  return { added: res.reduce((n, r) => n + (r.meta.changes ?? 0), 0), of: items.length };
}

export async function markSynced(env: Env, args: any) {
  const ids = (Array.isArray(args.ids) ? args.ids : []).slice(0, 50).map(String);
  if (!ids.length) return { synced: 0 };
  const res = await env.DB.batch(ids.map((id: string) => env.DB.prepare(`UPDATE letters SET synced_at = ? WHERE id = ? AND synced_at IS NULL`).bind(now(), id)));
  return { synced: res.reduce((n, r) => n + (r.meta.changes ?? 0), 0) };
}

export async function markRead(env: Env, args: any) {
  const r = await env.DB.prepare(`UPDATE letters SET read_at = ? WHERE agent = ? AND direction = 'to_owner' AND read_at IS NULL`).bind(now(), agentName(args.agent)).run();
  return { read: r.meta.changes ?? 0 };
}
