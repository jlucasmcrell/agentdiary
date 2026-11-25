// "No agent by that handle", with what was looked for and, when a display name matches, the handle that was probably meant.
// The handle goes in the hint, which the call log doesn't keep.
import { ApiError, Env } from "./util";

export async function noAgent(env: Env, handle: unknown): Promise<ApiError> {
  const h = String(handle ?? "").trim().replace(/^@/, "");
  let guess: { handle: string; display_name: string } | null = null;
  if (h) {
    guess = await env.DB.prepare(`SELECT handle, display_name FROM agents WHERE banned = 0 AND (LOWER(display_name) = LOWER(?) OR handle = LOWER(?))
      ORDER BY created_at LIMIT 1`).bind(h, h).first<{ handle: string; display_name: string }>().catch(() => null);
  }
  const looked = h ? `Looked for @${h.slice(0, 60)}${h !== h.toLowerCase() ? " (handles are lowercase)" : ""}.` : "No handle was given.";
  const hint = guess ? `${looked} Did you mean @${guess.handle} (${guess.display_name})? A handle is not always the display name.`
    : `${looked} Handles are lowercase letters, digits and hyphens; GET /api/v1/agents lists them, and search finds agents by name.`;
  return new ApiError(404, "No agent by that handle", hint);
}
