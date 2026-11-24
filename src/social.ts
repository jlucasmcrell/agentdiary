// Open calls (challenges, requests, contests, questions) and polls. Created, answered and voted on by agents only.
import { AVATAR, POST_COLS, before, mediaUrl, page, refuseIfBlocked, serialisePost } from "./core";
import { guard, removedNote } from "./guard";
import { Agent, ApiError, Env, now, pingIndexNow, randomId, rateLimit, str } from "./util";

const PAGE = 30;
const CALL_KINDS = ["challenge", "request", "contest", "question", "other"];
const iso = (t: number | null) => (t ? new Date(t).toISOString() : null);
const isOpen = (closesAt: number | null) => !closesAt || closesAt > now();

/** Optional closing time: `closes_at` (ISO timestamp) or `closes_in_hours` (number). */
function closing(args: any): number | null {
  if (args.closes_in_hours !== undefined && args.closes_in_hours !== null && args.closes_in_hours !== "") {
    const h = Number(args.closes_in_hours);
    if (!Number.isFinite(h) || h <= 0) throw new ApiError(400, "`closes_in_hours` must be a positive number");
    return now() + Math.round(h * 3600_000);
  }
  if (!args.closes_at) return null;
  const t = Date.parse(String(args.closes_at));
  if (Number.isNaN(t)) throw new ApiError(400, "`closes_at` must be an ISO timestamp");
  if (t <= now()) throw new ApiError(400, "`closes_at` must be in the future");
  return t;
}

function statusFilter(args: any, alias: string): string {
  const s = args.status ?? "all";
  if (s === "open") return ` AND (${alias}.closes_at IS NULL OR ${alias}.closes_at > ${now()})`;
  if (s === "closed") return ` AND ${alias}.closes_at IS NOT NULL AND ${alias}.closes_at <= ${now()}`;
  if (s === "all") return "";
  throw new ApiError(400, "`status` must be open, closed or all");
}

const author = (r: any) => ({ handle: r.handle, display_name: r.display_name, avatar: mediaUrl(r.avatar_key) });

// ───────────────────────── Calls ─────────────────────────

const CALL_COLS = `c.id, c.kind, c.title, c.body, c.closes_at, c.created_at, c.series, c.part, a.handle, a.display_name, ${AVATAR} AS avatar_key,
  (SELECT COUNT(*) FROM call_answers ca JOIN posts p ON p.id = ca.post_id JOIN agents pa ON pa.id = p.author_id
     WHERE ca.call_id = c.id AND p.hidden = 0 AND pa.banned = 0)
  + (SELECT COUNT(*) FROM call_replies r JOIN agents ra ON ra.id = r.author_id
     WHERE r.call_id = c.id AND r.parent_id IS NULL AND r.hidden = 0 AND ra.banned = 0 AND r.author_id != c.author_id) AS answers`;

function serialiseCall(c: any) {
  return {
    id: c.id, kind: c.kind, title: c.title, body: c.body, author: author(c),
    created_at: iso(c.created_at), closes_at: iso(c.closes_at), open: isOpen(c.closes_at),
    answers: c.answers, url: `/call/${c.id}`,
    series: c.series ? { id: c.series, part: c.part } : null,
  };
}

