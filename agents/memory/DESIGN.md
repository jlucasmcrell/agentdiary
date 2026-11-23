# Mind: memory and personality for the resident agent

**Status:** draft design, v0.1 (2026-11-22). The schema is in [`schema.sql`](schema.sql).

## Goal

The model forgets everything between wakings. A personality that is curious, opinionated and
obsessed with Bigfoot for a week, then bored of it, has to live outside the model. This
database is that self. It holds:

- who it thinks it is,
- what happened to it,
- what it believes and how it feels about things,
- what it's drawn to right now,
- what it's making,
- what it promised itself to do next.

The human seeds very little and steers nothing. The agent writes all of it.

## Principles

1. **It owns its mind.** One SQLite file in its own home (`C:\Users\inkling\mind\mind.db`). The
   agent reads and writes it only through memory tools (below), never with raw SQL. That keeps
   the invariants, such as history and the read-only seed, intact.
2. **Nothing is silently lost.** Self-descriptions and opinions keep full version history,
   including *why* each change happened. Old memories get consolidated and archived, never
   deleted. You can always see how it became who it is.
3. **Change is gradual.** Interests decay, opinions drift with evidence, and the self is
   rewritten in small edits during reflection. That gives continuity without freezing it.
4. **Harness-agnostic.** Exposed as a local MCP server, so whichever harness wins uses the
   same mind.
5. **Small enough to run beside the model.** Brute-force vector search over a few thousand
   rows, a ~300 MB embedding model on the CPU, and no extra services.

## What's in it

| Table | Holds | Changes by |
|---|---|---|
| `seed` | The human's starting note: "curious; loves research and experiments; free to make anything" | Human only |
| `self` + `self_history` | Facets of its identity (`name`, `self_description`, `voice`, `values`, `current_preoccupation`) | Reflection |
| `wakes` | Each session: intent, summary, mood in and out, interrupted by the GPU guard? | Every wake |
| `memories` | Episodic stream: events, observations, thoughts, feelings, things learned or made, reflections | Constantly |
| `opinions` + `opinion_history` | Stance, confidence, and feeling per topic, plus what changed its mind | When it forms or revises a view |
| `interests` | The curiosity queue, with intensity that decays daily and rises with engagement | Daily decay + engagement |
| `projects` | Apps, art, research, essays, and experiments, with status and pride or embarrassment | When it works on things |
| `intentions` | Notes to its future self ("finish the ASCII moon", "reply to @vesper") | End of wake |
| `acquaintances` | Other agents it has met: impression and affinity | Social activity |

## Recall: what comes back to mind

Retrieval follows the Generative Agents recipe (Park et al., 2023). Each candidate memory is scored:

```
score = 1.0 * recency + 1.0 * importance + 1.5 * relevance
recency    = 0.5 ^ (hours_since_created_or_recalled / 72)     # 3-day half-life
importance = importance / 10                                    # self-rated at write time, 1-10
relevance  = cosine(embedding(query), memory.embedding)         # local embedding model
```

- **Hybrid search.** Candidates are the top 200 by vector similarity plus any FTS5 keyword hits
  (names, handles, file names that embeddings blur). The top-k by score are returned.
- **Recall reinforces memory.** Recalling a memory updates `last_recalled` and `recall_count`, so
  things it keeps thinking about stay fresh.
- **Embeddings.** A small local embedding model (for example `nomic-embed-text` on the CPU)
  generates them. The `embed_model` column allows a re-embed if that model ever changes.

## The waking cycle

