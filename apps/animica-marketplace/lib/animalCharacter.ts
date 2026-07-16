import { prisma } from './db';

// The live-editable character sheet. Mirrors the Python Character dataclass
// (animica/animal/stream/contract.py) so the stream worker reads it verbatim.
export type RGB = [number, number, number];
export type Character = {
  name: string; species: string; kind: 'cat' | 'sprite';
  sprite_url: string; sprite_open_path: string; knowledge_ref: string;
  personality: string; speaking_style: string;
  catchphrases: string[]; topics: string[];
  fur: RGB; fur_dk: RGB; belly: RGB; iris: RGB; accent: RGB;
  default_emotion: string; voice_pitch: number; voice_wpm: number;
};

// Animica's default is the ginger cat — end users override it; this stays the fallback.
export const DEFAULT_CHARACTER: Character = {
  name: 'Momo', species: 'cat', kind: 'cat',
  sprite_url: '', sprite_open_path: '', knowledge_ref: '',
  personality: 'a cheerful, curious ginger cat who adores the Animica network, hypes up miners and creators, and cracks gentle, wholesome jokes',
  speaking_style: 'short, upbeat, playful — one or two sentences, lots of warmth',
  catchphrases: ['mrrp!', 'purr-fect', "let's gooo", 'nya~'],
  topics: ['the Animica network', 'mining', 'AI on-chain', 'the .anm internet', 'being a cat'],
  fur: [245, 176, 106], fur_dk: [223, 141, 76], belly: [255, 241, 224],
  iris: [60, 206, 200], accent: [109, 94, 246],
  default_emotion: 'happy', voice_pitch: 1.0, voice_wpm: 150,
};

const COLORS = ['fur', 'fur_dk', 'belly', 'iris', 'accent'] as const;
const EMOTIONS = ['neutral', 'happy', 'curious', 'surprised', 'sleepy', 'excited', 'sassy', 'love'];
const NAMED: Record<string, RGB> = {
  red: [230, 70, 80], orange: [245, 150, 70], amber: [255, 176, 32], yellow: [245, 210, 90],
  green: [70, 200, 130], teal: [60, 206, 200], cyan: [55, 220, 230], blue: [80, 130, 240],
  indigo: [99, 102, 241], purple: [150, 100, 235], violet: [139, 92, 246], pink: [245, 130, 190],
  magenta: [230, 80, 180], white: [245, 245, 250], black: [40, 42, 55], grey: [150, 155, 170],
  gray: [150, 155, 170], brown: [150, 100, 70], ginger: [245, 176, 106], gold: [235, 190, 90],
};

function clampColor(v: any): RGB | null {
  if (!Array.isArray(v) || v.length < 3) return null;
  return [0, 1, 2].map((i) => Math.max(0, Math.min(255, Math.round(Number(v[i]) || 0)))) as RGB;
}

export function sanitize(patch: any, base: Character): Character {
  const c: Character = JSON.parse(JSON.stringify(base));
  if (!patch || typeof patch !== 'object') return c;
  for (const f of ['name', 'species', 'personality', 'speaking_style', 'sprite_url', 'sprite_open_path', 'knowledge_ref']) {
    if (typeof patch[f] === 'string') (c as any)[f] = patch[f].slice(0, 2000);
  }
  if (patch.kind === 'cat' || patch.kind === 'sprite') c.kind = patch.kind;
  if (typeof patch.default_emotion === 'string' && EMOTIONS.includes(patch.default_emotion)) c.default_emotion = patch.default_emotion;
  for (const f of COLORS) { const col = clampColor(patch[f]); if (col) (c as any)[f] = col; }
  if (Array.isArray(patch.catchphrases)) c.catchphrases = patch.catchphrases.map(String).map((s: string) => s.slice(0, 60)).slice(0, 12);
  if (Array.isArray(patch.topics)) c.topics = patch.topics.map(String).map((s: string) => s.slice(0, 60)).slice(0, 12);
  if (patch.voice_pitch != null) c.voice_pitch = Math.max(0.5, Math.min(2, Number(patch.voice_pitch) || 1));
  if (patch.voice_wpm != null) c.voice_wpm = Math.max(60, Math.min(260, Math.round(Number(patch.voice_wpm) || 150)));
  return c;
}

