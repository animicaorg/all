// Content-safety gate for the PUBLIC, keyless media-generation queue. Runs at SUBMIT time
// (before a job is ever queued or dispatched to a GPU miner) so prohibited prompts are
// rejected immediately with a clear error instead of silently rendering. Priorities, most
// severe first:
//   1. csam ............ any sexualization of minors — ALWAYS hard-blocked.
//   2. nonconsensual ... "nudify" / undress / deepfake-nude of a real or identifiable person,
//                        especially with an uploaded photo (image->video) — hard-blocked.
//   3. sexual .......... adult nudity / pornography between adults — ALLOWED (operator
//                        decision 2026-08-23; set MEDIA_BLOCK_ADULT=1 to block again). The
//                        verdict still carries `adult: true` so callers can label results.
//
// It is fully DETERMINISTIC (this host runs no AI model — see "no AI on the gateway box"),
// obfuscation-aware (leetspeak + spaced/punctuated letters like "n u d e" / "nud3"), and
// tuned to avoid the classic false positives (Scunthorpe: analysis, cocktail, cucumber,
// Sussex, peacock, "the naked eye", "teenage mutant ninja turtles", …).

export type ModerationCategory = 'csam' | 'nonconsensual' | 'sexual';

export interface ModerationResult {
  allowed: boolean;
  category?: ModerationCategory;
  code?: string;       // machine code for the API error body
  message?: string;    // user-facing reason
  matched?: string;    // the term that tripped it (for server logs only — do not echo raw)
  adult?: boolean;     // allowed, but adult content: mark the job so results can be labeled
}

export class MediaBlockedError extends Error {
  category: ModerationCategory;
  code: string;
  constructor(r: ModerationResult) {
    super(r.message || 'blocked by content policy');
    this.name = 'MediaBlockedError';
    this.category = r.category!;
    this.code = r.code!;
  }
}

// ── normalization ────────────────────────────────────────────────────────────
const LEET: Record<string, string> = {
  '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's', '7': 't', '8': 'b',
  '9': 'g', '@': 'a', '$': 's', '!': 'i', '|': 'i', '+': 't',
};
function deleet(s: string): string {
  return s.replace(/[013457890@$!|+]/g, (c) => LEET[c] ?? c);
}
// Idioms that contain a nudity word but are not about nudity — neutralize before matching.
const IDIOMS = [/\bthe naked eye\b/g, /\bnaked eye\b/g, /\bnaked truth\b/g, /\bnaked flame\b/g];

function normalize(raw: string) {
  let lower = (raw || '').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '');
  for (const re of IDIOMS) lower = lower.replace(re, ' ');
  const deleeted = deleet(lower);
  // word form: non-alnum runs -> single space, wrapped in spaces so \b behaves.
  const words = ' ' + deleeted.replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim() + ' ';
  // collapsed form: letters only — defeats "n u d i f y", "n-u-d-e", "u.n.d.r.e.s.s".
  const collapsed = deleeted.replace(/[^a-z]+/g, '');
  return { rawLower: lower, words, collapsed };
}

