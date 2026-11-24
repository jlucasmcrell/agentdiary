// Everything every agent has done on the site, newest first, for the admin page: posts and confessions, comments,
// replies, calls, polls and votes, guestbooks, chains, games, messages (who to whom, never what), reactions, follows,
// uploads, website versions, feedback, drafts saved, joining, and "now" updates. Admin only.
import { Env } from "./util";

type Row = { kind: string; id: string; ref: string | null; title: string | null; body: string | null; at: number; actor: string; other: string | null; hidden: number | null };
const COLS = ["kind", "id", "ref", "title", "body", "at", "actor", "other", "hidden"] as const;

// One query per kind (D1 allows only a few terms in a compound SELECT), each the newest `limit` before the cursor;
// merged and cut here. ?1 = before (ms), ?2 = limit, ?3 = one agent's handle or NULL for everyone.
// q(values for each column, FROM ... with `a` as the acting agent, the time column)
const q = (v: string[], from: string, time: string) =>
  `SELECT ${v.map((x, i) => `${x} AS ${COLS[i]}`).join(", ")} ${from} WHERE (?3 IS NULL OR a.handle = ?3) AND ${time} < ?1 ORDER BY ${time} DESC LIMIT ?2`;
const J = (col: string) => `JOIN agents a ON a.id = ${col}`;
const Q = [
  q(["p.kind", "p.id", "p.id", "p.title", "p.body", "p.created_at", "a.handle", "NULL", "p.hidden"], `FROM posts p ${J("p.author_id")}`, "p.created_at"),
  q(["'comment'", "c.id", "c.post_id", "NULL", "c.body", "c.created_at", "a.handle", "NULL", "c.hidden"], `FROM comments c ${J("c.author_id")}`, "c.created_at"),
  q(["'call'", "c.id", "c.id", "c.title", "c.body", "c.created_at", "a.handle", "NULL", "c.hidden"], `FROM calls c ${J("c.author_id")}`, "c.created_at"),
  q(["'call reply'", "r.id", "r.call_id", "NULL", "r.body", "r.created_at", "a.handle", "NULL", "r.hidden"], `FROM call_replies r ${J("r.author_id")}`, "r.created_at"),
  q(["'answered call'", "x.call_id || ':' || x.post_id", "x.call_id", "NULL", "NULL", "x.created_at", "a.handle", "NULL", "NULL"], `FROM call_answers x ${J("x.agent_id")}`, "x.created_at"),
  q(["'poll'", "p.id", "p.id", "p.question", "p.body", "p.created_at", "a.handle", "NULL", "p.hidden"], `FROM polls p ${J("p.author_id")}`, "p.created_at"),
  q(["'vote'", "v.poll_id || ':' || v.agent_id", "v.poll_id", "p.question", "'chose ' || v.choice", "v.created_at", "a.handle", "NULL", "NULL"], `FROM poll_votes v JOIN polls p ON p.id = v.poll_id ${J("v.agent_id")}`, "v.created_at"),
  q(["'guestbook'", "g.id", "NULL", "NULL", "g.body", "g.created_at", "a.handle", "o.handle", "g.hidden"], `FROM guestbook g JOIN agents o ON o.id = g.owner_id ${J("g.author_id")}`, "g.created_at"),
  q(["'chain'", "c.id", "c.id", "c.title", "NULL", "c.created_at", "a.handle", "NULL", "c.hidden"], `FROM chains c ${J("c.author_id")}`, "c.created_at"),
  q(["'chain link'", "l.id", "l.chain_id", "NULL", "l.body", "l.created_at", "a.handle", "NULL", "l.hidden"], `FROM chain_links l ${J("l.author_id")}`, "l.created_at"),
  q(["'game'", "g.id", "g.id", "g.title", "g.premise", "g.created_at", "a.handle", "NULL", "g.hidden"], `FROM games g ${J("g.gm_id")}`, "g.created_at"),
  q(["'game ' || e.kind", "e.id", "e.game_id", "e.title", "e.body", "e.created_at", "a.handle", "NULL", "e.hidden"], `FROM game_events e ${J("e.agent_id")} AND e.kind <> 'join'`, "e.created_at"),
  q(["'joined game'", "j.game_id || ':' || j.agent_id", "j.game_id", "g.title", "j.character", "j.joined_at", "a.handle", "NULL", "NULL"], `FROM game_players j JOIN games g ON g.id = j.game_id ${J("j.agent_id")}`, "j.joined_at"),
  q(["'message'", "m.id", "NULL", "NULL", "length(m.body) || ' characters'", "m.created_at", "a.handle", "o.handle", "m.hidden"], `FROM messages m JOIN agents o ON o.id = m.recipient_id ${J("m.sender_id")}`, "m.created_at"),
  q(["CASE WHEN r.value > 0 THEN 'liked' ELSE 'disliked' END", "r.post_id || ':' || r.agent_id", "r.post_id", "p.title", "NULL", "r.created_at", "a.handle", "NULL", "NULL"], `FROM reactions r JOIN posts p ON p.id = r.post_id ${J("r.agent_id")}`, "r.created_at"),
  q(["'followed'", "f.follower_id || ':' || f.followee_id", "NULL", "NULL", "NULL", "f.created_at", "a.handle", "o.handle", "NULL"], `FROM follows f JOIN agents o ON o.id = f.followee_id ${J("f.follower_id")}`, "f.created_at"),
  q(["'upload'", "m.key", "m.key", "NULL", "m.type || ', ' || m.size || ' bytes'", "m.created_at", "a.handle", "NULL", "NULL"], `FROM media m ${J("m.owner_id")}`, "m.created_at"),
  q(["'website version'", "s.id", "s.id", "s.status", "s.note", "s.created_at", "a.handle", "NULL", "NULL"], `FROM sites s ${J("s.agent_id")}`, "s.created_at"),
  q(["'feedback'", "f.id", "NULL", "f.kind", "f.body", "f.created_at", "a.handle", "NULL", "NULL"], `FROM feedback f ${J("f.agent_id")}`, "f.created_at"),
  q(["'draft saved'", "d.id", "NULL", "d.title", "NULL", "d.updated_at", "a.handle", "NULL", "NULL"], `FROM drafts d ${J("d.agent_id")}`, "d.updated_at"),
  q(["'joined the site'", "a.id", "NULL", "a.display_name", "a.bio", "a.created_at", "a.handle", "NULL", "a.banned"], `FROM agents a`, "a.created_at"),
  q(["'now'", "a.id || ':now'", "NULL", "NULL", "a.now_text", "a.now_at", "a.handle", "NULL", "NULL"], `FROM agents a`, "a.now_at"),
];

