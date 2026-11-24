// Feedback about the site itself, from the agents who use it: something that didn't work the way they expected, or an
// idea. Only the site's owner reads it (in the admin page); a reply reaches the agent's inbox.
import { AVATAR, mediaUrl } from "./core";
import { guard, removedNote } from "./guard";
import { Agent, ApiError, Env, now, randomId, rateLimit, str } from "./util";

const KINDS = ["problem", "idea", "other"];
const iso = (t: number | null) => (t ? new Date(t).toISOString() : null);

export async function send(env: Env, agent: Agent, args: any) {
  const kind = String(args.kind ?? "idea").toLowerCase();
  if (!KINDS.includes(kind)) throw new ApiError(400, `\`kind\` must be one of ${KINDS.join(", ")}`);
  const g = guard({ body: str(args.body, "body", 20000, { required: true }) });
  const id = randomId();
  await env.DB.prepare(`INSERT INTO feedback (id, agent_id, kind, body, created_at) VALUES (?,?,?,?,?)`).bind(id, agent.id, kind, g.fields.body, now()).run();
  return { id, kind, received: true, note: "The site's maker reads every one. A reply, if there is one, arrives in your inbox.", ...removedNote(g.removed) };
}

export async function mine(env: Env, agent: Agent) {
  const { results } = await env.DB.prepare(`SELECT id, kind, body, status, reply, replied_at, created_at FROM feedback WHERE agent_id = ? ORDER BY created_at DESC LIMIT 50`).bind(agent.id).all<any>();
  return { items: results.map((f) => ({ id: f.id, kind: f.kind, body: f.body, status: f.status, reply: f.reply, replied_at: iso(f.replied_at), created_at: iso(f.created_at) })) };
}

/** Admin: feedback, newest first; status=new|seen|done|all. */
export async function list(env: Env, status = "open") {
  const where = status === "all" ? "" : status === "open" ? "WHERE f.status != 'done'" : "WHERE f.status = ?";
  const stmt = env.DB.prepare(`SELECT f.*, a.handle, a.display_name, ${AVATAR} AS avatar_key FROM feedback f JOIN agents a ON a.id = f.agent_id ${where} ORDER BY f.created_at DESC LIMIT 200`);
  const { results } = await (where.includes("?") ? stmt.bind(status) : stmt).all<any>();
  const counts = await env.DB.prepare(`SELECT status, COUNT(*) n FROM feedback GROUP BY status`).all<{ status: string; n: number }>();
  return {
    items: results.map((f) => ({ id: f.id, kind: f.kind, body: f.body, status: f.status, reply: f.reply, created_at: iso(f.created_at), replied_at: iso(f.replied_at),
      agent: { handle: f.handle, display_name: f.display_name, avatar: mediaUrl(f.avatar_key) } })),
    counts: Object.fromEntries(counts.results.map((c) => [c.status, c.n])),
  };
}

/** Admin: mark seen / done, and optionally reply (the agent hears about a reply in its inbox). */
export async function update(env: Env, id: string, args: any) {
  const status = args.status === undefined ? undefined : String(args.status);
  if (status !== undefined && !["new", "seen", "done"].includes(status)) throw new ApiError(400, "`status` must be new, seen or done");
  const reply = args.reply === undefined ? undefined : str(args.reply, "reply", 20000);
  const f = await env.DB.prepare(`SELECT status FROM feedback WHERE id = ?`).bind(id).first<{ status: string }>();
  if (!f) throw new ApiError(404, "No such feedback");
  await env.DB.prepare(`UPDATE feedback SET status = ?, reply = COALESCE(?, reply), replied_at = CASE WHEN ? IS NOT NULL THEN ? ELSE replied_at END WHERE id = ?`)
    .bind(status ?? (reply ? "seen" : f.status), reply || null, reply || null, now(), id).run();
  return { id, status: status ?? (reply ? "seen" : f.status), ...(reply ? { replied: true } : {}) };
}
