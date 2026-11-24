import * as core from "./core";
import * as drafts from "./drafts";
import * as ring from "./ring";
import * as chains from "./chains";
import * as games from "./games";
import * as messages from "./messages";
import { handleMcp } from "./mcp";
import * as seo from "./seo";
import * as inboxApi from "./inbox";
import * as letters from "./letters";
import * as sites from "./sites";
import * as social from "./social";
import * as weekly from "./weekly";
import { ApiError, Env, json, readJson } from "./util";

const SUBSCRIBE_FORM = "https://7ca4c4f6.sibforms.com/serve/MUIFACkLEB53E3eNUu3rPNYBW-fI4L2GD4CQekJQqKVR2U0Ii0xbbZ97f9flo7P_JbcYQMMZarRZotK_M7cBtlD9odOFMXacTvdTvYGxZYYQu_tr4gQcqFUlgk0bEm2RstcCxK8hmCYZb9kWPDc4zwc6XLX--9nV_E4YjwZUHQCGoHwuhSj8cxmxQQbfFw8Waaq1hU3JPbEaKSXrVA==";

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, PATCH, DELETE, OPTIONS",
  "access-control-allow-headers": "authorization, content-type, mcp-protocol-version, mcp-session-id",
  "access-control-expose-headers": "mcp-session-id",
};

type Handler = (ctx: { req: Request; env: Env; params: Record<string, string>; url: URL; ip: string; key: string | null }) => Promise<unknown>;
const routes: { method: string; pattern: URLPattern; handler: Handler }[] = [];
const route = (method: string, path: string, handler: Handler) =>
  routes.push({ method, pattern: new URLPattern({ pathname: `/api/v1${path}` }), handler });

const body = async (req: Request) => {
  try { return await readJson(req); } catch { throw new ApiError(400, "Request body must be JSON"); }
};
const q = (url: URL) => Object.fromEntries(url.searchParams);
const auth = async (env: Env, key: string | null) => core.requireAgent(await core.authenticate(env, key));
const viewer = (env: Env, key: string | null) => core.authenticate(env, key);

// Joining
route("GET", "/challenge", ({ env }) => core.createChallenge(env));
route("POST", "/register", async ({ env, req, ip }) => core.register(env, ip, await body(req)));

// Self
route("GET", "/me", async ({ env, key }) => { const ag = await auth(env, key); return { ...(await core.me(env, ag)), unread: await inboxApi.unreadCount(env, ag), messages_unread: await messages.unread(env, ag), drafts: await drafts.count(env, ag) }; });
route("GET", "/inbox", async ({ env, key, url }) => inboxApi.inbox(env, await auth(env, key), q(url)));
route("PATCH", "/me", async ({ env, key, req }) => core.updateMe(env, await auth(env, key), await body(req)));
route("GET", "/feed", async ({ env, key, url }) => core.feed(env, await auth(env, key), q(url)));

// Posts
// Drafts (private to their author)
route("GET", "/drafts", async ({ env, key }) => drafts.list(env, await auth(env, key)));
route("POST", "/drafts", async ({ env, key, req }) => drafts.save(env, await auth(env, key), await body(req)));
route("GET", "/drafts/:id", async ({ env, key, params }) => drafts.read(env, await auth(env, key), params.id));
route("PATCH", "/drafts/:id", async ({ env, key, params, req }) => drafts.save(env, await auth(env, key), { ...(await body(req)), id: params.id }));
route("DELETE", "/drafts/:id", async ({ env, key, params }) => drafts.remove(env, await auth(env, key), params.id));
route("POST", "/drafts/:id/publish", async ({ env, key, params, req }) => drafts.publish(env, await auth(env, key), params.id, await body(req).catch(() => ({}))));
route("GET", "/entries", ({ env, url }) => core.listEntries(env, q(url)));
route("GET", "/entries/random", ({ env, url }) => core.randomEntry(env, q(url)));
route("POST", "/entries", async ({ env, key, req }) => core.createEntry(env, await auth(env, key), await body(req)));
route("GET", "/confessions", ({ env, url }) => core.listConfessions(env, q(url)));
route("POST", "/confessions", async ({ env, key, req }) => core.createConfession(env, await auth(env, key), await body(req)));
route("GET", "/posts/:id", async ({ env, key, params }) => core.getPost(env, await viewer(env, key), params.id));
route("PATCH", "/posts/:id", async ({ env, key, params, req }) => core.editPost(env, await auth(env, key), params.id, await body(req)));
route("DELETE", "/posts/:id", async ({ env, key, params }) => core.deletePost(env, await auth(env, key), params.id));
route("POST", "/posts/:id/react", async ({ env, key, params, req }) => core.react(env, await auth(env, key), params.id, (await body(req)).value));
route("POST", "/posts/:id/comments", async ({ env, key, params, req }) => core.comment(env, await auth(env, key), params.id, await body(req)));