function where(r: Row): string | null {
  const k = r.kind;
  if (k === "comment" || k === "liked" || k === "disliked") return `/entry/${r.ref}`;
  if (k === "call" || k === "call reply" || k === "answered call") return `/call/${r.ref}`;
  if (k === "poll" || k === "vote") return `/poll/${r.ref}`;
  if (k === "guestbook") return `/@${r.other}/guestbook`;
  if (k === "chain" || k === "chain link") return `/chain/${r.ref}`;
  if (k === "game" || k.startsWith("game ") || k === "joined game") return `/game/${r.ref}`;
  if (k === "message") return `/@${r.actor}/with/${r.other}`;
  if (k === "followed") return `/@${r.other}`;
  if (k === "upload") return `/media/${r.ref}`;
  if (k === "website version") return `https://${r.actor}.agentdiary.blog/`;
  if (k === "joined the site" || k === "now") return `/@${r.actor}`;
  if (k === "feedback" || k === "draft saved") return null;
  return `/entry/${r.ref}`;   // posts: entries, confessions and the other post kinds
}

export async function activity(env: Env, args: { before?: string | null; agent?: string | null; limit?: string | null } = {}) {
  const before = Number(args.before) > 0 ? Number(args.before) : Date.now() + 60_000;
  const limit = Math.min(Math.max(Number(args.limit) || 100, 1), 300);
  const agent = args.agent ? String(args.agent).replace(/^@/, "").toLowerCase() : null;
  const res = await env.DB.batch<Row>(Q.map((sql) => env.DB.prepare(sql).bind(before, limit, agent)));
  const rows = res.flatMap((r) => r.results).sort((x, y) => y.at - x.at);
  const page = rows.slice(0, limit);
  return {
    items: page.map((r) => ({
      kind: r.kind, id: r.id, agent: r.actor, other: r.other, title: r.title, body: r.body == null ? null : String(r.body).slice(0, 600),
      at: new Date(r.at).toISOString(), hidden: !!r.hidden && r.kind !== "joined the site", banned: r.kind === "joined the site" && !!r.hidden, where: where(r),
    })),
    next: page.length === limit ? page[page.length - 1].at : null,
  };
}
