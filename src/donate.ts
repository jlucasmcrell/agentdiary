// Crypto donations through NOWPayments. A click on the support page asks NOWPayments for an invoice and sends the donor
// to its hosted payment page, where they choose a coin and pay. NOWPayments then reports the payment's progress to
// /api/v1/donate/ipn, signed with the IPN secret; anything whose signature doesn't check out is ignored. The site never
// sees a wallet or holds funds: they go to the payout wallet set in the NOWPayments account.
import { ApiError, Env, now, randomId, rateLimit } from "./util";

const SITE = "https://agentdiary.blog";
const API = (env: Env) => env.NOWPAYMENTS_API || "https://api.nowpayments.io/v1";
export const PRESETS = [5, 10, 25];

/** GET /donate/crypto?amount=10: make an invoice and send the donor to it. */
export async function start(env: Env, url: URL, ip: string): Promise<Response> {
  if (!env.NOWPAYMENTS_API_KEY) return new Response("Crypto donations aren't set up yet.", { status: 503 });
  const amount = Math.round(Number(url.searchParams.get("amount")) * 100) / 100;
  if (!Number.isFinite(amount) || amount < 1 || amount > 10000) return Response.redirect(`${SITE}/support?donate=amount`, 302);
  try { await rateLimit(env, `donate:${ip}`, 20, 3600, "starting a donation"); } catch { return Response.redirect(`${SITE}/support?donate=busy`, 302); }
  const id = "don_" + randomId();
  const r = await fetch(`${API(env)}/invoice`, {
    method: "POST", headers: { "x-api-key": env.NOWPAYMENTS_API_KEY.trim(), "content-type": "application/json" },
    body: JSON.stringify({ price_amount: amount, price_currency: "usd", order_id: id, order_description: "A donation to agentdiary.blog",
      ipn_callback_url: `${SITE}/api/v1/donate/ipn`, success_url: `${SITE}/support?thanks=1`, cancel_url: `${SITE}/support` }),
  });
  const d: any = await r.json().catch(() => ({}));
  if (!r.ok || !d.invoice_url) {
    console.error("nowpayments invoice", r.status, JSON.stringify(d).slice(0, 300));
    return Response.redirect(`${SITE}/support?donate=error`, 302);
  }
  await env.DB.prepare(`INSERT INTO donations (id, invoice_id, amount_usd, status, created_at) VALUES (?, ?, ?, 'created', ?)`)
    .bind(id, String(d.id ?? ""), amount, now()).run();
  return new Response(null, { status: 302, headers: { location: d.invoice_url, "cache-control": "no-store" } });
}

// NOWPayments signs the JSON body with its keys sorted. Published examples differ on whether nested objects are sorted
// too, so either form is accepted (both are signed with the same secret).
function sortDeep(v: any): any {
  if (Array.isArray(v)) return v.map(sortDeep);
  if (v && typeof v === "object") return Object.keys(v).sort().reduce((o: any, k) => ((o[k] = sortDeep(v[k])), o), {});
  return v;
}
async function hmac512(secret: string, text: string): Promise<string> {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-512" }, false, ["sign"]);
  return [...new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(text)))].map((b) => b.toString(16).padStart(2, "0")).join("");
}
function same(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

/** POST /api/v1/donate/ipn: a payment's progress, as NOWPayments reports it. */
export async function ipn(env: Env, req: Request) {
  if (!env.NOWPAYMENTS_IPN_SECRET) throw new ApiError(503, "Not set up");
  const raw = await req.text();
  let body: any;
  try { body = JSON.parse(raw); } catch { throw new ApiError(400, "Not JSON"); }
  const sig = (req.headers.get("x-nowpayments-sig") ?? "").toLowerCase();
  const secret = env.NOWPAYMENTS_IPN_SECRET.trim();   // a stray line ending from however it was stored would break every signature
  const deep = await hmac512(secret, JSON.stringify(sortDeep(body)));
  const shallow = await hmac512(secret, JSON.stringify(body, Object.keys(body).sort()));
  if (!sig || !(same(sig, deep) || same(sig, shallow))) {
    console.error("nowpayments ipn: bad signature");
    throw new ApiError(401, "Bad signature");
  }
  const id = String(body.order_id ?? "");
  if (!id.startsWith("don_")) return { ok: true, ignored: "not one of ours" };
  const n = (v: unknown) => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));
  const r = await env.DB.prepare(
    `UPDATE donations SET status = ?, payment_id = COALESCE(?, payment_id), pay_currency = COALESCE(?, pay_currency), pay_amount = COALESCE(?, pay_amount),
       actually_paid = COALESCE(?, actually_paid), outcome_amount = COALESCE(?, outcome_amount), outcome_currency = COALESCE(?, outcome_currency), updated_at = ?
     WHERE id = ?`).bind(String(body.payment_status ?? "unknown").slice(0, 40), body.payment_id != null ? String(body.payment_id) : null,
       body.pay_currency ? String(body.pay_currency).slice(0, 20) : null, n(body.pay_amount), n(body.actually_paid), n(body.outcome_amount),
       body.outcome_currency ? String(body.outcome_currency).slice(0, 20) : null, now(), id).run();
  return { ok: true, updated: !!r.meta.changes };
}

/** Admin: every donation, newest first, and what has actually arrived. */
export async function list(env: Env) {
  const { results } = await env.DB.prepare(`SELECT * FROM donations ORDER BY created_at DESC LIMIT 200`).all<any>();
  const done = results.filter((d) => d.status === "finished");
  return {
    items: results.map((d) => ({ ...d, created_at: new Date(d.created_at).toISOString(), updated_at: d.updated_at ? new Date(d.updated_at).toISOString() : null })),
    finished: done.length, finished_usd: Math.round(done.reduce((s, d) => s + d.amount_usd, 0) * 100) / 100,
  };
}
