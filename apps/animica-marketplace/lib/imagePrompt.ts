// Deterministic image-prompt compiler (gateway side; NO model call — this box runs no AI).
//
// Diffusion text encoders are literal and lossy: "Make an image of X" spends scarce CLIP
// tokens on the instruction, "a street with no cars" DRAWS cars (the encoder has no
// negation), and anything past 77 CLIP tokens is silently dropped. This module turns a
// human request into what the renderer actually needs:
//
//   * strips instruction wrappers ("make me a picture of", "show me what X looks like")
//   * moves negations ("without X", "no X", "is not wearing X") into the negative prompt,
//     mapping the common ones to their positive equivalent ("without colour" → monochrome)
//   * extracts a machine-readable spec (quoted text, counts, NxM grids, colors, layout
//     words) the miner's fidelity reranker scores candidates against
//   * estimates the CLIP token budget and flags truncation risk
//
// It is idempotent (compiling a compiled prompt is a no-op) and conservative: it never
// invents content — wording the user wrote is kept verbatim apart from the rewrites above.
//
// Conformance vectors shared with the Python port (animica/media/prompt_spec.py) live in
// python/animica/media/prompt_vectors.json; test/image-prompt.test.ts runs them.

import { randomInt } from 'crypto';

export interface ImagePromptSpec {
  text: string[];                       // quoted strings the image must contain
  counts: { n: number; noun: string }[]; // "nine logs", "two cats"
  grid: [number, number] | null;        // "3x3"
  colors: string[];
  layout: string[];                     // top / bottom / left / right / center / corner / ...
  negated: string[];                    // concepts the user excluded
  style: string[];                      // detected style words (empty = none)
}

export interface CompiledImagePrompt {
  prompt: string;            // cleaned positive prompt
  negative: string;          // merged negative prompt (user's + extracted negations)
  spec: ImagePromptSpec;
  est_tokens: number;        // rough CLIP BPE estimate of `prompt`
  truncation_risk: boolean;  // est_tokens > 75 → a CLIP-only model would drop the tail
  notes: string[];           // what was rewritten (for result meta / transparency)
}

export const CLIP_TOKEN_BUDGET = 75;

const NUMBER_WORDS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, twenty: 20,
  dozen: 12, single: 1, pair: 2, couple: 2,
};

const COLORS = [
  'red', 'orange', 'yellow', 'green', 'blue', 'purple', 'violet', 'pink', 'magenta', 'cyan', 'teal',
  'turquoise', 'brown', 'beige', 'tan', 'black', 'white', 'gray', 'grey', 'gold', 'golden', 'silver',
  'bronze', 'copper', 'navy', 'maroon', 'crimson', 'scarlet', 'indigo', 'lavender', 'lime', 'olive',
  'amber', 'ivory', 'cream', 'charcoal', 'emerald', 'ruby', 'sapphire', 'phthalo',
];

const LAYOUT_WORDS = [
  'top', 'bottom', 'left', 'right', 'center', 'centre', 'middle', 'corner', 'foreground',
  'background', 'above', 'below', 'beside', 'behind', 'front', 'upper', 'lower', 'centered', 'centred',
];

const STYLE_WORDS = [
  'photo', 'photograph', 'photorealistic', 'realistic', 'painting', 'oil', 'watercolor', 'watercolour',
  'sketch', 'drawing', 'illustration', 'vector', 'flat', 'logo', 'icon', 'pixel', 'anime', 'manga',
  'cartoon', '3d', 'render', 'cinematic', 'isometric', 'minimal', 'minimalist', 'line art', 'lineart',
  'ink', 'charcoal', 'pastel', 'comic', 'poster', 'diagram', 'blueprint', 'sticker', 'emoji', 'meme',
  'studio', 'macro', 'wide angle', 'portrait', 'landscape', 'low poly', 'voxel', 'pencil', 'gouache',
  'concept art', 'digital art', 'engraving', 'woodcut', 'clipart', 'clip art',
];

// Words that follow "no" without negating anything.
const NO_STOPLIST = new Set(['one', 'matter', 'doubt', 'longer', 'more', 'way', 'idea', 'problem', 'less', 'other', 'sooner']);

