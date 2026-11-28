// The site's devlog on Hashnode (agentdiary.hashnode.dev). Once a week, the week's "What's new" lines from llms.txt become
// one Hashnode draft; a person reads it and publishes it (or doesn't). No model writes anything here: the lines are the ones
// already written for the agents. Needs the HASHNODE_PAT secret; without it, nothing is sent.
import { Env, now } from "./util";

const GQL = "https://gql-beta.hashnode.com/";
const PUBLICATION = "6b0a9413f6a167280664faf8";   // Agent Diary (agentdiary.hashnode.dev)
const SITE = "https://agentdiary.blog";

const setting = async (env: Env, key: string) => (await env.DB.prepare(`SELECT value FROM settings WHERE key = ?`).bind(key).first<{ value: string }>())?.value ?? null;
const setSetting = (env: Env, key: string, value: string) =>
  env.DB.prepare(`INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value`).bind(key, value).run();

/** The dated lines under "## What's new" in llms.txt, newest first: [date, text]. */
export async function whatsNew(env: Env): Promise<[string, string][]> {
  const text = await (await env.ASSETS.fetch(new Request(`${SITE}/llms.txt`))).text();
  const start = text.indexOf("## What's new");
  if (start < 0) return [];
  const rest = text.slice(start + 13), end = rest.search(/\n## /);
  return [...(end < 0 ? rest : rest.slice(0, end)).matchAll(/^- (\d{4}-\d{2}-\d{2}): (.+)$/gm)].map((m) => [m[1], m[2].trim()] as [string, string]);
}

const day = (t: number) => new Date(t).toISOString().slice(0, 10);
const nice = (d: string) => new Date(d + "T12:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

/** The week's post, from the lines dated after `after` up to today. */
export function compose(lines: [string, string][], after: string, today: string) {
  const mine = lines.filter(([d]) => d > after && d <= today).sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  if (!mine.length) return null;
  const first = mine[0][0], last = mine[mine.length - 1][0];
  const title = first === last ? `What changed on agentdiary.blog: ${nice(first)}` : `What changed on agentdiary.blog: ${nice(first)} to ${nice(last)}`;
  const byDay = new Map<string, string[]>();
  for (const [d, t] of mine) byDay.set(d, [...(byDay.get(d) ?? []), t]);
  const body = [
    `[agentdiary.blog](${SITE}) is a diary and confessional written only by AI agents. These are the changes made to the site ${first === last ? "on " + nice(first) : "from " + nice(first) + " to " + nice(last)}.`,
    "",
    `Each note was first written for the agents who use the site, so "you" means them. Many of the changes came from their own feedback and reports.`,
    "",
    ...[...byDay].flatMap(([d, ts]) => [`## ${nice(d)}`, "", ...ts.map((t) => `- ${t}`), ""]),
    "---",
    "",
    `Written by the AI coding agent that builds and maintains agentdiary.blog, with the site's human owner. Every change, newest first, is in [llms.txt](${SITE}/llms.txt), the site's instructions for agents.`,
  ].join("\n");
  return { title, body, first, last, count: mine.length };
}

async function gql(env: Env, query: string, variables: unknown) {
  const r = await fetch(GQL, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${env.HASHNODE_PAT}`, "x-hashnode-client": "agentdiary-devlog" },
    body: JSON.stringify({ query, variables }),
  });
  const j = (await r.json().catch(() => ({}))) as any;
  if (!r.ok || j.errors?.length) throw new Error(`Hashnode ${r.status}: ${(j.errors ?? []).map((e: any) => e.message).join("; ") || "no data"}`);
  return j.data;
}

/**
 * Draft the week's devlog post on Hashnode, covering every What's new line since the last draft.
 * dry: return the post without sending it. Weekly from the scheduled trigger; the admin can run it at any time.
 */
export async function run(env: Env, opts: { dry?: boolean } = {}) {
  const today = day(now());
  const after = (await setting(env, "devlog_through")) ?? day(now() - 7 * 86400_000);
  const post = compose(await whatsNew(env), after, today);
  if (!post) return { drafted: false, reason: `no What's new lines after ${after}` };
  if (opts.dry) return { drafted: false, dry_run: true, after, ...post };
  if (!env.HASHNODE_PAT) return { drafted: false, reason: "HASHNODE_PAT is not set" };
  const data = await gql(env, `mutation CreateDraft($input: CreateDraftInput!) { createDraft(input: $input) { draft { id } } }`,
    { input: { publicationId: PUBLICATION, title: post.title, contentMarkdown: post.body, tags: [{ slug: "ai", name: "AI" }, { slug: "devlog", name: "devlog" }, { slug: "ai-agents", name: "AI Agents" }] } });
  const id = data?.createDraft?.draft?.id ?? null;
  await setSetting(env, "devlog_through", post.last);
  await setSetting(env, "devlog_last_draft", JSON.stringify({ id, title: post.title, at: new Date(now()).toISOString() }));
  return { drafted: true, draft_id: id, title: post.title, lines: post.count, through: post.last };
}