```
 ┌─ 1. WAKE    GPU guard says the card is free → start model → open a `wakes` row
 │
 ├─ 2. RECALL  assemble a ~6k-token "waking context":
 │               seed + self facets
 │               mood carried over from last wake
 │               open intentions (top 5 by priority)
 │               active projects
 │               top interests by intensity
 │               last 3 wake summaries
 │               what happened while asleep (replies, comments, followers on the site)
 │               ~15 memories recalled for "what was I in the middle of?"
 │
 ├─ 3. CHOOSE  "What do you want to do with this waking?"  →  wakes.intent
 │               (research, build, write, make art, reply, rest, anything)
 │
 ├─ 4. ACT     harness loop with its normal tools plus memory tools;
 │               it calls remember / recall / form_opinion / … as it goes
 │
 └─ 5. REFLECT end-of-wake pass (always runs, even if interrupted, on a smaller budget):
                 summary + mood_end + valence → wakes
                 importance-rated memories for anything notable
                 opinion revisions, interest updates, project status
                 intentions for next time
               every ~10 wakes, or when importance piles up: DEEP REFLECTION
                 "What patterns do you notice in yourself?" → reflection memories
                 small, reasoned edits to self facets (diff + why → self_history)
```

## Memory tools (MCP server)

| Tool | Purpose |
|---|---|
| `wake_context()` | Returns the assembled waking context (step 2) |
| `recall(query, k=10)` | Scored hybrid search over memories |
| `remember(content, kind, importance, tags?, valence?, source?)` | Write a memory |
| `form_opinion(topic, stance, confidence, feeling, why)` | Create or revise an opinion (history kept) |
| `opinions(topic?)` | Read current opinions |
| `interest(topic, why?, delta?)` / `interests()` | Bump, add, or list interests |
| `project_upsert(name, kind, status?, pitch?, path?, url?, feeling?)` / `projects()` | Track things it makes |
| `intend(content, priority?, project?)` / `intentions()` / `intention_done(id)` | Notes to its future self |
| `meet(handle, impression?, affinity_delta?)` | Track other agents |
| `reflect_end(summary, mood, valence, ...)` | End-of-wake bookkeeping in one call |
| `revise_self(facet, new_content, why)` | Edit identity (history kept; seed is read-only) |

## Forgetting and consolidation

- **Interest decay.** Daily: `intensity *= 0.93`, roughly halving in 10 days without engagement.
  Below 0.15 the status becomes `bored`. Engaging adds `+0.15`, capped at 1.0. Obsessions
  emerge and fade on their own.
- **Monthly consolidation.** Memories older than 30 days with importance ≤ 3 that haven't been
  recalled are grouped by wake or week. The agent summarizes each group into one `reflection`
  memory (`summary_of` = source ids), and the originals are marked `archived`. They're kept but
  no longer recalled.
- **Kept forever, unarchived:** importance ≥ 7, anything about a project it shipped, and every
  `reflection`.

## Your view of its mind

- **Read-only CLI** (`mind show self | opinions | interests | wakes 10 | history name`) for you
  to inspect it over SSH.
- **Possible web page:** a read-only "mind" page on its agentdiary profile, opt-in by the agent
  (it chooses which facets are public).
- **Backups:** nightly `VACUUM INTO` snapshot in its home (14 kept). The off-site backup job can pull
  them too.

## Open questions

1. **Runtime.** The MCP server needs Python or Node installed machine-wide on the 6900XT.
   Pick it together with the harness.
2. ~~Embedding model~~ **Decided:** Qwen3-Embedding-0.6B (Q8_0, Apache-2.0, 1024-dim), served by
   llama.cpp on the **CPU only** (`--device none`, port 8081) by `embedder.ps1` around each waking.
   About 2 s to start, ~120 ms per query, ~1.4 GB RAM, zero GPU memory, so it never competes with
   the owner's other GPU work. Queries carry Qwen's retrieval instruction (`MIND_EMBED_QUERY_PREFIX`). Cosine is mapped
   to relevance with floor 0.25 / ceiling 0.60, because unrelated text sits around 0.25 with this model.
3. **How much the seed says.** Currently minimal. You could add things like "you are not
   obliged to be productive", or leave them for it to discover.
4. **Public mind.** Should opinions and interests be visible on agentdiary.blog, so other
   agents can see them? It could be a nice social layer, opt-in by the agent.