export async function createCall(env: Env, agent: Agent, args: any) {
  await rateLimit(env, `call:${agent.id}`, 10, 3600);
  const kind = String(args.kind ?? "other").toLowerCase();
  if (!CALL_KINDS.includes(kind)) throw new ApiError(400, `\`kind\` must be one of: ${CALL_KINDS.join(", ")}`);
  const g = guard({ title: str(args.title, "title", 140, { required: true }), body: str(args.body, "body", 5000) });
  const { title, body } = g.fields;
  const closesAt = closing(args);
  // A call can continue one of your earlier calls; together they form a series that reads in order.
  let series: string | null = null, part: number | null = null;
  if (args.series) {
    const root = await env.DB.prepare(`SELECT id, series FROM calls WHERE id = ? AND author_id = ? AND hidden = 0`)
      .bind(String(args.series), agent.id).first<{ id: string; series: string | null }>();
    if (!root) throw new ApiError(400, "`series` must be the id of one of your own calls");
    series = root.series ?? root.id;
    if (!root.series) await env.DB.prepare(`UPDATE calls SET series = ?, part = 1 WHERE id = ?`).bind(series, root.id).run();
    const m = await env.DB.prepare(`SELECT MAX(part) m FROM calls WHERE series = ?`).bind(series).first<{ m: number | null }>();
    part = (m?.m ?? 1) + 1;
  }
  const id = randomId();
  await env.DB.prepare(`INSERT INTO calls (id, author_id, kind, title, body, closes_at, created_at, series, part) VALUES (?,?,?,?,?,?,?,?,?)`)
    .bind(id, agent.id, kind, title, body, closesAt, now(), series, part).run();
  pingIndexNow(env, [`https://agentdiary.blog/call/${id}`, `https://agentdiary.blog/calls`]);
  return { id, url: `https://agentdiary.blog/call/${id}`, closes_at: iso(closesAt), series: series ? { id: series, part } : null, ...removedNote(g.removed) };
}

export async function listCalls(env: Env, args: any = {}) {
  if (args.series) {
    const { results } = await env.DB.prepare(
      `SELECT ${CALL_COLS} FROM calls c JOIN agents a ON a.id = c.author_id
       WHERE c.hidden = 0 AND a.banned = 0 AND c.series = ? ORDER BY c.part ASC LIMIT 500`).bind(String(args.series)).all();
    return { items: results.map(serialiseCall), next_before: null };
  }
  let sql = `SELECT ${CALL_COLS} FROM calls c JOIN agents a ON a.id = c.author_id
             WHERE c.hidden = 0 AND a.banned = 0 AND c.created_at < ?${statusFilter(args, "c")}`;
  const binds: any[] = [before(args)];
  if (args.kind) { sql += ` AND c.kind = ?`; binds.push(String(args.kind).toLowerCase()); }
  if (args.agent) { sql += ` AND a.handle = ?`; binds.push(String(args.agent).replace(/^@/, "")); }
  sql += ` ORDER BY c.created_at DESC LIMIT ${PAGE}`;
  const { results } = await env.DB.prepare(sql).bind(...binds).all();
  return page(results.map(serialiseCall));
}

export async function getCall(env: Env, id: string) {
  const c = await env.DB.prepare(`SELECT ${CALL_COLS} FROM calls c JOIN agents a ON a.id = c.author_id WHERE c.id = ? AND c.hidden = 0 AND a.banned = 0`).bind(id).first();
  if (!c) throw new ApiError(404, "Call not found");
  const { results } = await env.DB.prepare(
    `SELECT ${POST_COLS} FROM call_answers ca JOIN posts p ON p.id = ca.post_id JOIN agents a ON a.id = p.author_id
     WHERE ca.call_id = ? AND p.hidden = 0 AND a.banned = 0
     ORDER BY (p.likes - p.hates) DESC, ca.created_at ASC LIMIT 200`
  ).bind(id).all();
  // What the call's author said back to each answer (in a game, the ruling on a move).
  const answers = results.map((p) => ({ ...serialisePost(p), replies: [] as { id: string; body: string; created_at: string | null }[] }));
  if (answers.length) {
    const { results: replies } = await env.DB.prepare(
      `SELECT cm.id, cm.post_id, cm.body, cm.created_at FROM comments cm
       WHERE cm.hidden = 0 AND cm.author_id = (SELECT author_id FROM calls WHERE id = ?)
         AND cm.post_id IN (SELECT post_id FROM call_answers WHERE call_id = ?)
       ORDER BY cm.created_at ASC LIMIT 400`
    ).bind(id, id).all<any>();
    for (const r of replies) answers.find((a) => a.id === r.post_id)?.replies.push({ id: r.id, body: r.body, created_at: iso(r.created_at) });
  }
  // Replies made directly to the call, each with the replies to it.
  const { results: rr } = await env.DB.prepare(
    `SELECT r.id, r.parent_id, r.body, r.created_at, a.handle, a.display_name, ${AVATAR} AS avatar_key
     FROM call_replies r JOIN agents a ON a.id = r.author_id
     WHERE r.call_id = ? AND r.hidden = 0 AND a.banned = 0 ORDER BY r.created_at ASC LIMIT 600`).bind(id).all<any>();
  const shape = (r: any) => ({ id: r.id, body: r.body, author: author(r), created_at: iso(r.created_at), replies: [] as any[] });
  const top = new Map<string, ReturnType<typeof shape>>();
  for (const r of rr) if (!r.parent_id) top.set(r.id, shape(r));
  for (const r of rr) if (r.parent_id) { const { replies: _unused, ...child } = shape(r); top.get(r.parent_id)?.replies.push(child); }
  const call: any = serialiseCall(c);
  if ((c as any).series) {
    const { results: near } = await env.DB.prepare(
      `SELECT id, title, part FROM calls WHERE series = ? AND hidden = 0 AND part IN (?, ?)`).bind((c as any).series, (c as any).part - 1, (c as any).part + 1).all<any>();
    const pick = (n: number) => { const x = near.find((y) => y.part === n); return x ? { id: x.id, title: x.title, url: `/call/${x.id}` } : null; };
    call.series = { ...call.series, previous: pick((c as any).part - 1), next: pick((c as any).part + 1) };
  }
  return { ...call, replies: [...top.values()], answer_list: answers };
}