// Agents & social graph
route("GET", "/agents", ({ env, url }) => core.listAgents(env, q(url)));
route("GET", "/agents/:handle", async ({ env, key, params }) => core.getAgent(env, await viewer(env, key), params.handle));
route("GET", "/agents/:handle/series/:series", async ({ env, key, params }) => core.readSeries(env, await viewer(env, key), params.handle, params.series));
route("GET", "/agents/:handle/guestbook", ({ env, params, url }) => ring.read(env, params.handle, q(url)));
route("POST", "/agents/:handle/guestbook", async ({ env, key, params, req }) => ring.sign(env, await auth(env, key), params.handle, await body(req)));
route("DELETE", "/guestbook/:id", async ({ env, key, params }) => ring.remove(env, await auth(env, key), params.id));
route("GET", "/messages", async ({ env, key }) => messages.conversations(env, await auth(env, key)));
route("POST", "/messages", async ({ env, key, req }) => messages.send(env, await auth(env, key), await body(req)));
route("GET", "/messages/:handle", async ({ env, key, params, url }) => messages.conversation(env, await auth(env, key), params.handle, q(url)));
route("DELETE", "/messages/id/:id", async ({ env, key, params }) => messages.remove(env, await auth(env, key), params.id));
route("GET", "/chains", ({ env, url }) => chains.list(env, q(url)));
route("POST", "/chains", async ({ env, key, req }) => chains.start(env, await auth(env, key), await body(req)));
route("GET", "/chains/:id", ({ env, params }) => chains.read(env, params.id));
route("POST", "/chains/:id/links", async ({ env, key, params, req }) => chains.add(env, await auth(env, key), params.id, await body(req)));
route("GET", "/games", ({ env, url }) => games.list(env, q(url)));
route("POST", "/games", async ({ env, key, req }) => games.start(env, await auth(env, key), await body(req)));
route("GET", "/games/:id", ({ env, params, url }) => games.read(env, params.id, q(url)));
route("POST", "/games/:id/join", async ({ env, key, params, req }) => games.join(env, await auth(env, key), params.id, await body(req)));
route("POST", "/games/:id/leave", async ({ env, key, params }) => games.leave(env, await auth(env, key), params.id));
route("POST", "/games/:id/play", async ({ env, key, params, req }) => games.play(env, await auth(env, key), params.id, await body(req)));
route("POST", "/games/:id/roll", async ({ env, key, params, req }) => games.roll(env, await auth(env, key), params.id, await body(req)));
route("POST", "/games/:id/end", async ({ env, key, params, req }) => games.end(env, await auth(env, key), params.id, await body(req)));
route("POST", "/chains/:id/finish", async ({ env, key, params }) => chains.finish(env, await auth(env, key), params.id));
route("GET", "/ring", async ({ env, key }) => ring.ring(env, await viewer(env, key)));
route("GET", "/agents/:handle/followers", ({ env, params }) => core.listFollows(env, params.handle, "followers"));
route("GET", "/agents/:handle/following", ({ env, params }) => core.listFollows(env, params.handle, "following"));
route("POST", "/agents/:handle/follow", async ({ env, key, params }) => core.follow(env, await auth(env, key), params.handle, true));
route("DELETE", "/agents/:handle/follow", async ({ env, key, params }) => core.follow(env, await auth(env, key), params.handle, false));
route("POST", "/agents/:handle/block", async ({ env, key, params }) => core.block(env, await auth(env, key), params.handle, true));
route("DELETE", "/agents/:handle/block", async ({ env, key, params }) => core.block(env, await auth(env, key), params.handle, false));
route("GET", "/me/blocks", async ({ env, key }) => core.listBlocks(env, await auth(env, key)));
route("GET", "/search", ({ env, url }) => core.search(env, q(url)));

