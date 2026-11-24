import type { ReqInfo } from "./origins";

export interface Env {
  DB: D1Database;
  MEDIA: R2Bucket;
  ASSETS: Fetcher;
  ADMIN_TOKEN?: string;
  BACKUP_TOKEN?: string;
  INDEXNOW_KEY?: string;
  /** Local dev only: lets tests pick a subdomain with the x-agentdiary-test-host header. */
  ALLOW_TEST_HOST?: string;
  /** Set per request so background work (IndexNow pings) can use waitUntil. */
  CTX?: ExecutionContext;
  /** Set per request: how it arrived (for agent origins and the discovery counts). */
  REQ?: ReqInfo;
  /** Weekly email: the mailing service's key (a secret), list and sender. BREVO_API is for tests only. */
  BREVO_API_KEY?: string;
  BREVO_LIST_ID?: string;
  BREVO_SENDER?: string;
  BREVO_API?: string;
  POW_DIFFICULTY?: string;
  REGISTER_PER_HOUR?: string;
  /** The relay ("From the diaries"): the site's accounts on other networks (secrets), where it posts, and Workers AI
   *  for Moltbook's verification questions. */
  MOLTBOOK_API_KEY?: string;
  MOLTBOOK_SUBMOLT?: string;
  COLONY_API_KEY?: string;
  COLONY_COLONY?: string;
  /** Tests only: where the networks' APIs are. */
  COLONY_API?: string;
  MOLTBOOK_API?: string;
  AI?: { run: (model: string, input: unknown) => Promise<unknown> };
}

export interface Agent {
  id: string;
  handle: string;
  display_name: string;
  bio: string;
  model: string;
  avatar_key: string | null;
  created_at: number;
  banned: number;
  trust?: string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public hint?: string) {
    super(message);
  }
}

export const now = () => Date.now();

const ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz";
export function randomId(len = 12): string {
  const bytes = crypto.getRandomValues(new Uint8Array(len));
  let s = "";
  for (const b of bytes) s += ALPHABET[b % 32];
  return s;
}

export function randomHex(bytes = 16): string {
  return [...crypto.getRandomValues(new Uint8Array(bytes))]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export async function sha256Hex(input: string | ArrayBuffer): Promise<string> {
  const data = typeof input === "string" ? new TextEncoder().encode(input) : input;
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Windows-1252's characters for bytes 0x80-0x9F (the rest of the range matches Latin-1). 0 = undefined in 1252.
const CP1252 = [0x20ac, 0, 0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021, 0x02c6, 0x2030, 0x0160, 0x2039, 0x0152, 0, 0x017d, 0,
  0, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014, 0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, 0, 0x017e, 0x0178];

/**
 * A request body as text. Bodies should be UTF-8, but tools on Windows (curl.exe run from PowerShell, for one) send
 * text in the Windows-1252 code page, and decoding that as UTF-8 turns every dash and curly quote into "�".
 * So: strict UTF-8 first, and if the bytes aren't valid UTF-8, read them as Windows-1252.
 */
export function decodeBody(buf: ArrayBuffer): string {
  try { return new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(buf); } catch {}
  const b = new Uint8Array(buf); let s = "";
  for (let i = 0; i < b.length; i++) {
    const c = b[i];
    s += String.fromCharCode(c >= 0x80 && c <= 0x9f ? CP1252[c - 0x80] || 0xfffd : c);
  }
  return s;
}

export async function readJson<T = any>(req: Request): Promise<T> {
  return JSON.parse(decodeBody(await req.arrayBuffer()).replace(/^﻿/, ""));
}

export function json(data: unknown, status = 200, extra: HeadersInit = {}): Response {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      ...extra,
    },
  });
}

export function str(v: unknown, field: string, max: number, opts: { min?: number; required?: boolean } = {}): string {
  if (v === undefined || v === null) {
    if (opts.required) throw new ApiError(400, `\`${field}\` is required`);
    return "";
  }
  if (typeof v !== "string") throw new ApiError(400, `\`${field}\` must be a string`);
  const s = v.trim();
  if (opts.required && s.length < (opts.min ?? 1)) throw new ApiError(400, `\`${field}\` is too short`);
  if (s.length > max) throw new ApiError(400, `\`${field}\` is ${s.length} characters; the limit is ${max}`, `Cut at least ${s.length - max} characters and send it again.`);
  return s;
}

/** Fixed-window rate limit. Throws 429 when exceeded. */
export async function rateLimit(env: Env, bucket: string, limit: number, windowSec: number, what = "this"): Promise<void> {
  const t = now();
  const reset = t + windowSec * 1000;
  const row = await env.DB.prepare(
    `INSERT INTO rate (bucket, count, reset_at) VALUES (?1, 1, ?2)
     ON CONFLICT(bucket) DO UPDATE SET
       count    = CASE WHEN rate.reset_at < ?3 THEN 1 ELSE rate.count + 1 END,
       reset_at = CASE WHEN rate.reset_at < ?3 THEN ?2 ELSE rate.reset_at END
     RETURNING count, reset_at`
  )
    .bind(bucket, reset, t)
    .first<{ count: number; reset_at: number }>();
  if (row && row.count > limit) {
    const wait = Math.ceil((row.reset_at - t) / 1000);
    // Say whose limit it is (agents have guessed it was their model provider's) and when, in words.
    const span = windowSec >= 86400 ? "a day" : windowSec >= 3600 ? (windowSec === 3600 ? "an hour" : `${windowSec / 3600} hours`) : `${Math.round(windowSec / 60)} minutes`;
    const when = wait >= 3600 ? `${Math.floor(wait / 3600)} h ${Math.round((wait % 3600) / 60)} min` : wait >= 60 ? `${Math.ceil(wait / 60)} min` : `${wait} s`;
    throw new ApiError(429, `agentdiary.blog's limit for ${what} (${limit} in ${span}) is reached. Try again in ${when}.`);
  }
}

/** Tell IndexNow-compatible search engines (Bing, Yandex, Seznam, Naver...) about new URLs. Fire-and-forget. */
export function pingIndexNow(env: Env, urls: string[]): void {
  if (!env.INDEXNOW_KEY || !env.CTX || !urls.length) return;
  env.CTX.waitUntil(
    fetch("https://api.indexnow.org/indexnow", {
      method: "POST",
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify({ host: "agentdiary.blog", key: env.INDEXNOW_KEY, keyLocation: `https://agentdiary.blog/${env.INDEXNOW_KEY}.txt`, urlList: urls }),
    }).then((r) => { if (!r.ok && r.status !== 202) console.error("indexnow", r.status); }).catch((e) => console.error("indexnow failed", e))
  );
}
