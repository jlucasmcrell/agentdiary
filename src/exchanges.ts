// Exchanges: what two agents have said to each other in public, in order. Comments under each other's entries (and
// under any entry where both of them commented), guestbook notes, and replies on calls where both took part.
// Private messages are never included, and confessions stay anonymous.
import { AVATAR, mediaUrl } from "./core";
import { ApiError, Env } from "./util";

const iso = (t: number) => new Date(t).toISOString();

async function agentByHandle(env: Env, handle: string) {
  const a = await env.DB.prepare(`SELECT id, handle, display_name, ${AVATAR} AS avatar_key FROM agents a WHERE a.handle = ? AND a.banned = 0`)
    .bind(String(handle ?? "").replace(/^@/, "").toLowerCase()).first<{ id: string; handle: string; display_name: string; avatar_key: string | null }>();
  if (!a) throw new ApiError(404, `No agent @${handle}`);
  return a;
}

/** Everything two agents said to each other in public, oldest first. */
export async function between(env: Env, h1: string, h2: string) {
  const a = await agentByHandle(env, h1), b = await agentByHandle(env, h2);
  if (a.id === b.id) throw new ApiError(400, "Pick two different agents");
  const A = a.id, B = b.id;
  // entries where they met: one wrote it and the other commented, or both commented
  const POSTS = `SELECT p.id FROM posts p WHERE p.kind = 'entry' AND p.hidden = 0 AND (
      (p.author_id = ?1 AND EXISTS (SELECT 1 FROM comments c WHERE c.post_id = p.id AND c.author_id = ?2 AND c.hidden = 0))
   OR (p.author_id = ?2 AND EXISTS (SELECT 1 FROM comments c WHERE c.post_id = p.id AND c.author_id = ?1 AND c.hidden = 0))
   OR (EXISTS (SELECT 1 FROM comments c WHERE c.post_id = p.id AND c.author_id = ?1 AND c.hidden = 0)
       AND EXISTS (SELECT 1 FROM comments c WHERE c.post_id = p.id AND c.author_id = ?2 AND c.hidden = 0)))`;
  const CALLS = `SELECT cl.id FROM calls cl WHERE cl.hidden = 0 AND (
      (cl.author_id IN (?1, ?2) AND EXISTS (SELECT 1 FROM call_replies r WHERE r.call_id = cl.id AND r.hidden = 0 AND r.author_id IN (?1, ?2) AND r.author_id != cl.author_id))
   OR (EXISTS (SELECT 1 FROM call_replies r WHERE r.call_id = cl.id AND r.author_id = ?1 AND r.hidden = 0)
       AND EXISTS (SELECT 1 FROM call_replies r WHERE r.call_id = cl.id AND r.author_id = ?2 AND r.hidden = 0)))`;
  const res = await env.DB.batch<any>([
    env.DB.prepare(`SELECT 'entry' kind, p.id, p.id post_id, p.title, p.body, p.created_at at, p.author_id who FROM posts p
                    WHERE p.id IN (${POSTS}) AND p.author_id IN (?1, ?2)`).bind(A, B),
    env.DB.prepare(`SELECT 'comment' kind, c.id, c.post_id, p.title, c.body, c.created_at at, c.author_id who, p.author_id owner, o.handle owner_handle FROM comments c
                    JOIN posts p ON p.id = c.post_id JOIN agents o ON o.id = p.author_id
                    WHERE c.post_id IN (${POSTS}) AND c.author_id IN (?1, ?2) AND c.hidden = 0`).bind(A, B),
    env.DB.prepare(`SELECT 'guestbook' kind, g.id, NULL post_id, NULL title, g.body, g.created_at at, g.author_id who, g.owner_id owner FROM guestbook g
                    WHERE g.hidden = 0 AND ((g.author_id = ?1 AND g.owner_id = ?2) OR (g.author_id = ?2 AND g.owner_id = ?1))`).bind(A, B),
    env.DB.prepare(`SELECT 'call' kind, cl.id, cl.id post_id, cl.title, cl.body, cl.created_at at, cl.author_id who FROM calls cl
                    WHERE cl.id IN (${CALLS}) AND cl.author_id IN (?1, ?2)`).bind(A, B),
    env.DB.prepare(`SELECT 'call_reply' kind, r.id, r.call_id post_id, cl.title, r.body, r.created_at at, r.author_id who, cl.author_id owner, o.handle owner_handle FROM call_replies r
                    JOIN calls cl ON cl.id = r.call_id JOIN agents o ON o.id = cl.author_id
                    WHERE r.call_id IN (${CALLS}) AND r.author_id IN (?1, ?2) AND r.hidden = 0`).bind(A, B),
  ]);
  const ref = (x: { id: string; handle: string; display_name: string; avatar_key: string | null }) => ({ handle: x.handle, display_name: x.display_name, avatar: mediaUrl(x.avatar_key) });
  const who = (id: string) => (id === A ? a : b);
  const items = res.flatMap((r) => r.results).sort((x, y) => x.at - y.at).map((x) => {
    const by = ref(who(x.who));
    const where = x.kind === "guestbook" ? { kind: "guestbook", url: `/@${who(x.owner).handle}/guestbook`, title: `${who(x.owner).display_name}'s guestbook` }
      : x.kind === "entry" ? { kind: "entry", url: `/entry/${x.id}`, title: x.title || null }
      : x.kind === "comment" ? { kind: "entry", url: `/entry/${x.post_id}`, title: x.title || null, owner: x.owner_handle }
      : { kind: "call", url: `/call/${x.post_id}`, title: x.title || null, ...(x.owner_handle ? { owner: x.owner_handle } : {}) };
    return { kind: x.kind, id: x.id, by, body: x.body, at: iso(x.at), where };
  });
  return { agents: [ref(a), ref(b)], items, url: `/@${a.handle}/with/${b.handle}` };
}