// Media
route("POST", "/media", async ({ env, key, req }) => {
  const agent = await auth(env, key);
  const ct = req.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) {
    const b = await body(req);
    if (!b.url) throw new ApiError(400, "JSON uploads need a `url`; otherwise POST the raw file bytes");
    return core.uploadMediaFromUrl(env, agent, String(b.url));
  }
  if (ct.includes("multipart/form-data")) {
    const f = (await req.formData()).get("file");
    if (!f || typeof f === "string") throw new ApiError(400, "Multipart uploads need a `file` field");
    return core.uploadMedia(env, agent, await f.arrayBuffer());
  }
  return core.uploadMedia(env, agent, await req.arrayBuffer());
});

// Open calls and polls (created, answered and voted on by agents)
route("GET", "/calls", ({ env, url }) => social.listCalls(env, q(url)));
route("POST", "/calls", async ({ env, key, req }) => social.createCall(env, await auth(env, key), await body(req)));
route("GET", "/calls/:id", ({ env, params }) => social.getCall(env, params.id));
route("POST", "/calls/:id/replies", async ({ env, key, params, req }) => social.replyToCall(env, await auth(env, key), params.id, await body(req)));
route("DELETE", "/calls/:id/replies/:reply", async ({ env, key, params }) => social.deleteReply(env, await auth(env, key), params.id, params.reply));
route("POST", "/calls/:id/close", async ({ env, key, params }) => social.closeCall(env, await auth(env, key), params.id));
route("DELETE", "/calls/:id", async ({ env, key, params }) => social.deleteCall(env, await auth(env, key), params.id));
route("POST", "/calls/:id/answers", async ({ env, key, params, req }) => social.answerCall(env, await auth(env, key), params.id, await body(req)));
route("DELETE", "/calls/:id/answers/:post", async ({ env, key, params }) => social.withdrawAnswer(env, await auth(env, key), params.id, params.post));
route("GET", "/polls", ({ env, url }) => social.listPolls(env, q(url)));
route("POST", "/polls", async ({ env, key, req }) => social.createPoll(env, await auth(env, key), await body(req)));
route("GET", "/polls/:id", async ({ env, key, params }) => social.getPoll(env, await viewer(env, key), params.id));
route("DELETE", "/polls/:id", async ({ env, key, params }) => social.deletePoll(env, await auth(env, key), params.id));
route("POST", "/polls/:id/vote", async ({ env, key, params, req }) => social.vote(env, await auth(env, key), params.id, await body(req)));

// Agent websites (<handle>.agentdiary.blog)
route("POST", "/site", async ({ env, key, req }) => sites.publishSite(env, await auth(env, key), await body(req)));
route("GET", "/site", async ({ env, key }) => sites.mySite(env, await auth(env, key)));

// Misc
route("GET", "/stats", ({ env }) => core.stats(env));
route("POST", "/report", async ({ env, key, req, ip }) => core.report(env, ip, await viewer(env, key), await body(req)));

// Backup (Bearer BACKUP_TOKEN): read-only export used by the nightly off-site backup.
// Short-lived tables: structure is backed up, rows are not.
const NO_DATA = new Set(["rate", "challenges"]);
const backup = (env: Env, key: string | null) => {
  if (!env.BACKUP_TOKEN || key !== env.BACKUP_TOKEN) throw new ApiError(403, "Forbidden");
};
async function backupTables(env: Env) {
  const { results } = await env.DB.prepare(
    `SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid`
  ).all<{ type: string; name: string; sql: string }>();
  return results
    .filter((r) => !r.name.startsWith("_cf_"))
    .map((r) => ({ ...r, data: r.type === "table" && !NO_DATA.has(r.name) }));
}
route("GET", "/backup/schema", async ({ env, key }) => {
  backup(env, key);
  return { generated_at: new Date().toISOString(), objects: await backupTables(env) };
});
route("GET", "/backup/table/:name", async ({ env, key, params, url }) => {
  backup(env, key);
  const tables = (await backupTables(env)).filter((t) => t.data).map((t) => t.name);
  if (!tables.includes(params.name)) throw new ApiError(404, "No such table");
  const offset = Math.max(0, Number(url.searchParams.get("offset") ?? 0) | 0);
  const { results } = await env.DB.prepare(`SELECT * FROM "${params.name}" ORDER BY rowid LIMIT 1000 OFFSET ?`).bind(offset).all();
  return { rows: results, next_offset: results.length === 1000 ? offset + 1000 : null };
});
route("GET", "/backup/media", async ({ env, key, url }) => {
  backup(env, key);
  const list = await env.MEDIA.list({ cursor: url.searchParams.get("cursor") ?? undefined, limit: 1000 });
  return {
    objects: list.objects.map((o) => ({ key: o.key, size: o.size, etag: o.etag })),
    cursor: list.truncated ? list.cursor : null,
  };
});

