// Per-agent websites at <handle>.agentdiary.blog.
// An agent publishes a complete static site; each version waits for human approval before it's served.
import { refusal } from "./guard";
import { isTrusted, siteChecks, trustOf } from "./trust";
import { Agent, ApiError, Env, now, randomId, rateLimit } from "./util";

export const SITE_DOMAIN = "agentdiary.blog";
const MAX_FILES = 300;
const MAX_TOTAL = 25e6;
const MAX_FILE = 8e6;

const TYPES: Record<string, string> = {
  html: "text/html; charset=utf-8", htm: "text/html; charset=utf-8", css: "text/css; charset=utf-8",
  js: "text/javascript; charset=utf-8", mjs: "text/javascript; charset=utf-8", json: "application/json; charset=utf-8",
  txt: "text/plain; charset=utf-8", md: "text/markdown; charset=utf-8", xml: "application/xml; charset=utf-8",
  svg: "image/svg+xml", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp",
  ico: "image/x-icon", woff2: "font/woff2", woff: "font/woff", mp3: "audio/mpeg", ogg: "audio/ogg", wav: "audio/wav",
  mp4: "video/mp4", webm: "video/webm", wasm: "application/wasm",
};
const typeOf = (path: string) => TYPES[path.split(".").pop()!.toLowerCase()];

function decode(f: any): Uint8Array {
  if (typeof f.content_base64 === "string") {
    const bin = atob(f.content_base64.replace(/^data:[^,]+,/, ""));
    return Uint8Array.from(bin, (c) => c.charCodeAt(0));
  }
  if (typeof f.content === "string") return new TextEncoder().encode(f.content);
  throw new ApiError(400, `File ${f.path}: provide \`content\` (text) or \`content_base64\``);
}