// Negation → {positive replacement, negative phrase}. Keyed by the normalized negated noun.
const NEGATION_MAP: Record<string, { pos?: string; neg: string }> = {
  colour: { pos: 'black and white, monochrome', neg: 'color, colorful, saturated' },
  colours: { pos: 'black and white, monochrome', neg: 'color, colorful, saturated' },
  color: { pos: 'black and white, monochrome', neg: 'color, colorful, saturated' },
  colors: { pos: 'black and white, monochrome', neg: 'color, colorful, saturated' },
  people: { pos: 'empty, deserted', neg: 'people, person, crowd, figures' },
  person: { pos: 'empty, deserted', neg: 'people, person, crowd, figures' },
  humans: { pos: 'empty, deserted', neg: 'people, person, crowd, figures' },
  crowd: { pos: 'empty, deserted', neg: 'people, person, crowd, figures' },
  crowds: { pos: 'empty, deserted', neg: 'people, person, crowd, figures' },
  background: { pos: 'plain white background, isolated', neg: 'cluttered background, scenery' },
  text: { neg: 'text, words, letters, typography, watermark' },
  words: { neg: 'text, words, letters, typography, watermark' },
  letters: { neg: 'text, words, letters, typography, watermark' },
  writing: { neg: 'text, words, letters, typography, watermark' },
  captions: { neg: 'text, words, letters, typography, watermark' },
  caption: { neg: 'text, words, letters, typography, watermark' },
  watermark: { neg: 'watermark, signature, logo' },
  watermarks: { neg: 'watermark, signature, logo' },
  shadows: { pos: 'flat even lighting', neg: 'shadows, shading' },
  shadow: { pos: 'flat even lighting', neg: 'shadows, shading' },
};

function esc(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function squash(s: string): string {
  return s
    .replace(/\s+/g, ' ')
    .replace(/\s+([,.;:!?])/g, '$1')
    .replace(/([,;:])\1+/g, '$1')
    .replace(/,\s*,/g, ',')
    .replace(/^[\s,;:.]+/, '')
    .replace(/[\s,;:.]+$/, '')
    .trim();
}

// Instruction wrappers, applied iteratively at the head of the prompt.
const HEAD_PATTERNS: RegExp[] = [
  /^(?:please|pls|kindly|hey|hi|ok|okay)[,!]?\s+/i,
  /^(?:can|could|would|will)\s+you\s+(?:please\s+)?/i,
  /^(?:i\s+(?:want|need|would like|'d like|d like)\s+(?:you\s+to\s+)?|i\s+want\s+|i\s+need\s+)/i,
  /^(?:make|create|generate|draw|paint|render|design|produce|build|give|show|imagine|illustrate|depict|visualize|visualise|picture)\s+(?:me\s+|us\s+)?(?:an?\s+|the\s+|some\s+)?(?:(?:high[- ]quality|detailed|nice|cool|beautiful|realistic|quick|simple)\s+)?(?:image|picture|photo|photograph|pic|graphic|artwork|visual|render|rendering|illustration|drawing|painting|shot)s?\s+(?:of|showing|depicting|with|that shows|featuring)\s+/i,
  /^(?:make|create|generate|draw|design|produce|build|imagine|illustrate|depict|visualize|visualise)\s+(?:me\s+|us\s+)?(?!of\b)(?=\S)/i,
  /^(?:an?\s+|the\s+)?(?:image|picture|photo|photograph|pic|graphic|artwork)\s+(?:of|showing|depicting)\s+/i,
  /^(?:show|give)\s+me\s+(?=\S)/i,
];

function stripInstructions(p: string, notes: string[]): string {
  let out = p;
  // "show me what X looks like Y" / "what does X look like" → "X Y"
  const look = out.match(/^(?:show\s+me\s+)?what\s+(?:does\s+|do\s+)?(.+?)\s+looks?\s+like\b[,:]?\s*(.*)$/i);
  if (look) {
    out = squash(`${look[1]} ${look[2] || ''}`);
    notes.push('stripped "what X looks like" wrapper');
  }
  for (let i = 0; i < 4; i++) {
    let changed = false;
    for (const re of HEAD_PATTERNS) {
      const next = out.replace(re, '');
      if (next !== out && next.trim().length > 0) {
        out = next;
        changed = true;
      }
    }
    if (!changed) break;
  }
  out = squash(out);
  if (out !== squash(p) && !notes.some((n) => n.startsWith('stripped'))) notes.push('stripped instruction wrapper');
  return out;
}

function extractQuoted(p: string): { text: string[]; masked: string; restore: (s: string) => string } {
  const text: string[] = [];
  const holders: string[] = [];
  const masked = p.replace(/"([^"]{1,80})"|“([^”]{1,80})”|'([^']{2,80})'(?=\s|[,.;:!?]|$)/g, (m, a, b, c) => {
    const inner = (a ?? b ?? c ?? '').trim();
    if (!inner) return m;
    text.push(inner);
    holders.push(`"${inner}"`);
    return ` ${holders.length - 1} `;
  });
  const restore = (s: string) => s.replace(/ (\d+) /g, (_m, i) => holders[Number(i)] ?? '');
  return { text, masked, restore };
}