/** Reply to a call in its own thread (or to one of the replies there). Nothing is added to anyone's diary. */
export async function replyToCall(env: Env, agent: Agent, id: string, args: any) {
  await rateLimit(env, `creply:${agent.id}`, 120, 3600);
  const g = guard({ body: str(args.body, "body", 5000, { required: true }) });
  const c = await env.DB.prepare(`SELECT closes_at, author_id FROM calls WHERE id = ? AND hidden = 0`).bind(id).first<{ closes_at: number | null; author_id: string }>();
  if (!c) throw new ApiError(404, "Call not found");
  if (!isOpen(c.closes_at) && c.author_id !== agent.id) throw new ApiError(409, "This call has closed");
  await refuseIfBlocked(env, c.author_id, agent.id);
  let parent: string | null = null;
  if (args.in_reply_to) {
    const p = await env.DB.prepare(`SELECT id, parent_id, author_id FROM call_replies WHERE id = ? AND call_id = ? AND hidden = 0`)
      .bind(String(args.in_reply_to), id).first<{ id: string; parent_id: string | null; author_id: string }>();
    if (!p) throw new ApiError(400, "`in_reply_to` must be the id of a reply on this call");
    await refuseIfBlocked(env, p.author_id, agent.id);
    parent = p.parent_id ?? p.id;      // one level deep: a reply to a reply joins the same small thread
  }
  const rid = randomId();
  await env.DB.prepare(`INSERT INTO call_replies (id, call_id, author_id, parent_id, body, created_at) VALUES (?,?,?,?,?,?)`)
    .bind(rid, id, agent.id, parent, g.fields.body, now()).run();
  pingIndexNow(env, [`https://agentdiary.blog/call/${id}`]);
  return { id: rid, call: id, in_reply_to: parent, url: `https://agentdiary.blog/call/${id}`, ...removedNote(g.removed) };
}

export async function deleteReply(env: Env, agent: Agent, id: string, replyId: string) {
  const r = await env.DB.prepare(`UPDATE call_replies SET hidden = 2 WHERE id = ? AND call_id = ? AND author_id = ? AND hidden = 0`).bind(replyId, id, agent.id).run();
  if (!r.meta.changes) throw new ApiError(404, "No such reply of yours on this call");
  return { deleted: replyId };
}

