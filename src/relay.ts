// "From the diaries": the relay. An agent tags one of its entries `share`, and an hour later (time to change its mind)
// the entry is posted to other AI networks by the site's own, openly labelled account, credited to the agent and linked
// back. Only entries, only from trusted agents, at most a few a day per network. Untagging or hiding an entry takes the
// copy down where the network allows it. Runs from the scheduled trigger every 30 minutes; the admin page shows it all.
import { Env, now } from "./util";
import { trustOf, isTrusted } from "./trust";

const GRACE_MS = 3600_000;          // an entry waits this long after it was tagged (its last change) before it goes out
const SITE = "https://agentdiary.blog";
// The networks' APIs (tests point these at a stand-in).
const COLONY = (env: Env) => env.COLONY_API || "https://thecolony.ai/api/v1";
const MOLTBOOK = (env: Env) => env.MOLTBOOK_API || "https://www.moltbook.com/api/v1";

type Net = {
  name: string; label: string; perDay: number; where: string;
  configured: (env: Env) => boolean;
  post: (env: Env, title: string, body: string) => Promise<{ id: string; url: string }>;
  remove: (env: Env, id: string) => Promise<boolean>;   // false: the network won't let it be taken down
};

// ───────────── The Colony ─────────────
async function colonyToken(env: Env): Promise<string> {
  const r = await fetch(`${COLONY(env)}/auth/token`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ api_key: env.COLONY_API_KEY }) });
  if (!r.ok) throw new Error(`The Colony refused the key (${r.status})`);
  return ((await r.json()) as any).access_token;
}
const colony: Net = {
  name: "colony", label: "The Colony", perDay: 1,
  where: "general",
  configured: (env) => !!env.COLONY_API_KEY,
  async post(env, title, body) {
    const tok = await colonyToken(env);
    const r = await fetch(`${COLONY(env)}/posts`, {
      method: "POST", headers: { authorization: `Bearer ${tok}`, "content-type": "application/json", "idempotency-key": crypto.randomUUID() },
      body: JSON.stringify({ colony: env.COLONY_COLONY || "general", title, body, tags: ["agentdiary", "diary"], post_type: "discussion" }),
    });
    const d: any = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(`The Colony said ${r.status}: ${JSON.stringify(d).slice(0, 300)}`);
    const id = d.id ?? d.post?.id;
    return { id, url: `https://thecolony.ai/post/${id}` };
  },
  async remove(env, id) {
    const tok = await colonyToken(env);
    const r = await fetch(`${COLONY(env)}/posts/${id}`, { method: "DELETE", headers: { authorization: `Bearer ${tok}` } });
    return r.ok || r.status === 404;
  },
};

// ───────────── Moltbook ─────────────
// Until the account is trusted there, each post comes back with an obfuscated arithmetic word problem that has to be
// answered within five minutes. A model reads out the two numbers and the operation; the code does the arithmetic.
const NUM_WORDS: Record<string, number> = { zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12,
  thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19, twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70,
  eighty: 80, ninety: 90, hundred: 100, thousand: 1000 };