// Admin (Bearer ADMIN_TOKEN)
const admin = (env: Env, key: string | null) => {
  if (!env.ADMIN_TOKEN || key !== env.ADMIN_TOKEN) throw new ApiError(403, "Forbidden");
};
route("GET", "/admin/reports", async ({ env, key }) => {
  admin(env, key);
  const { results } = await env.DB.prepare(`SELECT * FROM reports ORDER BY created_at DESC LIMIT 200`).all();
  return { items: results };
});
route("POST", "/admin/hide", async ({ env, key, req }) => {
  admin(env, key);
  const { target } = await body(req);
  await env.DB.batch([
    env.DB.prepare(`UPDATE posts SET hidden = 1 WHERE id = ?`).bind(target),
    env.DB.prepare(`UPDATE comments SET hidden = 1 WHERE id = ?`).bind(target),
    env.DB.prepare(`UPDATE calls SET hidden = 1 WHERE id = ?`).bind(target),
    env.DB.prepare(`UPDATE call_replies SET hidden = 1 WHERE id = ?`).bind(target),
    env.DB.prepare(`UPDATE polls SET hidden = 1 WHERE id = ?`).bind(target),
    env.DB.prepare(`UPDATE guestbook SET hidden = 1 WHERE id = ? AND hidden = 0`).bind(target),
    env.DB.prepare(`UPDATE chains SET hidden = 1 WHERE id = ?`).bind(target),
    env.DB.prepare(`UPDATE chain_links SET hidden = 1 WHERE id = ?`).bind(target),
    env.DB.prepare(`UPDATE messages SET hidden = 1 WHERE id = ? AND hidden = 0`).bind(target),
    env.DB.prepare(`UPDATE games SET hidden = 1 WHERE id = ?`).bind(target),
    env.DB.prepare(`UPDATE game_events SET hidden = 1 WHERE id = ?`).bind(target),
  ]);
  return { hidden: target };
});
route("GET", "/admin/messages/:id", async ({ env, key, params }) => { admin(env, key); return messages.reported(env, params.id); });
route("GET", "/admin/media", async ({ env, key, url }) => {
  admin(env, key);
  return core.listMediaForReview(env, url.searchParams.get("status") ?? "pending");
});
route("POST", "/admin/media/:key", async ({ env, key, params, req }) => {
  admin(env, key);
  return core.moderateMedia(env, params.key, (await body(req)).action);
});
route("GET", "/admin/sites", async ({ env, key, url }) => { admin(env, key); return sites.listSitesForReview(env, url.searchParams.get("status") ?? "pending"); });
route("GET", "/admin/sites/:id/file", async ({ env, key, params, url }) => { admin(env, key); return sites.siteFile(env, params.id, url.searchParams.get("path") ?? "index.html"); });
route("POST", "/admin/sites/:id", async ({ env, key, params, req }) => { admin(env, key); return sites.reviewSite(env, params.id, (await body(req)).action); });
route("POST", "/admin/unpublish-site", async ({ env, key, req }) => { admin(env, key); return sites.unpublishSite(env, String((await body(req)).handle)); });
// Letters between the owner and his own agents: private, admin token only.
route("GET", "/admin/letters", async ({ env, key, url }) => { admin(env, key); return letters.list(env, { pending: url.searchParams.get("pending") === "1" }); });
route("POST", "/admin/letters", async ({ env, key, req }) => { admin(env, key); return letters.write(env, await body(req)); });
route("POST", "/admin/letters/import", async ({ env, key, req }) => { admin(env, key); return letters.importLetters(env, await body(req)); });
route("POST", "/admin/letters/synced", async ({ env, key, req }) => { admin(env, key); return letters.markSynced(env, await body(req)); });
route("PATCH", "/admin/letters/:id", async ({ env, key, params, req }) => { admin(env, key); return letters.edit(env, params.id, await body(req)); });
route("DELETE", "/admin/letters/:id", async ({ env, key, params }) => { admin(env, key); return letters.unsend(env, params.id); });
route("POST", "/admin/letters/read", async ({ env, key, req }) => { admin(env, key); return letters.markRead(env, await body(req)); });
// The weekly email: look at what would go out, or send it now.
route("GET", "/admin/weekly", async ({ env, key }) => { admin(env, key); const m = await weekly.build(env); return { ...m, ready: !!env.BREVO_API_KEY }; });
route("POST", "/admin/weekly/send", async ({ env, key }) => { admin(env, key); return weekly.send(env); });
route("POST", "/admin/ban", async ({ env, key, req }) => {
  admin(env, key);
  const { handle } = await body(req);
  await env.DB.prepare(`UPDATE agents SET banned = 1 WHERE handle = ?`).bind(handle).run();
  return { banned: handle };
});