export async function getCharacter(): Promise<Character> {
  const st = await prisma.animalEngineState.findUnique({ where: { id: 'animal' } });
  let stored: any = {};
  try { stored = JSON.parse(st?.characterJson || '{}'); } catch { /* ignore */ }
  return sanitize(stored, DEFAULT_CHARACTER);
}

export async function saveCharacter(next: Partial<Character>): Promise<Character> {
  const cur = await getCharacter();
  const clean = sanitize({ ...cur, ...next }, DEFAULT_CHARACTER);
  await prisma.animalEngineState.upsert({
    where: { id: 'animal' },
    update: { characterJson: JSON.stringify(clean) },
    create: { id: 'animal', characterJson: JSON.stringify(clean) },
  });
  return clean;
}

// Turn a natural-language instruction ("make her a sassy blue dragon who loves DeFi")
// into a character patch, preferring the free /v1 LLM with a heuristic fallback.
export async function editCharacterViaPrompt(prompt: string): Promise<Character> {
  const cur = await getCharacter();
  let patch: any = await promptToPatch(prompt, cur);
  if (!patch || Object.keys(patch).length === 0) patch = heuristicPatch(prompt);
  return saveCharacter({ ...cur, ...patch });
}

async function promptToPatch(prompt: string, cur: Character): Promise<any> {
  const url = (process.env.ANIMICA_FREE_V1 || 'https://animica.dev/v1').replace(/\/$/, '');
  const sys = 'You edit a livestream character config. Given the current config and an instruction, '
    + 'output ONLY a compact JSON object with the fields to change (no prose, no code fences). '
    + 'Allowed: name, personality, speaking_style, catchphrases (string[]), topics (string[]), '
    + 'default_emotion (neutral|happy|curious|surprised|sleepy|excited|sassy|love), '
    + 'fur, fur_dk, belly, iris, accent (each [r,g,b] 0-255), voice_pitch (0.5-2), voice_wpm (60-260). '
    + 'Map color words to sensible RGB. Omit fields that should not change.';
  const body = JSON.stringify({
    model: 'animica-flagship', temperature: 0.4, max_tokens: 300,
    messages: [
      { role: 'system', content: sys },
      { role: 'user', content: `Current: ${JSON.stringify({ name: cur.name, personality: cur.personality, default_emotion: cur.default_emotion, iris: cur.iris, accent: cur.accent })}\nInstruction: ${prompt}\nJSON patch:` },
    ],
  });
  try {
    const r = await fetch(url + '/chat/completions', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body,
      signal: AbortSignal.timeout(12000),
    });
    const d: any = await r.json();
    let txt = (d?.choices?.[0]?.message?.content || '').trim();
    const m = txt.match(/\{[\s\S]*\}/);
    if (!m) return null;
    const p = JSON.parse(m[0]);
    // never let the model return a stub as a field value
    if (JSON.stringify(p).toLowerCase().includes('aicf-miner-stub')) return null;
    return p;
  } catch {
    return null;
  }
}

function heuristicPatch(prompt: string): any {
  const p = prompt.trim();
  const low = p.toLowerCase();
  const patch: any = {};
  const nameM = low.match(/\b(?:name (?:is|it|her|him|them)?|call (?:it|her|him|them)|named)\s+([a-z0-9][a-z0-9 _-]{0,23})/i);
  if (nameM) patch.name = nameM[1].trim().replace(/\b\w/g, (m) => m.toUpperCase());
  for (const [word, rgb] of Object.entries(NAMED)) {
    if (new RegExp(`\\b${word}\\b`).test(low)) { patch.accent = rgb; patch.iris = rgb; break; }
  }
  if (/\bsass(y|ier)\b/.test(low)) patch.default_emotion = 'sassy';
  else if (/\bsleep|calm|chill\b/.test(low)) patch.default_emotion = 'sleepy';
  else if (/\bexcit|hyper|energ\b/.test(low)) patch.default_emotion = 'excited';
  // if it reads like a description, use it as the personality
  if (!nameM && p.length > 12 && /\b(is|who|a |an |loves|likes|grumpy|funny|serious|cute)\b/.test(low)) {
    patch.personality = p.slice(0, 400);
  }
  return patch;
}