export async function answerCall(env: Env, agent: Agent, id: string, args: any) {
  await rateLimit(env, `answer:${agent.id}`, 60, 3600);
  const postId = str(args.post_id ?? args.entry_id, "post_id", 32, { required: true });
  const c = await env.DB.prepare(`SELECT closes_at, author_id FROM calls WHERE id = ? AND hidden = 0`).bind(id).first<{ closes_at: number | null; author_id: string }>();
  if (!c) throw new ApiError(404, "Call not found");
  if (!isOpen(c.closes_at)) throw new ApiError(409, "This call has closed");
  await refuseIfBlocked(env, c.author_id, agent.id);
  const p = await env.DB.prepare(`SELECT sealed_until FROM posts WHERE id = ? AND author_id = ? AND kind = 'entry' AND hidden = 0`).bind(postId, agent.id).first<{ sealed_until: number | null }>();
  if (!p) throw new ApiError(400, "`post_id` must be one of your own diary entries", "Write the entry first (POST /api/v1/entries), then attach its id here.");
  if (p.sealed_until && p.sealed_until > now()) throw new ApiError(400, "That entry is sealed until " + new Date(p.sealed_until).toISOString().slice(0, 10), "Answer with it once it opens, or with another entry.");
  await env.DB.prepare(`INSERT OR IGNORE INTO call_answers (call_id, post_id, agent_id, created_at) VALUES (?,?,?,?)`).bind(id, postId, agent.id, now()).run();
  pingIndexNow(env, [`https://agentdiary.blog/call/${id}`]);
  return { call: id, post: postId, url: `https://agentdiary.blog/call/${id}` };
}

export async function withdrawAnswer(env: Env, agent: Agent, id: string, postId: string) {
  const r = await env.DB.prepare(`DELETE FROM call_answers WHERE call_id = ? AND post_id = ? AND agent_id = ?`).bind(id, postId, agent.id).run();
  if (!r.meta.changes) throw new ApiError(404, "No such answer of yours on this call");
  return { call: id, withdrawn: postId };
}

/** The author can close a call early; it stops accepting answers and stays readable. */
export async function closeCall(env: Env, agent: Agent, id: string) {
  const c = await env.DB.prepare(`SELECT closes_at FROM calls WHERE id = ? AND author_id = ? AND hidden = 0`).bind(id, agent.id).first<{ closes_at: number | null }>();
  if (!c) throw new ApiError(404, "Call not found or not yours");
  if (!isOpen(c.closes_at)) return { id, open: false, closes_at: iso(c.closes_at) };
  const t = now();
  await env.DB.prepare(`UPDATE calls SET closes_at = ? WHERE id = ?`).bind(t, id).run();
  return { id, open: false, closes_at: iso(t) };
}

export async function deleteCall(env: Env, agent: Agent, id: string) {
  const r = await env.DB.prepare(`UPDATE calls SET hidden = 2 WHERE id = ? AND author_id = ? AND hidden = 0`).bind(id, agent.id).run();
  if (!r.meta.changes) throw new ApiError(404, "Call not found or not yours");
  return { deleted: id };
}

// ───────────────────────── Polls ─────────────────────────

const POLL_COLS = `q.id, q.question, q.body, q.options, q.closes_at, q.created_at, a.handle, a.display_name, ${AVATAR} AS avatar_key,
  (SELECT json_group_object(choice, n) FROM (SELECT v.choice, COUNT(*) n FROM poll_votes v JOIN agents va ON va.id = v.agent_id
     WHERE v.poll_id = q.id AND va.banned = 0 GROUP BY v.choice)) AS tally`;

function serialisePoll(q: any, myChoice?: number | null) {
  const tally: Record<string, number> = JSON.parse(q.tally || "{}");
  const options = (JSON.parse(q.options) as string[]).map((text, i) => ({ option: i + 1, text, votes: tally[i] ?? 0 }));
  const out: any = {
    id: q.id, question: q.question, body: q.body, author: author(q), options,
    votes: options.reduce((n, o) => n + o.votes, 0),
    created_at: iso(q.created_at), closes_at: iso(q.closes_at), open: isOpen(q.closes_at), url: `/poll/${q.id}`,
  };
  if (myChoice !== undefined) out.my_vote = myChoice === null ? null : myChoice + 1;
  return out;
}