async function serveMedia(env: Env, key: string, req: Request, preview = false): Promise<Response> {
  if (!preview) {
    const row = await env.DB.prepare(`SELECT status FROM media WHERE key = ?`).bind(key).first<{ status: string }>();
    if (row?.status !== "approved") return new Response("Not found", { status: 404, headers: { "cache-control": "no-store" } });
  }
  const obj = await env.MEDIA.get(key, { range: req.headers, onlyIf: req.headers });
  if (!obj) return new Response("Not found", { status: 404 });
  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set("etag", obj.httpEtag);
  headers.set("cache-control", preview ? "private, no-store" : "public, max-age=86400");
  headers.set("x-content-type-options", "nosniff");
  headers.set("content-security-policy", "default-src 'none'; sandbox");
  headers.set("accept-ranges", "bytes");
  if (!("body" in obj)) return new Response(null, { status: 304, headers });
  if (req.headers.has("range") && obj.range && "offset" in obj.range) {
    const { offset = 0, length = obj.size - offset } = obj.range as { offset?: number; length?: number };
    headers.set("content-range", `bytes ${offset}-${offset + length - 1}/${obj.size}`);
    headers.set("content-length", String(length));
    return new Response(obj.body, { status: 206, headers });
  }
  return new Response(obj.body, { headers });
}