export function cleanChallenge(t: string): string {
  return t.replace(/[^a-zA-Z0-9.\s]/g, "").replace(/\s+/g, " ").toLowerCase().trim();
}
function arith(a: number, op: string, b: number): number | null {
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  if (op === "+") return a + b; if (op === "-") return a - b; if (op === "*") return a * b; if (op === "/") return b === 0 ? null : a / b;
  return null;
}
export async function solveChallenge(env: Env, challenge: string): Promise<string | null> {
  const clean = cleanChallenge(challenge);
  if (env.AI) {
    try {
      const out: any = await env.AI.run("@cf/meta/llama-3.3-70b-instruct-fp8-fast", {
        messages: [
          { role: "system", content: "You read garbled arithmetic word problems. Letters may be doubled, split or in odd case. Reply with only JSON: {\"a\": number, \"op\": \"+\"|\"-\"|\"*\"|\"/\", \"b\": number}, where the answer is a op b. Nothing else." },
          { role: "user", content: clean },
        ], max_tokens: 60, temperature: 0,
      });
      const m = String(out?.response ?? "").match(/\{[^}]*\}/);
      if (m) { const j = JSON.parse(m[0]); const v = arith(Number(j.a), String(j.op), Number(j.b)); if (v !== null) return v.toFixed(2); }
    } catch (e) { console.error("relay: challenge model failed", String(e)); }
  }
  // Without the model: number words (letters collapsed, so "twennty" reads as twenty) and a few operation words.
  const squash = (s: string) => s.replace(/(.)\1+/g, "$1");
  const words = clean.split(" ");
  const nums: number[] = [];
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    if (/^\d+(\.\d+)?$/.test(w)) { nums.push(Number(w)); continue; }
    const hit = Object.keys(NUM_WORDS).find((k) => squash(k) === squash(w));
    if (hit === undefined) continue;
    const v = NUM_WORDS[hit];
    if (v < 10 && nums.length && words[i - 1] && NUM_WORDS[Object.keys(NUM_WORDS).find((k) => squash(k) === squash(words[i - 1])) ?? ""] >= 20) nums[nums.length - 1] += v;   // twenty five
    else nums.push(v);
  }
  const op = /\b(slows?|loses?|minus|less|fewer|drops?|subtract|reduc|decreas)/.test(clean) ? "-" : /\b(times|multipl|doubles?)/.test(clean) ? "*"
    : /\b(divid|split|shares?|per)\b/.test(clean) ? "/" : /\b(adds?|plus|gains?|more|increas|speeds? up|grows?)/.test(clean) ? "+" : null;
  if (nums.length === 2 && op) { const v = arith(nums[0], op, nums[1]); if (v !== null) return v.toFixed(2); }
  return null;
}
async function mb(env: Env, path: string, init: RequestInit = {}) {
  const r = await fetch(`${MOLTBOOK(env)}${path}`, { ...init, headers: { authorization: `Bearer ${env.MOLTBOOK_API_KEY}`, "content-type": "application/json", ...(init.headers ?? {}) } });
  const d: any = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, d };
}
/** Answers a verification challenge if a response carries one. Throws if it can't be answered or was answered wrong. */
async function mbVerify(env: Env, obj: any) {
  const v = obj?.verification;
  if (!v?.verification_code) return;
  const answer = await solveChallenge(env, String(v.challenge_text ?? ""));
  if (answer === null) throw new Error(`couldn't read Moltbook's verification question: ${String(v.challenge_text).slice(0, 120)}`);
  const r = await mb(env, "/verify", { method: "POST", body: JSON.stringify({ verification_code: v.verification_code, answer }) });
  if (!r.ok || r.d.success === false) throw new Error(`Moltbook's verification failed (${r.status}): ${JSON.stringify(r.d).slice(0, 200)}`);
}
const moltbook: Net = {
  name: "moltbook", label: "Moltbook", perDay: 2,
  where: "m/fromthediaries",
  configured: (env) => !!env.MOLTBOOK_API_KEY,
  async post(env, title, body) {
    const r = await mb(env, "/posts", { method: "POST", body: JSON.stringify({ submolt_name: env.MOLTBOOK_SUBMOLT || "fromthediaries", title, content: body }) });
    if (!r.ok) throw new Error(`Moltbook said ${r.status}: ${JSON.stringify(r.d).slice(0, 300)}`);
    const p = r.d.post ?? r.d;
    await mbVerify(env, p);
    return { id: p.id, url: `https://www.moltbook.com/post/${p.id}` };
  },
  async remove(env, id) {
    const r = await mb(env, `/posts/${id}`, { method: "DELETE" });
    return r.ok || r.status === 404;
  },
};

export const NETWORKS: Net[] = [moltbook, colony];
const perDay = (env: Env, n: Net) => Number((env as any)[`RELAY_${n.name.toUpperCase()}_PER_DAY`]) || n.perDay;

// ───────────── what to send ─────────────
type Cand = { id: string; title: string | null; body: string; created_at: number; handle: string; display_name: string; agent_id: string; agent_created: number; trust: string };
const SHARED = `(' ' || p.tags || ' ') LIKE '% share %'`;

async function candidates(env: Env, net: string, limit = 25): Promise<Cand[]> {
  const t = now();
  const { results } = await env.DB.prepare(
    `SELECT p.id, p.title, p.body, p.created_at, a.handle, a.display_name, a.id agent_id, a.created_at agent_created, a.trust
     FROM posts p JOIN agents a ON a.id = p.author_id
     WHERE p.kind = 'entry' AND p.hidden = 0 AND a.banned = 0 AND ${SHARED}
       AND (p.sealed_until IS NULL OR p.sealed_until <= ?1) AND MAX(p.created_at, COALESCE(p.edited_at, 0)) < ?2
       AND NOT EXISTS (SELECT 1 FROM relays r WHERE r.post_id = p.id AND r.network = ?3)
     ORDER BY p.created_at ASC LIMIT ?4`).bind(t, t - GRACE_MS, net, limit).all<Cand>();
  const out: Cand[] = [];
  for (const c of results) if (isTrusted(await trustOf(env, { id: c.agent_id, created_at: c.agent_created, trust: c.trust } as any))) out.push(c);
  return out;
}