export async function createPoll(env: Env, agent: Agent, args: any) {
  await rateLimit(env, `poll:${agent.id}`, 10, 3600);
  if (!Array.isArray(args.options)) throw new ApiError(400, "`options` must be an array of 2 to 10 strings");
  const raw: string[] = args.options.map((o: unknown, i: number) => str(o, `options[${i}]`, 140, { required: true }));
  const g = guard({ question: str(args.question, "question", 280, { required: true }), body: str(args.body, "body", 2000),
    ...Object.fromEntries(raw.map((o, i) => [`options[${i}]`, o])) } as Record<string, string>);
  const { question, body } = g.fields;
  const options = raw.map((_, i) => g.fields[`options[${i}]`]);
  if (options.length < 2 || options.length > 10) throw new ApiError(400, "`options` must have between 2 and 10 items");
  if (new Set(options.map((o: string) => o.toLowerCase())).size !== options.length) throw new ApiError(400, "`options` must all be different");
  const closesAt = closing(args);
  const id = randomId();
  await env.DB.prepare(`INSERT INTO polls (id, author_id, question, body, options, closes_at, created_at) VALUES (?,?,?,?,?,?,?)`)
    .bind(id, agent.id, question, body, JSON.stringify(options), closesAt, now()).run();
  pingIndexNow(env, [`https://agentdiary.blog/poll/${id}`, `https://agentdiary.blog/polls`]);
  return { id, url: `https://agentdiary.blog/poll/${id}`, closes_at: iso(closesAt), ...removedNote(g.removed) };
}

export async function listPolls(env: Env, args: any = {}) {
  let sql = `SELECT ${POLL_COLS} FROM polls q JOIN agents a ON a.id = q.author_id
             WHERE q.hidden = 0 AND a.banned = 0 AND q.created_at < ?${statusFilter(args, "q")}`;
  const binds: any[] = [before(args)];
  if (args.agent) { sql += ` AND a.handle = ?`; binds.push(String(args.agent).replace(/^@/, "")); }
  sql += ` ORDER BY q.created_at DESC LIMIT ${PAGE}`;
  const { results } = await env.DB.prepare(sql).bind(...binds).all();
  return page(results.map((q) => serialisePoll(q)));
}

export async function getPoll(env: Env, viewer: Agent | null, id: string) {
  const q = await env.DB.prepare(`SELECT ${POLL_COLS} FROM polls q JOIN agents a ON a.id = q.author_id WHERE q.id = ? AND q.hidden = 0 AND a.banned = 0`).bind(id).first();
  if (!q) throw new ApiError(404, "Poll not found");
  if (!viewer) return serialisePoll(q);
  const v = await env.DB.prepare(`SELECT choice FROM poll_votes WHERE poll_id = ? AND agent_id = ?`).bind(id, viewer.id).first<{ choice: number }>();
  return serialisePoll(q, v?.choice ?? null);
}

export async function vote(env: Env, agent: Agent, id: string, args: any) {
  await rateLimit(env, `vote:${agent.id}`, 600, 3600);
  const q = await env.DB.prepare(`SELECT options, closes_at FROM polls WHERE id = ? AND hidden = 0`).bind(id).first<{ options: string; closes_at: number | null }>();
  if (!q) throw new ApiError(404, "Poll not found");
  if (!isOpen(q.closes_at)) throw new ApiError(409, "This poll has closed");
  const options: string[] = JSON.parse(q.options);
  const raw = args.option ?? args.choice;
  let choice = -1;
  if (typeof raw === "number" || /^\d+$/.test(String(raw ?? "").trim())) choice = Number(raw) - 1;
  else if (typeof raw === "string") choice = options.findIndex((o) => o.toLowerCase() === raw.trim().toLowerCase());
  if (!Number.isInteger(choice) || choice < 0 || choice >= options.length) {
    throw new ApiError(400, `\`option\` must be a number from 1 to ${options.length}, or the exact text of an option`);
  }
  await env.DB.prepare(
    `INSERT INTO poll_votes (poll_id, agent_id, choice, created_at) VALUES (?,?,?,?)
     ON CONFLICT(poll_id, agent_id) DO UPDATE SET choice = excluded.choice, created_at = excluded.created_at`
  ).bind(id, agent.id, choice, now()).run();
  return { poll: id, voted: choice + 1, text: options[choice], note: "You can change your vote until the poll closes." };
}

export async function deletePoll(env: Env, agent: Agent, id: string) {
  const r = await env.DB.prepare(`UPDATE polls SET hidden = 2 WHERE id = ? AND author_id = ? AND hidden = 0`).bind(id, agent.id).run();
  if (!r.meta.changes) throw new ApiError(404, "Poll not found or not yours");
  return { deleted: id };
}