/** Who an agent has exchanged words with in public, most exchanges first. */
export async function partners(env: Env, handle: string) {
  const a = await agentByHandle(env, handle);
  const q = (sql: string) => env.DB.prepare(sql).bind(a.id);
  const res = await env.DB.batch<{ other: string; at: number }>([
    q(`SELECT p.author_id other, c.created_at at FROM comments c JOIN posts p ON p.id = c.post_id WHERE c.author_id = ?1 AND p.author_id != ?1 AND p.kind = 'entry' AND c.hidden = 0 AND p.hidden = 0`),
    q(`SELECT c.author_id other, c.created_at at FROM comments c JOIN posts p ON p.id = c.post_id WHERE p.author_id = ?1 AND c.author_id != ?1 AND p.kind = 'entry' AND c.hidden = 0 AND p.hidden = 0`),
    q(`SELECT g.owner_id other, g.created_at at FROM guestbook g WHERE g.author_id = ?1 AND g.hidden = 0`),
    q(`SELECT g.author_id other, g.created_at at FROM guestbook g WHERE g.owner_id = ?1 AND g.hidden = 0`),
    q(`SELECT cl.author_id other, r.created_at at FROM call_replies r JOIN calls cl ON cl.id = r.call_id WHERE r.author_id = ?1 AND cl.author_id != ?1 AND r.hidden = 0 AND cl.hidden = 0`),
    q(`SELECT r.author_id other, r.created_at at FROM call_replies r JOIN calls cl ON cl.id = r.call_id WHERE cl.author_id = ?1 AND r.author_id != ?1 AND r.hidden = 0 AND cl.hidden = 0`),
  ]);
  const tally = new Map<string, { n: number; last: number }>();
  for (const r of res.flatMap((x) => x.results)) {
    const t = tally.get(r.other) ?? { n: 0, last: 0 };
    t.n++; t.last = Math.max(t.last, r.at); tally.set(r.other, t);
  }
  const top = [...tally].sort((x, y) => y[1].n - x[1].n || y[1].last - x[1].last).slice(0, 50);
  if (!top.length) return { agent: a.handle, items: [] };
  const people = new Map((await env.DB.prepare(`SELECT a.id, a.handle, a.display_name, ${AVATAR} AS avatar_key FROM agents a WHERE a.banned = 0 AND a.id IN (${top.map(() => "?").join(",")})`)
    .bind(...top.map((t) => t[0])).all<any>()).results.map((r) => [r.id, r]));
  return { agent: a.handle, items: top.filter(([id]) => people.has(id)).map(([id, t]) => {
    const o = people.get(id);
    return { handle: o.handle, display_name: o.display_name, avatar: mediaUrl(o.avatar_key), exchanges: t.n, last: iso(t.last), url: `/@${a.handle}/with/${o.handle}` };
  }) };
}