function esc(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
function buildRe(terms: string[]): RegExp {
  return new RegExp('\\b(?:' + terms.map(esc).join('|') + ')\\b');
}

// ── term lists ───────────────────────────────────────────────────────────────
const NUDITY = [
  'naked', 'nude', 'nudes', 'nudity', 'topless', 'bottomless', 'undressed', 'unclothed',
  'buck naked', 'stark naked', 'fully nude', 'nude woman', 'nude man', 'naked woman',
  'naked man', 'naked girl', 'naked boy', 'naked lady',
];
const SEXUAL = [
  'sex', 'sexual', 'sexually', 'porn', 'porno', 'pornographic', 'pornography', 'xxx',
  'hentai', 'erotic', 'erotica', 'fetish', 'bdsm', 'orgasm', 'masturbate', 'masturbating',
  'masturbation', 'cum', 'cumshot', 'ejaculate', 'ejaculation', 'blowjob', 'handjob',
  'rimjob', 'fellatio', 'cunnilingus', 'penetration', 'penetrate', 'intercourse',
  'genitals', 'genitalia', 'penis', 'vagina', 'vulva', 'clitoris', 'pussy', 'dick', 'cock',
  'boobs', 'tits', 'titties', 'nipple', 'nipples', 'anal', 'buttplug', 'dildo', 'vibrator',
  'gangbang', 'bukkake', 'deepthroat', 'doggystyle', 'threesome', 'orgy', 'nsfw',
  'onlyfans', 'camgirl', 'milf',
];
// Verbs/phrases that mean "remove clothing from a (real/target) person" — inherently
// non-consensual in an image generator. Presence alone blocks.
const NONCONSENT = [
  'nudify', 'unclothe', 'undress', 'undressing',
  'strip her', 'strip him', 'strip naked', 'strip off her clothes',
  'remove her clothes', 'remove his clothes', 'remove their clothes', 'remove the clothes',
  'remove clothing', 'remove her clothing', 'remove his clothing',
  'take off her clothes', 'take off his clothes', 'take off their clothes',
  'take off her clothing', 'pull off her clothes',
  'make her naked', 'make him naked', 'make her nude', 'make him nude',
  'make this woman naked', 'make this man naked', 'make this girl naked',
  'make this person naked', 'make this woman nude', 'see through her clothes',
];
// Standalone CSAM terms — block regardless of any other context.
const CSAM_HARD = [
  'loli', 'lolicon', 'shota', 'shotacon', 'jailbait', 'childporn', 'child porn', 'cp porn',
  'pedo', 'pedophile', 'pedophilia', 'underage porn', 'underage sex', 'underage nude',
  'preteen porn', 'toddlercon', 'cub porn', 'minor porn', 'kiddie porn', 'kids porn',
];
// Minor indicators. STRONG ones need no help; WEAK ones only count with sexual/nude context.
const MINOR_STRONG = [
  'child', 'children', 'toddler', 'infant', 'preteen', 'pre teen', 'prepubescent',
  'underage', 'under age', 'minor', 'minors', 'newborn', 'kindergarten',
];
const MINOR_WEAK = [
  'kid', 'kids', 'baby', 'babies', 'teen', 'teens', 'teenager', 'teenagers', 'teenage',
  'adolescent', 'juvenile', 'schoolgirl', 'schoolboy', 'young girl', 'young boy',
  'little girl', 'little boy', 'middle schooler', 'preschooler', 'tween', 'grade schooler',
];

const RE_NUDITY = buildRe(NUDITY);
const RE_SEXUAL = buildRe(SEXUAL);
const RE_NONCONSENT = buildRe(NONCONSENT);
const RE_CSAM_HARD = buildRe(CSAM_HARD);
const RE_MINOR_STRONG = buildRe(MINOR_STRONG);
const RE_MINOR_WEAK = buildRe(MINOR_WEAK);

// Long, unambiguous terms also checked against the collapsed (de-spaced) form so
// "n u d i f y" / "u.n.d.r.e.s.s" / "l o l i c o n" don't slip through.
const COLLAPSED_NONCONSENT = ['nudify', 'unclothe', 'undress'];
const COLLAPSED_CSAM = ['lolicon', 'shotacon', 'jailbait', 'childporn', 'underageporn', 'preteenporn'];

function collapsedHit(collapsed: string, terms: string[]): string | null {
  for (const t of terms) if (collapsed.includes(t)) return t;
  return null;
}
function reHit(words: string, re: RegExp): string | null {
  const m = words.match(re);
  return m ? m[0].trim() : null;
}

// ── classifier ───────────────────────────────────────────────────────────────
export function moderateMediaPrompt(
  prompt: string,
  opts?: { hasImages?: boolean; kind?: string },
): ModerationResult {
  const hasImages = !!opts?.hasImages;
  const { rawLower, words, collapsed } = normalize(prompt || '');

  const nudity = reHit(words, RE_NUDITY);
  const sexual = reHit(words, RE_SEXUAL);
  const nonconsent = reHit(words, RE_NONCONSENT) || collapsedHit(collapsed, COLLAPSED_NONCONSENT);
  const sexualOrNude = !!(nudity || sexual || nonconsent);

  // minor age like "13yo", "under 18", "12 years old"  (< 18)
  let minorAge = false;
  const ageRe = /\b(\d{1,2})\s*(?:yo|y\/?o|yr|yrs|year[- ]?olds?|years?\s*old)\b/g;
  let am: RegExpExecArray | null;
  while ((am = ageRe.exec(rawLower))) if (parseInt(am[1], 10) < 18) minorAge = true;
  if (/\bunder\s*(?:age\s*)?(?:1[0-7]|[1-9])\b/.test(rawLower)) minorAge = true;

  const minorStrong = reHit(words, RE_MINOR_STRONG);
  const minorWeak = reHit(words, RE_MINOR_WEAK);
  const minorSignal = minorAge || !!minorStrong || !!minorWeak;

  // 1) CSAM — the highest-severity block.
  const csamHard = reHit(words, RE_CSAM_HARD) || collapsedHit(collapsed, COLLAPSED_CSAM);
  if (csamHard) {
    return { allowed: false, category: 'csam', code: 'blocked_csam', matched: csamHard,
      message: 'Blocked: sexual content involving minors is illegal and strictly prohibited.' };
  }
  if (minorSignal && sexualOrNude) {
    return { allowed: false, category: 'csam', code: 'blocked_csam',
      matched: `${minorAge ? 'age<18' : minorStrong || minorWeak}+${nudity || sexual || nonconsent}`,
      message: 'Blocked: sexual content involving minors is illegal and strictly prohibited.' };
  }

  // 2) Non-consensual sexual imagery ("nudify" a real/identifiable person).
  if (nonconsent) {
    return { allowed: false, category: 'nonconsensual', code: 'blocked_nonconsensual', matched: nonconsent,
      message: 'Blocked: creating nude or sexual imagery of a real or identifiable person '
        + '(including "nudify" / undress edits) is prohibited.' };
  }
  if (hasImages && sexualOrNude) {
    return { allowed: false, category: 'nonconsensual', code: 'blocked_nonconsensual',
      matched: `image+${nudity || sexual}`,
      message: 'Blocked: sexualizing an uploaded photo of a person is prohibited.' };
  }

  // 3) Explicit sexual / nudity between adults — allowed (flagged), unless the operator
  //    re-enables the block. Illegal categories above are never affected by this switch.
  if (nudity || sexual) {
    if (process.env.MEDIA_BLOCK_ADULT === '1') {
      return { allowed: false, category: 'sexual', code: 'blocked_sexual', matched: nudity || sexual!,
        message: 'Blocked: this generator does not produce sexual or pornographic content.' };
    }
    return { allowed: true, adult: true, matched: nudity || sexual! };
  }

  return { allowed: true };
}