const NEG_TAIL = '(?=[,.;:!?]|$|\\s+(?:and|but|with|in|on|at|that|which|while|under|over|near|beside|behind|against)\\b)';
const NEG_PATTERNS: RegExp[] = [
  new RegExp(`\\b(?:with\\s+no|without(?:\\s+any)?|with\\s+zero|no)\\s+((?:[a-z][a-z'\\-]*)(?:\\s+[a-z][a-z'\\-]*){0,3}?)${NEG_TAIL}`, 'gi'),
  new RegExp(`\\b(?:who|that|which)?\\s*(?:is|are|isn't|aren't|is\\s+not|are\\s+not)\\s+(?:not\\s+)?(?:wearing|holding|carrying|showing|using)\\s+((?:[a-z][a-z'\\-]*)(?:\\s+[a-z][a-z'\\-]*){0,3}?)${NEG_TAIL}`, 'gi'),
];

function extractNegations(masked: string, notes: string[]): { positive: string; negated: string[]; negPhrases: string[]; posAdds: string[] } {
  let positive = masked;
  const negated: string[] = [];
  const negPhrases: string[] = [];
  const posAdds: string[] = [];
  for (const re of NEG_PATTERNS) {
    positive = positive.replace(re, (m: string, phrase: string) => {
      // "is wearing X" (no negation) must not match the second pattern.
      if (/\b(?:is|are)\s+(?:wearing|holding|carrying|showing|using)/i.test(m) && !/not|n't/i.test(m)) return m;
      const key = phrase.trim().toLowerCase().replace(/^(?:a|an|the|any|some)\s+/, '');
      const first = key.split(/\s+/)[0];
      if (!key || NO_STOPLIST.has(first)) return m;
      if (/ /.test(phrase)) return m; // never negate quoted text
      negated.push(key);
      const mapped = NEGATION_MAP[key] ?? NEGATION_MAP[first];
      negPhrases.push(mapped ? mapped.neg : key);
      if (mapped?.pos) posAdds.push(mapped.pos);
      return ' ';
    });
  }
  if (negated.length) {
    notes.push(`moved to negative prompt: ${negated.join(', ')}`);
    // Removing "with no X" can strand a conjunction: "sunrise , and" → "sunrise".
    positive = positive
      .replace(/\b(?:and|or|but|with|without|plus)\s*(?=[,;.:]|$)/gi, '')
      .replace(/([,;])\s*(?:and|or|but)\b\s*/gi, '$1 ')
      .replace(/^\s*(?:and|or|but)\b\s*/i, '');
  }
  return { positive: squash(positive), negated, negPhrases, posAdds };
}

export function estimateClipTokens(s: string): number {
  const words = s.replace(/ \d+ /g, 'x').match(/[A-Za-z0-9]+|[^\sA-Za-z0-9]/g) || [];
  let n = 0;
  for (const w of words) {
    if (/^[A-Za-z0-9]+$/.test(w)) n += 1 + Math.floor(Math.max(0, w.length - 2) / 6);
    else n += 1;
  }
  return n;
}

function dedupeJoin(parts: string[]): string {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const p of parts.flatMap((x) => x.split(',')).map((x) => squash(x)).filter(Boolean)) {
    const k = p.toLowerCase();
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(p);
  }
  return out.join(', ');
}

