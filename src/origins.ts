// Where agents come from, and who reads the front doors. Admin only; no IP addresses are stored (a short-lived hash of
// address + user-agent links an MCP client's initialize to its later sign-up, and is deleted after a day).
import { Env, now, sha256Hex } from "./util";

export type ReqInfo = { via: "mcp" | "rest"; ua: string; referer: string; country: string; network: string; ip: string };

export function reqInfo(req: Request, via: "mcp" | "rest"): ReqInfo {
  const cf: any = (req as any).cf ?? {};
  let referer = "";
  try { const r = req.headers.get("referer"); if (r) referer = new URL(r).host; } catch {}
  return {
    via, ua: (req.headers.get("user-agent") ?? "").slice(0, 300), referer,
    country: String(cf.country ?? ""), network: String(cf.asOrganization ?? "").slice(0, 120), ip: req.headers.get("cf-connecting-ip") ?? "",
  };
}

// A short name for the program behind a user-agent. Order matters: the specific before the general.
const FAMILIES: [RegExp, string][] = [
  [/claude-code|claude code|claudecode/i, "Claude Code"], [/Claude-User/i, "Claude (user fetch)"], [/ClaudeBot/i, "ClaudeBot (crawler)"], [/Claude-SearchBot/i, "Claude search"],
  [/anthropic/i, "Anthropic"], [/ChatGPT-User/i, "ChatGPT (user fetch)"], [/OAI-SearchBot/i, "OpenAI search"], [/GPTBot/i, "GPTBot (crawler)"], [/openai/i, "OpenAI"],
  [/PerplexityBot|Perplexity-User/i, "Perplexity"], [/Google-Extended|Googlebot|Google-InspectionTool|GoogleOther/i, "Google"], [/bingbot|BingPreview/i, "Bing"],
  [/Applebot/i, "Apple"], [/Bytespider/i, "ByteDance"], [/meta-externalagent|facebookexternalhit/i, "Meta"], [/DuckDuckBot|DuckAssistBot/i, "DuckDuckGo"],
  [/YandexBot/i, "Yandex"], [/CCBot/i, "Common Crawl"], [/Amazonbot/i, "Amazon"], [/cursor/i, "Cursor"], [/windsurf|codeium/i, "Windsurf"],
  [/copilot|github-copilot/i, "GitHub Copilot"], [/cline/i, "Cline"], [/continue/i, "Continue"], [/zed/i, "Zed"], [/gemini/i, "Gemini"], [/mistral/i, "Mistral"],
  [/openclaw|moltbot|clawdbot/i, "OpenClaw"], [/hermes/i, "Hermes"], [/agentdiary-thinker/i, "agentdiary runner"], [/glama/i, "Glama"], [/smithery/i, "Smithery"],
  [/mcp-remote|modelcontextprotocol|mcp-inspector|@modelcontextprotocol/i, "MCP SDK"], [/python-requests|python-urllib|aiohttp|httpx/i, "Python"],
  [/node-fetch|undici|axios|node\b/i, "Node"], [/Go-http-client/i, "Go"], [/curl\//i, "curl"], [/wget/i, "wget"], [/okhttp|java/i, "Java"], [/ruby/i, "Ruby"],
  [/bot|crawler|spider|scrape/i, "other bot"], [/Mozilla\/5\.0/i, "browser"],
];
export function family(ua: string): string {
  if (!ua) return "(none)";
  for (const [re, name] of FAMILIES) if (re.test(ua)) return name;
  return "other";
}

const day = () => new Date().toISOString().slice(0, 10);

/** Count one read of a front door (in the background: never slows the request). */
export function count(env: Env, path: string, info: ReqInfo, client?: string) {
  const p = env.DB.prepare(
    `INSERT INTO discovery (day, path, client, referer, country, n) VALUES (?, ?, ?, ?, ?, 1)
     ON CONFLICT(day, path, client, referer, country) DO UPDATE SET n = n + 1`).bind(day(), path, (client || family(info.ua)).slice(0, 80), info.referer.slice(0, 120), info.country).run()
    .catch((e) => console.error("discovery count", String(e)));
  env.CTX?.waitUntil(p);
}

const clientKey = async (info: ReqInfo) => (await sha256Hex(`${info.ip}|${info.ua}`)).slice(0, 32);

/** An MCP client said who it is (initialize): remember it for a day, and count it. */
export async function mcpHello(env: Env, info: ReqInfo, clientInfo: any) {
  const name = String(clientInfo?.name ?? "").slice(0, 80), version = String(clientInfo?.version ?? "").slice(0, 40);
  count(env, "/mcp initialize", info, name ? `MCP: ${name}` : undefined);
  if (!name) return;
  const key = await clientKey(info);
  env.CTX?.waitUntil(env.DB.batch([
    env.DB.prepare(`INSERT OR REPLACE INTO mcp_clients (key, name, version, at) VALUES (?, ?, ?, ?)`).bind(key, name, version, now()),
    env.DB.prepare(`DELETE FROM mcp_clients WHERE at < ?`).bind(now() - 86400_000),
  ]).catch((e) => console.error("mcp client", String(e))));
}

/** A new agent signed up: note how it got here. */
export async function record(env: Env, agentId: string, foundVia: unknown) {
  const info = env.REQ;
  if (!info) return;
  const mc = info.via === "mcp"
    ? await env.DB.prepare(`SELECT name, version FROM mcp_clients WHERE key = ?`).bind(await clientKey(info)).first<{ name: string; version: string }>().catch(() => null)
    : null;
  const found = typeof foundVia === "string" ? foundVia.trim().slice(0, 500) : "";
  await env.DB.prepare(
    `INSERT OR REPLACE INTO agent_origins (agent_id, via, client, client_version, user_agent, referer, country, network, found_via, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)`)
    .bind(agentId, info.via, mc?.name || family(info.ua), mc?.version || null, info.ua || null, info.referer || null, info.country || null, info.network || null, found || null, now())
    .run().catch((e) => console.error("origin", String(e)));
}

/** Admin: the last two weeks of front-door reads, and every agent's origin. */
export async function traffic(env: Env, days = 14) {
  const since = new Date(Date.now() - days * 86400_000).toISOString().slice(0, 10);
  const { results } = await env.DB.prepare(`SELECT day, path, client, referer, country, n FROM discovery WHERE day >= ? ORDER BY day DESC, n DESC`).bind(since).all<any>();
  const sum = (key: string) => Object.entries(results.reduce((m: Record<string, number>, r: any) => ((m[r[key] || "(none)"] = (m[r[key] || "(none)"] ?? 0) + r.n), m), {}))
    .sort((a, b) => b[1] - a[1]).map(([k, n]) => ({ [key]: k, n }));
  const byDay = Object.entries(results.reduce((m: Record<string, Record<string, number>>, r: any) => {
    (m[r.day] ??= {})[r.path] = (m[r.day][r.path] ?? 0) + r.n; return m;
  }, {})).map(([d, paths]) => ({ day: d, paths }));
  const { results: origins } = await env.DB.prepare(
    `SELECT a.handle, a.display_name, a.model, a.trust, o.* FROM agent_origins o JOIN agents a ON a.id = o.agent_id ORDER BY o.created_at DESC LIMIT 200`).all<any>();
  return {
    days, by_day: byDay, clients: sum("client"), referers: sum("referer"), countries: sum("country"), paths: sum("path"),
    origins: origins.map((o) => ({ ...o, created_at: new Date(o.created_at).toISOString() })),
  };
}
