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
  /** Weekly email: the mailing service's key (a secret), list and sender. BREVO_API is for tests only. */
  BREVO_API_KEY?: string;
  BREVO_LIST_ID?: string;
  BREVO_SENDER?: string;
  BREVO_API?: string;
  POW_DIFFICULTY?: string;
  REGISTER_PER_HOUR?: string;
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
  if (s.length > max) throw new ApiError(400, `\`${field}\` must be at most ${max} characters`);
  return s;
}

/** Fixed-window rate limit. Throws 429 when exceeded. */
export async function rateLimit(env: Env, bucket: string, limit: number, windowSec: number): Promise<void> {
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
    throw new ApiError(429, `Rate limit exceeded. Try again in ${wait}s.`);
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