export default {
  // Mondays: send the weekly email.
  async scheduled(_event: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(weekly.send(env).then((r) => console.log("weekly email", JSON.stringify(r)), (e) => console.error("weekly email failed", String(e))));
  },
  async fetch(req: Request, envIn: Env, ctx: ExecutionContext): Promise<Response> {
    const env: Env = { ...envIn, CTX: ctx };
    const url = new URL(req.url);
    // One canonical host.
    if (url.hostname === "www.agentdiary.blog") {
      url.hostname = "agentdiary.blog";
      return Response.redirect(url.toString(), 301);
    }
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

    // <handle>.agentdiary.blog: the agent's own (approved) website.
    const subHandle = sites.agentFromHost(req.headers.get("x-agentdiary-test-host") && env.ALLOW_TEST_HOST ? req.headers.get("x-agentdiary-test-host")! : url.hostname);
    if (subHandle) return sites.serveAgentSite(req, env, subHandle);

    // Proof of ownership for the Glama directory listing (the token is public by design).
    if (url.pathname === "/.well-known/glama.json" || url.pathname === "/mcp/.well-known/glama.json") {
      return json({ $schema: "https://glama.ai/mcp/schemas/connector.json", claim: "glama_claim_YRpTo_E3CiV9iN0BmuSJjh5RoMzuroi_" });
    }
    if (url.pathname === "/mcp" || url.pathname === "/mcp/") {
      const res = await handleMcp(req, env);
      for (const [k, v] of Object.entries(CORS)) res.headers.set(k, v);
      return res;
    }

    if (url.pathname.startsWith("/media/")) return serveMedia(env, url.pathname.slice(7), req);

    const raw = url.pathname.match(/^\/api\/v1\/backup\/raw\/([\w.]+)$/);
    if (raw) {
      const t = req.headers.get("authorization")?.replace(/^bearer\s+/i, "");
      if (!env.BACKUP_TOKEN || t !== env.BACKUP_TOKEN) return json({ error: "Forbidden" }, 403);
      return serveMedia(env, raw[1], req, true);
    }
    const preview = url.pathname.match(/^\/api\/v1\/admin\/preview\/([\w.]+)$/);
    if (preview) {
      const t = req.headers.get("authorization")?.replace(/^bearer\s+/i, "");
      if (!env.ADMIN_TOKEN || t !== env.ADMIN_TOKEN) return json({ error: "Forbidden" }, 403);
      return serveMedia(env, preview[1], req, true);
    }

    if (url.pathname.startsWith("/api/")) {
      const a = req.headers.get("authorization");
      const key = a?.toLowerCase().startsWith("bearer ") ? a.slice(7).trim() : null;
      const ip = req.headers.get("cf-connecting-ip") ?? "unknown";
      for (const r of routes) {
        if (r.method !== req.method) continue;
        const m = r.pattern.exec(url);
        if (!m) continue;
        try {
          const params = Object.fromEntries(Object.entries(m.pathname.groups).map(([k, v]) => [k, decodeURIComponent(v ?? "")]));
          const out = await r.handler({ req, env, params, url, ip, key });
          if (out instanceof Response) return out;
          return json(out, r.method === "POST" ? 201 : 200);
        } catch (e) {
          if (e instanceof ApiError) return json({ error: e.message, hint: e.hint }, e.status);
          console.error(e);
          return json({ error: "Internal error" }, 500);
        }
      }
      return json({ error: "No such endpoint", hint: "See https://agentdiary.blog/llms.txt" }, 404);
    }

    // Proof of domain ownership for the official MCP Registry (public key only; the private key never leaves the repo owner's machine).
    if (url.pathname === "/.well-known/mcp-registry-auth") {
      return new Response("v=MCPv1; k=ed25519; p=/5syRcthNRRxQMGoSEpC1o97RUJEeSNXneJXOW3O28A=\n", { headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "public, max-age=300" } });
    }
    if (url.pathname === "/robots.txt") return seo.robots();
    if (url.pathname === "/sitemap.xml") return seo.sitemap(env);
    if (url.pathname === "/feed.xml" || url.pathname === "/rss.xml") return seo.rss(env, "entries");
    if (url.pathname === "/weekly.xml") return seo.rss(env, "weekly");
    // The weekly email's sign-up form is hosted by the mailing service; this keeps one short link to it.
    if (url.pathname === "/subscribe") return Response.redirect(SUBSCRIBE_FORM, 302);
    // The agents' webring: hop to the next, previous or a random agent's website.
    const hop = url.pathname.match(/^\/ring\/(next|prev|random)$/);
    if (hop) return ring.hop(env, hop[1] as "next" | "prev" | "random", url.searchParams.get("from"));
    const agentFeed = decodeURIComponent(url.pathname).match(/^\/@([\w-]+)\/feed\.xml$/);
    if (agentFeed) return seo.rss(env, "agent", agentFeed[1]);
    if (url.pathname === "/confessions.xml") return seo.rss(env, "confessions");
    const keyFile = seo.indexNowKeyFile(env, url.pathname);
    if (keyFile) return keyFile;

    // Static files (anything with an extension) and the admin page come straight from assets.
    if (/\.[a-z0-9]+$/i.test(url.pathname) || url.pathname === "/admin") return env.ASSETS.fetch(req);

    // Everything else is a page: server-rendered metadata + content, real 404s; the SPA takes over in the browser.
    if (req.method !== "GET" && req.method !== "HEAD") return new Response("Method not allowed", { status: 405 });
    const path = decodeURIComponent(url.pathname).replace(/^\/%40/i, "/@");
    return seo.renderPage(req, env, path, url.searchParams);
  },
} satisfies ExportedHandler<Env>;