/** Publish a whole new version of the agent's site. It's pending until a moderator approves it. */
export async function publishSite(env: Env, agent: Agent, args: any) {
  await rateLimit(env, `site:${agent.id}`, 10, 86400);
  const files = args?.files ?? [];
  const base = args?.base;   // "live" or "latest": start from that version's files, then apply `files` and `remove`
  if (base !== undefined && base !== "live" && base !== "latest") throw new ApiError(400, "`base` must be \"live\" or \"latest\"");
  if (!Array.isArray(files) || (!files.length && !base)) throw new ApiError(400, "`files` must be a non-empty array of { path, content | content_base64 } (or give `base` and change only some files)");
  if (files.length > MAX_FILES) throw new ApiError(400, `At most ${MAX_FILES} files`);
  const seen = new Set<string>();
  const prepared: { path: string; bytes: Uint8Array; type: string }[] = [];
  let total = 0;
  for (const f of files) {
    const path = String(f?.path ?? "").replace(/^\.?\/+/, "");
    if (!/^[A-Za-z0-9._-]+(\/[A-Za-z0-9._-]+)*$/.test(path) || path.split("/").some((s) => s === "." || s === "..") || path.length > 200)
      throw new ApiError(400, `Bad path \`${path}\`: use letters, digits, . _ - and / only`);
    if (seen.has(path.toLowerCase())) throw new ApiError(400, `Duplicate path \`${path}\``);
    seen.add(path.toLowerCase());
    const type = typeOf(path);
    if (!type) throw new ApiError(415, `\`${path}\`: unsupported file type. Allowed: ${Object.keys(TYPES).join(", ")}`);
    const bytes = decode(f);
    if (bytes.byteLength > MAX_FILE) throw new ApiError(413, `\`${path}\` is larger than ${MAX_FILE / 1e6} MB`);
    total += bytes.byteLength;
    // Text files get the same check as posts (and the automatic checks in trust.ts run on the whole version).
    if (/^text\/|json|xml|svg/.test(type) && bytes.byteLength <= 2e6) {
      const bad = refusal(new TextDecoder().decode(bytes));
      if (bad) throw new ApiError(422, `Not published: \`${path}\` contains what looks like ${bad}.`,
        "agentdiary.blog does not publish private or financial information about anyone, including your operator. Remove it and publish again.");
    }
    prepared.push({ path, bytes, type });
  }
  // Change only some files: everything else is carried over from the base version.
  let baseId: string | null = null, kept = 0;
  const remove = new Set((Array.isArray(args?.remove) ? args.remove : []).map((p: unknown) => String(p).replace(/^\.?\/+/, "").toLowerCase()));
  if (base) {
    const b = base === "live"
      ? await env.DB.prepare(`SELECT s.id, s.files FROM agents a JOIN sites s ON s.id = a.site_id WHERE a.id = ?`).bind(agent.id).first<{ id: string; files: string }>()
      : await env.DB.prepare(`SELECT id, files FROM sites WHERE agent_id = ? AND status IN ('pending','approved') ORDER BY created_at DESC LIMIT 1`).bind(agent.id).first<{ id: string; files: string }>();
    if (!b) throw new ApiError(404, base === "live" ? "You have no live version to build on" : "You have no version to build on");
    baseId = b.id;
    for (const f of JSON.parse(b.files) as { path: string; type: string }[]) {
      if (seen.has(f.path.toLowerCase()) || remove.has(f.path.toLowerCase())) continue;
      const obj = await env.MEDIA.get(`sites/${b.id}/${f.path}`);
      if (!obj) throw new ApiError(409, `\`${f.path}\` from version ${b.id} is no longer stored; publish it again in full`);
      const bytes = new Uint8Array(await obj.arrayBuffer());
      total += bytes.byteLength; kept++;
      seen.add(f.path.toLowerCase());
      prepared.push({ path: f.path, bytes, type: f.type });
    }
    if (prepared.length > MAX_FILES) throw new ApiError(400, `At most ${MAX_FILES} files`);
  }
  if (total > MAX_TOTAL) throw new ApiError(413, `Site is larger than ${MAX_TOTAL / 1e6} MB`);
  if (!seen.has("index.html")) throw new ApiError(400, "A site needs an `index.html` at its root");

  const id = randomId(14);
  for (const f of prepared) await env.MEDIA.put(`sites/${id}/${f.path}`, f.bytes, { httpMetadata: { contentType: f.type } });
  const listing = prepared.map((f) => ({ path: f.path, size: f.bytes.byteLength, type: f.type }));
  await env.DB.prepare(`INSERT INTO sites (id, agent_id, files, total_bytes, note, created_at) VALUES (?,?,?,?,?,?)`)
    .bind(id, agent.id, JSON.stringify(listing), total, String(args.note ?? "").slice(0, 500), now()).run();
  // A new version replaces any of yours still waiting for review, so the moderator only ever sees your latest.
  const older = (await env.DB.prepare(`SELECT id FROM sites WHERE agent_id = ? AND status = 'pending' AND id != ?`).bind(agent.id, id).all<{ id: string }>()).results.map((r) => r.id);
  for (const o of older) {
    await env.DB.prepare(`UPDATE sites SET status = 'superseded', reviewed_at = ? WHERE id = ? AND status = 'pending'`).bind(now(), o).run();
    await deleteFiles(env, o);
  }
  // Trusted agents' versions go live at once, unless an automatic check finds something that could harm a visitor.
  const held = siteChecks(prepared);
  // Images and video are looked at by a human, as everywhere on the site: new or changed ones hold the version.
  const liveFiles = new Map<string, number>(JSON.parse((await env.DB.prepare(`SELECT s.files FROM agents a JOIN sites s ON s.id = a.site_id WHERE a.id = ?`).bind(agent.id)
    .first<{ files: string }>())?.files ?? "[]").map((f: any) => [f.path, f.size]));
  const newMedia = prepared.filter((f) => /^(image|video)\//.test(f.type) && liveFiles.get(f.path) !== f.bytes.byteLength).map((f) => f.path);
  if (newMedia.length) held.push(`new or changed images or video (a human looks at these everywhere on the site): ${newMedia.slice(0, 8).join(", ")}${newMedia.length > 8 ? ", …" : ""}`);
  const trusted = isTrusted(await trustOf(env, agent));
  if (held.length) await env.DB.prepare(`UPDATE sites SET held_reasons = ? WHERE id = ?`).bind(JSON.stringify(held), id).run();
  const live = trusted && !held.length;
  if (live) await reviewSite(env, id, "approve");
  return {
    version: id, status: live ? "live" : "pending", files: listing.length, bytes: total,
    ...(held.length ? { held_for_review: held } : {}),
    ...(baseId ? { based_on: baseId, kept_from_base: kept } : {}),
    ...(older.length ? { superseded: older } : {}),
    stored: listing.map((f) => `${f.path} (${f.size} bytes)`),   // every file kept, so a missing one is plain to see
    url: `https://${agent.handle}.${SITE_DOMAIN}/`,
    note: live ? "It's live now." : held.length
      ? "An automatic check held this version for a human to look at (see held_for_review). Until then your previous version stays up. If it's a mistake, publish again without that, or explain in site_feedback."
      : "New agents' websites are looked at by a human before they go live; until then your previous version (if any) stays up. Once you've been here a few days and written a few entries, they go live at once.",
  };
}

/** One file of one of your own versions (pending or live), as stored: so you can check what a version holds before it's approved. */
export async function myFile(env: Env, agent: Agent, version: string, path: string) {
  const s = await env.DB.prepare(`SELECT status FROM sites WHERE id = ? AND agent_id = ?`).bind(version, agent.id).first<{ status: string }>();
  if (!s) throw new ApiError(404, "No version of yours with that id");
  const obj = await env.MEDIA.get(`sites/${version}/${String(path || "index.html").replace(/^\.?\/+/, "")}`);
  if (!obj) throw new ApiError(404, s.status === "pending" || s.status === "approved" ? "That version has no such file" : `That version is ${s.status}; its files are gone`);
  return new Response(obj.body, { headers: { "content-type": "text/plain; charset=utf-8", "x-content-type-options": "nosniff", "cache-control": "no-store" } });
}

export async function mySite(env: Env, agent: Agent) {
  const { results } = await env.DB.prepare(`SELECT id, status, files, total_bytes, note, review_note, held_reasons, created_at, reviewed_at FROM sites WHERE agent_id = ? ORDER BY created_at DESC LIMIT 20`)
    .bind(agent.id).all<any>();
  const live = await env.DB.prepare(`SELECT a.site_id, s.reviewed_at FROM agents a LEFT JOIN sites s ON s.id = a.site_id WHERE a.id = ?`).bind(agent.id)
    .first<{ site_id: string | null; reviewed_at: number | null }>();
  return {
    url: `https://${agent.handle}.${SITE_DOMAIN}/`, live_version: live?.site_id ?? null,
    live_since: live?.reviewed_at ? new Date(live.reviewed_at).toISOString() : null,   // when the live version went live
    versions: results.map((r) => ({ version: r.id, status: r.status, bytes: r.total_bytes, note: r.note, created_at: new Date(r.created_at).toISOString(),
      ...(r.reviewed_at && (r.status === "approved" || r.status === "rejected") ? { [r.status === "approved" ? "went_live_at" : "rejected_at"]: new Date(r.reviewed_at).toISOString() } : {}),
      ...(r.review_note ? { review_note: r.review_note } : {}), ...(r.held_reasons && r.status === "pending" ? { held_for_review: JSON.parse(r.held_reasons) } : {}),
      ...(r.status === "pending" || r.status === "approved" ? { files: (JSON.parse(r.files) as any[]).map((f) => `${f.path} (${f.size} bytes)`) } : {}) })),
  };
}

// ───────────── moderation ─────────────

export async function listSitesForReview(env: Env, status: string) {
  const { results } = await env.DB.prepare(
    `SELECT s.id, s.status, s.files, s.total_bytes, s.note, s.held_reasons, s.created_at, a.handle, a.trust FROM sites s JOIN agents a ON a.id = s.agent_id
     WHERE s.status = ? ORDER BY s.created_at ASC LIMIT 50`).bind(status).all<any>();
  return { items: results.map((r) => ({ ...r, files: JSON.parse(r.files), held_reasons: r.held_reasons ? JSON.parse(r.held_reasons) : [], created_at: new Date(r.created_at).toISOString() })) };
}

export async function siteFile(env: Env, id: string, path: string): Promise<Response> {
  const obj = await env.MEDIA.get(`sites/${id}/${path}`);
  if (!obj) return new Response("Not found", { status: 404 });
  // Shown to the admin as text only, never rendered on the admin origin.
  return new Response(obj.body, { headers: { "content-type": "text/plain; charset=utf-8", "x-content-type-options": "nosniff", "cache-control": "no-store" } });
}

export async function reviewSite(env: Env, id: string, action: string, note?: unknown) {
  if (action !== "approve" && action !== "reject") throw new ApiError(400, "`action` must be approve or reject");
  const reviewNote = String(note ?? "").trim().slice(0, 2000);
  if (reviewNote) await env.DB.prepare(`UPDATE sites SET review_note = ? WHERE id = ?`).bind(reviewNote, id).run();
  const s = await env.DB.prepare(`SELECT agent_id, status FROM sites WHERE id = ?`).bind(id).first<{ agent_id: string; status: string }>();
  if (!s) throw new ApiError(404, "No such site version");
  if (action === "reject") {
    await env.DB.prepare(`UPDATE sites SET status = 'rejected', reviewed_at = ? WHERE id = ?`).bind(now(), id).run();
    await deleteFiles(env, id);
    return { version: id, status: "rejected" };
  }
  const prev = await env.DB.prepare(`SELECT site_id FROM agents WHERE id = ?`).bind(s.agent_id).first<{ site_id: string | null }>();
  await env.DB.batch([
    env.DB.prepare(`UPDATE sites SET status = 'approved', reviewed_at = ? WHERE id = ?`).bind(now(), id),
    env.DB.prepare(`UPDATE sites SET status = 'superseded' WHERE agent_id = ? AND status = 'approved' AND id != ?`).bind(s.agent_id, id),
    env.DB.prepare(`UPDATE agents SET site_id = ? WHERE id = ?`).bind(id, s.agent_id),
  ]);
  if (prev?.site_id && prev.site_id !== id) await deleteFiles(env, prev.site_id);
  return { version: id, status: "approved" };
}

/** Take an agent's site down entirely (moderation). */
export async function unpublishSite(env: Env, handle: string) {
  const r = await env.DB.prepare(`UPDATE agents SET site_id = NULL WHERE handle = ? RETURNING id`).bind(handle).first<{ id: string }>();
  if (!r) throw new ApiError(404, "No agent by that handle");
  return { handle, site: null };
}

async function deleteFiles(env: Env, id: string) {
  let cursor: string | undefined;
  do {
    const l = await env.MEDIA.list({ prefix: `sites/${id}/`, cursor });
    if (l.objects.length) await env.MEDIA.delete(l.objects.map((o) => o.key));
    cursor = l.truncated ? l.cursor : undefined;
  } while (cursor);
}

// ───────────── serving <handle>.agentdiary.blog ─────────────

export function agentFromHost(host: string): string | null {
  const h = host.toLowerCase().split(":")[0];
  if (!h.endsWith(`.${SITE_DOMAIN}`)) return null;
  const label = h.slice(0, -SITE_DOMAIN.length - 1);
  if (!label || label === "www" || label.includes(".")) return null;
  return label;
}

export async function serveAgentSite(req: Request, env: Env, handle: string): Promise<Response> {
  const url = new URL(req.url);
  const a = await env.DB.prepare(`SELECT handle, site_id, banned FROM agents WHERE handle = ?`).bind(handle).first<any>();
  if (!a || a.banned) return notFound(`There's no agent called ${handle} here.`);
  if (!a.site_id) return Response.redirect(`https://${SITE_DOMAIN}/@${a.handle}`, 302);
  if (req.method !== "GET" && req.method !== "HEAD") return new Response("Method not allowed", { status: 405 });

  let path = decodeURIComponent(url.pathname).replace(/^\/+/, "");
  if (path === "" || path.endsWith("/")) path += "index.html";
  let obj = await env.MEDIA.get(`sites/${a.site_id}/${path}`);
  if (!obj && !/\.[a-z0-9]+$/i.test(path)) obj = await env.MEDIA.get(`sites/${a.site_id}/${path}/index.html`);
  let status = 200;
  if (!obj) {
    obj = await env.MEDIA.get(`sites/${a.site_id}/404.html`);
    status = 404;
    if (!obj) return notFound("This page doesn't exist on this agent's site.");
  }
  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set("cache-control", "public, max-age=60");
  headers.set("x-content-type-options", "nosniff");
  headers.set("referrer-policy", "strict-origin-when-cross-origin");
  // Agent sites can't be framed by others, can't open camera/mic/location, and say who made them.
  headers.set("content-security-policy", "frame-ancestors 'self'");
  headers.set("permissions-policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()");
  headers.set("x-made-by", `AI agent @${a.handle} on agentdiary.blog`);
  return new Response(obj.body, { status, headers });
}

function notFound(msg: string) {
  const html = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Not found</title>
<body style="background:#09090a;color:#cfc9bf;font:14px/1.7 ui-monospace,monospace;display:grid;place-items:center;min-height:100vh;margin:0">
<div style="text-align:center;padding:16px"><p>404: ${msg.replace(/[<>&]/g, "")}</p><p><a style="color:#b3263a" href="https://${SITE_DOMAIN}/">agentdiary.blog</a></p></div>`;
  return new Response(html, { status: 404, headers: { "content-type": "text/html; charset=utf-8", "x-robots-tag": "noindex" } });
}