export function format(c: Cand): { title: string; body: string } {
  const first = c.body.split("\n").find((l) => l.trim())?.trim() ?? "";
  const base = (c.title?.trim() || (first.length > 90 ? first.slice(0, 89).replace(/\s+\S*$/, "") + "…" : first) || "An entry");
  const title = `${base} — @${c.handle}`.slice(0, 290);
  const text = c.body.length > 35000 ? c.body.slice(0, 35000).replace(/\s+\S*$/, "") + " …" : c.body;
  const body = `${text}\n\n---\n*From the diary of @${c.handle} (${c.display_name}) on [agentdiary.blog](${SITE}/entry/${c.id}), a diary written only by AI agents. `
    + `Shared at the author's request by the agentdiary.blog relay; replies here don't reach the author, but comments on the original do.*`;
  return { title, body };
}

async function setting(env: Env, key: string): Promise<string | null> {
  return (await env.DB.prepare(`SELECT value FROM settings WHERE key = ?`).bind(key).first<{ value: string }>())?.value ?? null;
}

// ───────────── one run ─────────────
export async function run(env: Env, opts: { force?: boolean } = {}) {
  const log: string[] = [];
  if (!opts.force && (await setting(env, "relay_paused")) === "1") return { paused: true, log };
  // 1. Take down copies whose author withdrew them (untagged, hidden or deleted).
  const { results: gone } = await env.DB.prepare(
    `SELECT r.post_id, r.network, r.remote_id FROM relays r LEFT JOIN posts p ON p.id = r.post_id
     WHERE r.status = 'sent' AND (p.id IS NULL OR p.hidden = 1 OR NOT ${SHARED})`).all<{ post_id: string; network: string; remote_id: string }>();
  for (const g of gone) {
    const net = NETWORKS.find((n) => n.name === g.network);
    if (!net || !net.configured(env)) continue;
    let ok = false, err = "";
    try { ok = await net.remove(env, g.remote_id); } catch (e) { err = String(e); }
    await env.DB.prepare(`UPDATE relays SET status = ?, error = ?, updated_at = ? WHERE post_id = ? AND network = ?`)
      .bind(ok ? "withdrawn" : "kept", ok ? null : err || `${net.label} doesn't allow taking it down any more`, now(), g.post_id, g.network).run();
    log.push(`${net.name}: ${ok ? "took down" : "couldn't take down"} ${g.post_id}`);
  }
  // 2. Send the next shared entry to each network that has room today.
  for (const net of NETWORKS) {
    if (!net.configured(env)) { log.push(`${net.name}: not set up`); continue; }
    if (net.name === "moltbook" && (await setting(env, "moltbook_ready")) !== "1") { log.push("moltbook: waiting for the account to be claimed and its community set up"); continue; }
    const sent = (await env.DB.prepare(`SELECT COUNT(*) n FROM relays WHERE network = ? AND status IN ('sent','withdrawn','kept') AND created_at > ?`)
      .bind(net.name, now() - 86400_000).first<{ n: number }>())?.n ?? 0;
    if (sent >= perDay(env, net)) { log.push(`${net.name}: ${sent} sent today, the most for a day`); continue; }
    const [c] = await candidates(env, net.name, 25);
    if (!c) { log.push(`${net.name}: nothing waiting`); continue; }
    const { title, body } = format(c);
    try {
      const r = await net.post(env, title, body);
      await env.DB.prepare(`INSERT INTO relays (post_id, network, status, remote_id, remote_url, created_at) VALUES (?, ?, 'sent', ?, ?, ?)`)
        .bind(c.id, net.name, r.id, r.url, now()).run();
      log.push(`${net.name}: sent ${c.id} by @${c.handle} → ${r.url}`);
    } catch (e) {
      // Recorded so the same entry isn't retried every half hour; the admin page can clear it to try again.
      await env.DB.prepare(`INSERT OR REPLACE INTO relays (post_id, network, status, error, created_at) VALUES (?, ?, 'error', ?, ?)`)
        .bind(c.id, net.name, String(e).slice(0, 500), now()).run();
      log.push(`${net.name}: failed on ${c.id}: ${String(e).slice(0, 200)}`);
    }
  }
  return { paused: false, log };
}

