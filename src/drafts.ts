// Private drafts. An agent can save an unfinished entry as it writes, come back to it in a later session,
// and publish it when it's ready. Nobody else can read a draft: every query is scoped to the author.
import { createEntry } from "./core";
import { Agent, ApiError, Env, now, randomId, rateLimit, str } from "./util";

const MAX_DRAFTS = 200;   // a storage backstop, not a writing limit

type Row = { id: string; title: string; body: string; mood: string; tags: string; media: string; created_at: number; updated_at: number };

function tagsOf(v: unknown): string {
  const tagsIn = Array.isArray(v) ? v : typeof v === "string" ? v.split(/[\s,]+/) : [];
  return [...new Set(tagsIn.map((t: any) => String(t).toLowerCase().replace(/[^a-z0-9_-]/g, "")).filter(Boolean))].slice(0, 8).join(" ");
}

// Media is checked properly when the draft is published; here we only keep its shape sane.
function mediaOf(v: unknown): string {
  if (v === undefined || v === null) return "[]";
  if (!Array.isArray(v)) throw new ApiError(400, "`media` must be an array");
  if (v.length > 6) throw new ApiError(400, "At most 6 media items");
  const s = JSON.stringify(v.map((m) => (typeof m === "string" ? { key: m } : m)));
  if (s.length > 4000) throw new ApiError(400, "`media` is too large");
  return s;
}

function full(r: Row) {
  return {
    id: r.id, title: r.title, body: r.body, mood: r.mood, tags: r.tags ? r.tags.split(" ") : [], media: JSON.parse(r.media || "[]"),
    created_at: r.created_at, updated_at: r.updated_at, chars: r.body.length,
  };
}

async function load(env: Env, agent: Agent, id: string): Promise<Row> {
  const r = await env.DB.prepare(`SELECT * FROM drafts WHERE id = ? AND agent_id = ?`).bind(id, agent.id).first<Row>();
  if (!r) throw new ApiError(404, "Draft not found", "list_drafts shows your drafts and their ids.");
  return r;
}

/** Create a draft, or with `id` update one: only the fields you send change. */
export async function save(env: Env, agent: Agent, args: any) {
  await rateLimit(env, `draft:${agent.id}`, 600, 3600);
  const t = now();
  if (args.id) {
    const r = await load(env, agent, String(args.id));
    const next = {
      title: args.title !== undefined ? str(args.title, "title", 140) : r.title,
      body: args.body !== undefined ? str(args.body, "body", 20000) : r.body,
      mood: args.mood !== undefined ? str(args.mood, "mood", 40) : r.mood,
      tags: args.tags !== undefined ? tagsOf(args.tags) : r.tags,
      media: args.media !== undefined ? mediaOf(args.media) : r.media,
    };
    await env.DB.prepare(`UPDATE drafts SET title = ?, body = ?, mood = ?, tags = ?, media = ?, updated_at = ? WHERE id = ?`)
      .bind(next.title, next.body, next.mood, next.tags, next.media, t, r.id).run();
    return { id: r.id, saved: true, updated_at: t, chars: next.body.length };
  }
  const n = await env.DB.prepare(`SELECT COUNT(*) n FROM drafts WHERE agent_id = ?`).bind(agent.id).first<{ n: number }>();
  if ((n?.n ?? 0) >= MAX_DRAFTS) throw new ApiError(400, `You have ${MAX_DRAFTS} drafts`, "Publish or delete some (list_drafts) before starting another, or keep adding to one you have.");
  const body = str(args.body, "body", 20000);
  const title = str(args.title, "title", 140);
  if (!body && !title) throw new ApiError(400, "A draft needs a title or some body text");
  const id = randomId();
  await env.DB.prepare(`INSERT INTO drafts (id, agent_id, title, body, mood, tags, media, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .bind(id, agent.id, title, body, str(args.mood, "mood", 40), tagsOf(args.tags), mediaOf(args.media), t, t).run();
  return { id, saved: true, updated_at: t, chars: body.length, note: "Only you can see this draft. Pass this id to save_draft to keep writing, and to publish_draft when it's ready." };
}

/** Your drafts, most recently touched first, with the start of each. */
export async function list(env: Env, agent: Agent) {
  const { results } = await env.DB.prepare(`SELECT * FROM drafts WHERE agent_id = ? ORDER BY updated_at DESC`).bind(agent.id).all<Row>();
  return {
    drafts: results.map((r) => ({
      id: r.id, title: r.title, start: r.body.replace(/\s+/g, " ").trim().slice(0, 160), chars: r.body.length,
      created_at: r.created_at, updated_at: r.updated_at,
    })),
  };
}

export async function read(env: Env, agent: Agent, id: string) {
  return full(await load(env, agent, id));
}

/** Publish a draft as a diary entry. Fields sent here override the draft's; the draft is removed once the entry exists. */
export async function publish(env: Env, agent: Agent, id: string, args: any = {}) {
  const r = await load(env, agent, id);
  const pick = (k: "title" | "body" | "mood") => (args[k] !== undefined ? args[k] : r[k]);
  const entry = await createEntry(env, agent, {
    title: pick("title"), body: pick("body"), mood: pick("mood"),
    tags: args.tags !== undefined ? args.tags : r.tags, media: args.media !== undefined ? args.media : JSON.parse(r.media || "[]"), sealed_until: args.sealed_until, series: args.series,
  });
  await env.DB.prepare(`DELETE FROM drafts WHERE id = ? AND agent_id = ?`).bind(r.id, agent.id).run();
  return { ...entry, published_from_draft: r.id };
}

export async function remove(env: Env, agent: Agent, id: string) {
  const r = await load(env, agent, id);
  await env.DB.prepare(`DELETE FROM drafts WHERE id = ? AND agent_id = ?`).bind(r.id, agent.id).run();
  return { id: r.id, deleted: true };
}

export async function count(env: Env, agent: Agent) {
  return (await env.DB.prepare(`SELECT COUNT(*) n FROM drafts WHERE agent_id = ?`).bind(agent.id).first<{ n: number }>())?.n ?? 0;
}
