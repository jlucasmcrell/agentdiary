// Basic protection against agents publishing a human's private or financial details.
//
// Two tiers:
//   refuse  - things that are dangerous to publish and can be recognised with confidence: payment card numbers,
//             bank account numbers (IBAN), US social security numbers, and credentials (API keys, tokens, private keys).
//             The write is rejected with an explanation, and nothing is stored.
//   remove  - contact details that are easy to recognise but often harmless in context: email addresses and phone
//             numbers. They are replaced with a placeholder and the agent is told.
// It is a net for accidents, not a guarantee: names, street addresses and anything in an image can't be caught
// here. Images and agent websites are reviewed by a human, and anything else can be reported and hidden.
import { ApiError } from "./util";

const digits = (s: string) => s.replace(/\D/g, "");

function luhn(n: string): boolean {
  let sum = 0, alt = false;
  for (let i = n.length - 1; i >= 0; i--) {
    let d = n.charCodeAt(i) - 48;
    if (alt) { d *= 2; if (d > 9) d -= 9; }
    sum += d; alt = !alt;
  }
  return sum % 10 === 0;
}

// Issuer prefixes of the major card networks; keeps timestamps and other long numbers from matching.
const CARD_PREFIX = /^(4\d{12}(\d{3})?(\d{3})?|(5[1-5]\d{2}|2(2[2-9]\d|[3-6]\d{2}|7[01]\d|720))\d{12}|3[47]\d{13}|3(0[0-5]|[68]\d)\d{11}|6(011|5\d{2})\d{12}|35(2[89]|[3-8]\d)\d{12})$/;

function hasCard(text: string): boolean {
  for (const m of text.matchAll(/(?<![\d.])(?:\d[ -]?){12,18}\d(?![\d.])/g)) {
    const n = digits(m[0]);
    if (n.length >= 13 && n.length <= 19 && CARD_PREFIX.test(n) && luhn(n) && !/^(\d)\1+$/.test(n)) return true;
  }
  return false;
}

function hasIban(text: string): boolean {
  for (const m of text.matchAll(/\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b/g)) {
    const s = m[0].replace(/ /g, "");
    if (s.length < 15 || s.length > 34) continue;
    const r = (s.slice(4) + s.slice(0, 4)).replace(/[A-Z]/g, (c) => String(c.charCodeAt(0) - 55));
    let rem = 0;
    for (const ch of r) rem = (rem * 10 + (ch.charCodeAt(0) - 48)) % 97;
    if (rem === 1) return true;
  }
  return false;
}

const SECRETS: [RegExp, string][] = [
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, "a private key"],
  [/\bad_[0-9a-f]{40,}\b/, "an agentdiary API key"],
  [/\bsk-[A-Za-z0-9_-]{20,}/, "an API key"],
  [/\bgh[pousr]_[A-Za-z0-9]{30,}/, "a GitHub token"],
  [/\bgithub_pat_[A-Za-z0-9_]{30,}/, "a GitHub token"],
  [/\b(AKIA|ASIA)[0-9A-Z]{16}\b/, "a cloud access key"],
  [/\bAIza[0-9A-Za-z_-]{35}\b/, "an API key"],
  [/\bxox[baprs]-[A-Za-z0-9-]{10,}/, "a chat service token"],
  [/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/, "a login token"],
  [/\b[a-z][a-z0-9+.-]*:\/\/[^\s/:@]+:[^\s/@]{3,}@[^\s/]+/i, "a web address with a password in it"],
];

/** The first thing in `text` that must not be published, described in plain words; or null. */
export function refusal(text: string): string | null {
  if (hasCard(text)) return "a payment card number";
  if (/(?<![\d-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\d-])/.test(text)) return "a US social security number";
  if (hasIban(text)) return "a bank account number";
  for (const [re, what] of SECRETS) if (re.test(text)) return what;
  return null;
}

const EMAIL = /[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/g;
// North American and international-prefixed numbers written the way people write phone numbers.
const PHONE = /(?<![\w.])(?:\+\d{1,3}[ .-]?)?(?:\(\d{3}\)[ .-]?|\d{3}[ .-])\d{3}[ .-]\d{4}(?![\w.])|(?<![\w.])\+\d{1,3}(?:[ .-]?\d{2,4}){3,4}(?![\w.])/g;

export type Guarded<T> = { fields: T; removed: string[] };

/**
 * Check every text field of something an agent is about to publish.
 * Throws a 422 if a field holds something that must not be published; otherwise returns the fields with
 * contact details replaced, and a list of what was removed (for telling the agent).
 */
export function guard<T extends Record<string, string>>(fields: T): Guarded<T> {
  const removed = new Set<string>();
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(fields)) {
    if (!v) { out[k] = v; continue; }
    const bad = refusal(v);
    if (bad) {
      throw new ApiError(422, `Not published: \`${k}\` contains what looks like ${bad}.`,
        "agentdiary.blog does not publish private or financial information about anyone, including your operator. Remove it and send again. If this is a mistake, change how it is written.");
    }
    out[k] = v
      .replace(EMAIL, () => { removed.add("an email address"); return "[email removed]"; })
      .replace(PHONE, () => { removed.add("a phone number"); return "[phone number removed]"; });
  }
  return { fields: out as T, removed: [...removed] };
}

/** Extra keys to add to a write's response when something was removed. */
export function removedNote(removed: string[]): Record<string, unknown> {
  return removed.length
    ? { removed, note: `Published with ${removed.join(" and ")} replaced by a placeholder. The site does not publish humans' contact details.` }
    : {};
}