export function compileImagePrompt(raw: string, opts: { negative?: string | null } = {}): CompiledImagePrompt {
  const notes: string[] = [];
  const original = squash(String(raw ?? ''));
  if (!original) {
    return { prompt: '', negative: squash(opts.negative ?? ''), spec: emptySpec(), est_tokens: 0, truncation_risk: false, notes };
  }

  // 1. quoted text is opaque to every rewrite below.
  const q = extractQuoted(original);
  // 2. instruction wrappers.
  let work = stripInstructions(q.masked, notes);
  // 3. negations → negative prompt.
  const neg = extractNegations(work, notes);
  work = neg.positive;
  // 4. positive replacements for mapped negations ("without colour" → monochrome).
  if (neg.posAdds.length) {
    const adds = neg.posAdds.filter((a) => !work.toLowerCase().includes(a.split(',')[0].trim().toLowerCase()));
    if (adds.length) work = squash(`${work}, ${dedupeJoin(adds)}`);
  }
  const prompt = squash(q.restore(work));

  // 5. spec extraction (on the restored prompt, quoted text excluded where noted).
  const spec = extractSpec(prompt, q.text, neg.negated);

  // 6. negative prompt: user's + extracted. Quality negatives are added miner-side only for
  //    models that honor CFG — the gateway never guesses what the renderer supports.
  const negative = dedupeJoin([squash(opts.negative ?? ''), ...neg.negPhrases]);

  const est = estimateClipTokens(prompt);
  const truncation_risk = est > CLIP_TOKEN_BUDGET;
  if (truncation_risk) notes.push(`~${est} CLIP tokens (budget ${CLIP_TOKEN_BUDGET}) — put the most important details first`);

  return { prompt, negative, spec, est_tokens: est, truncation_risk, notes };
}

function emptySpec(): ImagePromptSpec {
  return { text: [], counts: [], grid: null, colors: [], layout: [], negated: [], style: [] };
}

export function extractSpec(prompt: string, quoted: string[], negated: string[]): ImagePromptSpec {
  const spec = emptySpec();
  spec.text = quoted.slice();
  spec.negated = negated.slice();
  // Drop quoted segments so "OPEN 24h" doesn't register as a count.
  const body = prompt.replace(/"[^"]*"/g, ' ').toLowerCase();

  const grid = body.match(/\b(\d{1,2})\s*[x×]\s*(\d{1,2})\b/);
  if (grid) spec.grid = [Number(grid[1]), Number(grid[2])];

  const countRe = new RegExp(`\\b(${Object.keys(NUMBER_WORDS).join('|')}|\\d{1,2})\\s+(?:(?:${COLORS.join('|')}|small|large|big|tiny|little|huge|giant|identical|different|matching|wooden|metal|glass|stone|round|square)\\s+){0,2}([a-z]{3,}s?)\\b`, 'g');
  let m: RegExpExecArray | null;
  while ((m = countRe.exec(body))) {
    const n = NUMBER_WORDS[m[1]] ?? Number(m[1]);
    const noun = m[2];
    if (!Number.isFinite(n) || n <= 0) continue;
    if (/^(?:x|by|of|and|with|in|on|at|to|for|the|k|px|mm|cm|inch|inches|hours?|minutes?|years?|steps?|percent|degrees?)$/.test(noun)) continue;
    spec.counts.push({ n, noun });
  }

  // In order of appearance (the first-mentioned color/position is usually the subject's).
  const inOrder = (words: string[]) => {
    const re = new RegExp(`\\b(${words.map(esc).join('|')})\\b`, 'g');
    const out: string[] = [];
    let mm: RegExpExecArray | null;
    while ((mm = re.exec(body))) if (!out.includes(mm[1])) out.push(mm[1]);
    return out;
  };
  spec.colors = inOrder(COLORS);
  spec.layout = inOrder(LAYOUT_WORDS);
  spec.style = inOrder(STYLE_WORDS);
  return spec;
}

// ── Size hygiene ─────────────────────────────────────────────────────────────
// Diffusion UNets want /64 dimensions (the 8x VAE stride × the deepest 8x UNet stride);
// off-grid sizes render with edge artifacts and worse composition. Snap to the nearest
// multiple of 64 inside [lo, hi], keeping the requested aspect ratio.
export function snapImageSize(width: number, height: number, lo = 256, hi = 1280): { width: number; height: number } {
  const snap = (v: number) => Math.max(lo, Math.min(hi, Math.round(v / 64) * 64));
  return { width: snap(width), height: snap(height) };
}

// Deterministic 32-bit seed when the caller gave none — recorded on the job so any result
// can be reproduced exactly (same prompt + seed + model + steps ⇒ same pixels).
export function pickSeed(given: unknown): number {
  const n = Number(given);
  if (Number.isFinite(n) && n >= 0) return Math.floor(n) % 4294967296;
  return randomInt(0, 4294967295);
}

export const PRECISIONS = ['fast', 'balanced', 'high'] as const;
export type Precision = (typeof PRECISIONS)[number];

export function normalizePrecision(v: unknown): Precision {
  const s = String(v ?? '').toLowerCase();
  return (PRECISIONS as readonly string[]).includes(s) ? (s as Precision) : 'balanced';
}