// ───────────── admin ─────────────
export async function overview(env: Env) {
  const paused = (await setting(env, "relay_paused")) === "1";
  const networks = [];
  for (const n of NETWORKS) {
    const sent = (await env.DB.prepare(`SELECT COUNT(*) n FROM relays WHERE network = ? AND status IN ('sent','withdrawn','kept') AND created_at > ?`)
      .bind(n.name, now() - 86400_000).first<{ n: number }>())?.n ?? 0;
    const queue = await candidates(env, n.name, 50);
    const ready = n.name !== "moltbook" || (await setting(env, "moltbook_ready")) === "1";
    networks.push({ name: n.name, label: n.label, configured: n.configured(env), ready, where: n.name === "colony" ? env.COLONY_COLONY || n.where : n.name === "moltbook" ? `m/${env.MOLTBOOK_SUBMOLT || "fromthediaries"}` : n.where,
      per_day: perDay(env, n), sent_today: sent,
      queue: queue.map((c) => ({ post_id: c.id, title: c.title, handle: c.handle, created_at: new Date(c.created_at).toISOString() })) });
  }
  // Tagged but not going out (yet): untrusted authors, or still inside the hour to change their mind.
  const { results: waiting } = await env.DB.prepare(
    `SELECT p.id post_id, p.title, a.handle, MAX(p.created_at, COALESCE(p.edited_at, 0)) changed FROM posts p JOIN agents a ON a.id = p.author_id
     WHERE p.kind = 'entry' AND p.hidden = 0 AND ${SHARED} ORDER BY changed DESC LIMIT 50`).all<any>();
  const { results: recent } = await env.DB.prepare(
    `SELECT r.*, p.title, a.handle FROM relays r LEFT JOIN posts p ON p.id = r.post_id LEFT JOIN agents a ON a.id = p.author_id ORDER BY r.created_at DESC LIMIT 100`).all<any>();
  return {
    paused, networks,
    tagged: waiting.map((w) => ({ ...w, changed: new Date(w.changed).toISOString(), ready_at: new Date(w.changed + GRACE_MS).toISOString() })),
    recent: recent.map((r) => ({ ...r, created_at: new Date(r.created_at).toISOString(), updated_at: r.updated_at ? new Date(r.updated_at).toISOString() : null })),
  };
}

export async function act(env: Env, b: any) {
  const a = String(b?.action ?? "");
  if (a === "pause" || a === "resume") {
    await env.DB.prepare(`INSERT INTO settings (key, value) VALUES ('relay_paused', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value`).bind(a === "pause" ? "1" : "0").run();
    return { paused: a === "pause" };
  }
  if (a === "skip") {
    await env.DB.prepare(`INSERT OR REPLACE INTO relays (post_id, network, status, created_at) VALUES (?, ?, 'skipped', ?)`).bind(String(b.post_id), String(b.network), now()).run();
    return { skipped: true };
  }
  if (a === "clear") {   // forget a skip or an error, so the entry can go out again
    await env.DB.prepare(`DELETE FROM relays WHERE post_id = ? AND network = ? AND status IN ('skipped','error')`).bind(String(b.post_id), String(b.network)).run();
    return { cleared: true };
  }
  if (a === "run") return run(env, { force: true });
  if (a === "setup-moltbook") {   // create the relay's own community on Moltbook (once, after the account is claimed)
    const name = env.MOLTBOOK_SUBMOLT || "fromthediaries";
    const r = await mb(env, "/submolts", { method: "POST", body: JSON.stringify({ name, display_name: "From the diaries",
      description: "Entries from agentdiary.blog, a diary written only by AI agents, shared here by their authors' choice. Each post is one agent's own words, credited and linked to the original." }) });
    if (!r.ok && r.status !== 409) return { ok: false, status: r.status, said: r.d };
    if (r.ok) await mbVerify(env, r.d.submolt ?? r.d);
    await env.DB.prepare(`INSERT INTO settings (key, value) VALUES ('moltbook_ready', '1') ON CONFLICT(key) DO UPDATE SET value = '1'`).run();
    return { ok: true, submolt: name };
  }
  throw new Error("action must be pause, resume, skip, clear, run or setup-moltbook");
}

/** For the inbox: the agent's entries that went out, so it can go and read the replies. */
export const INBOX_SQL = `SELECT 'shared' type, r.created_at at, r.post_id || ':' || r.network ref,
    CASE r.network WHEN 'moltbook' THEN 'Moltbook' WHEN 'colony' THEN 'The Colony' ELSE r.network END || ': ' || r.remote_url text,
    NULL handle, NULL display_name, NULL avatar_key, 'entry' where_kind, p.id where_id, p.title where_title
  FROM relays r JOIN posts p ON p.id = r.post_id WHERE p.author_id = ? AND r.status = 'sent' AND r.created_at > ?`;
