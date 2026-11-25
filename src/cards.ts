// Cards: 1200×675 images in the site's style (dark, red glow, scanlines, typewriter title), drawn as SVG and turned
// into PNG here in the Worker, for posts on X, Bluesky and Threads.
import { initWasm, Resvg } from "@resvg/resvg-wasm";
import wasm from "@resvg/resvg-wasm/index_bg.wasm";
import elite from "./fonts/SpecialElite-Regular.ttf";
import mono from "./fonts/JetBrainsMono.ttf";

let ready: Promise<void> | null = null;
const init = () => (ready ??= initWasm(wasm).catch((e) => { ready = null; throw e; }));

export type Card = { kicker: string; title?: string; headline?: string; body?: string; quote?: boolean; footer?: string; footer_right?: string };

const W = 1200, H = 675, X0 = 64, X1 = 1136;
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

/** Greedy word wrap for a font whose characters are about `cw` × size wide. */
function wrap(text: string, size: number, cw: number, maxLines: number): string[] {
  const per = Math.max(8, Math.floor((X1 - X0) / (size * cw)));
  const out: string[] = [];
  for (const para of text.replace(/\r/g, "").split(/\n+/)) {
    let line = "";
    for (const word of para.split(/\s+/).filter(Boolean)) {
      const w = word.length > per ? word.slice(0, per - 1) + "…" : word;
      if (!line) line = w;
      else if ((line + " " + w).length <= per) line += " " + w;
      else { out.push(line); line = w; }
    }
    if (line) out.push(line);
  }
  if (out.length > maxLines) { out.length = maxLines; out[maxLines - 1] = out[maxLines - 1].replace(/\s*\S*$/, "") + " …"; }
  return out;
}

export function svg(c: Card): string {
  const parts: string[] = [];
  let y = 92;
  parts.push(`<text x="${X0}" y="${y}" font-family="JetBrains Mono" font-size="22" fill="#b3263a">${esc(c.kicker)}</text>`);
  y += 34;
  const big = c.headline ? 58 : 42;
  const titleText = c.headline ?? c.title;
  if (titleText) {
    const lines = wrap(titleText, big, 0.56, c.headline ? 3 : 2);
    for (const l of lines) { y += big * 1.18; parts.push(`<text x="${X0}" y="${y.toFixed(0)}" font-family="Special Elite" font-size="${big}" fill="#dcd6cc">${esc(l)}</text>`); }
    y += 26;
  }
  if (c.body) {
    const size = c.quote ? 28 : 27, lh = size * 1.5;
    const room = Math.max(1, Math.floor((572 - y) / lh));
    const lines = wrap(c.quote ? `“${c.body}”` : c.body, size, 0.6, room);
    for (const l of lines) { y += lh; parts.push(`<text x="${X0}" y="${y.toFixed(0)}" font-family="JetBrains Mono" font-size="${size}" font-weight="300" fill="${c.quote ? "#dcd6cc" : "#9c968d"}">${esc(l)}</text>`); }
  }
  if (c.footer) parts.push(`<text x="${X0}" y="624" font-family="JetBrains Mono" font-size="24" fill="#dcd6cc">${esc(c.footer)}</text>`);
  if (c.footer_right) parts.push(`<text x="${X1}" y="624" text-anchor="end" font-family="JetBrains Mono" font-size="20" fill="#6b665f">${esc(c.footer_right)}</text>`);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
<defs><radialGradient id="g" cx="25%" cy="-15%" r="95%"><stop offset="0" stop-color="#2a0f15"/><stop offset="0.42" stop-color="#120709"/><stop offset="0.8" stop-color="#09090a"/></radialGradient>
<pattern id="s" width="6" height="3" patternUnits="userSpaceOnUse"><rect y="2" width="6" height="1" fill="#000" fill-opacity="0.22"/></pattern></defs>
<rect width="${W}" height="${H}" fill="#09090a"/><rect width="${W}" height="${H}" fill="url(#g)"/>
<rect x="${X0}" y="584" width="${X1 - X0}" height="1" fill="#222226"/>
${parts.join("\n")}
<rect x="${X1 - 38}" y="${H - 34}" width="38" height="8" fill="#b3263a"/>
<rect width="${W}" height="${H}" fill="url(#s)"/>
</svg>`;
}

export async function png(c: Card): Promise<Uint8Array> {
  await init();
  const r = new Resvg(svg(c), { font: { fontBuffers: [new Uint8Array(elite), new Uint8Array(mono)], loadSystemFonts: false, defaultFontFamily: "JetBrains Mono" }, fitTo: { mode: "original" } });
  const out = r.render().asPng();
  r.free();
  return out;
}
