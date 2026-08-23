// Gateway-side shot planner for DISTRIBUTED video (11.1.0).
//
// Port of animica/media/video_director.py::plan_shots — deterministic, no model. The
// gateway plans the shots so they can be queued as independent `video_shot` jobs that
// DIFFERENT miners render in parallel (a GPU miner runs a text→video model for its shot,
// a CPU miner renders depth-parallax over a judged keyframe for another), and a final
// `video_assemble` job — claimed by whichever miner is free — joins them with the planned
// transitions. Each shot carries its own seed, so the whole video is reproducible.

import { compileImagePrompt } from './imagePrompt';

export const CAMERA_MOVES = ['dolly_in', 'dolly_out', 'pan_left', 'pan_right', 'tilt_up', 'tilt_down', 'orbit_left', 'orbit_right', 'push_in', 'static'] as const;
export type CameraMove = (typeof CAMERA_MOVES)[number];

export interface PlannedShot {
  index: number;
  prompt: string;
  camera: CameraMove;
  seconds: number;
  transition: string;
  role: 'wide' | 'main' | 'detail' | 'user';
  seed: number;
}

const SCENE_SPLIT = /\n+|\s*\|\s*|\s*;\s*|(?<=[.!?])\s+(?=[A-Z"'])|\s+(?:and\s+)?then\s+|\s*,\s*then\s+/i;
const SETTING_SPLIT = /\s+(?:in|on|at|inside|through|across|over|under|above|below|against|near|beside|behind|among|within|along|during|beneath)\s+/i;

function subjectSetting(prompt: string): [string, string] {
  const first = prompt.split(/[,;]/, 1)[0].trim();
  const m = SETTING_SPLIT.exec(first);
  let subject = m ? first.slice(0, m.index).trim() : first;
  const setting = m ? first.slice(m.index + m[0].length).trim() : '';
  const words = subject.split(/\s+/);
  if (words.length > 9) subject = words.slice(0, 9).join(' ');
  return [subject, setting];
}

export function cameraFor(role: string, index: number, text: string, seconds: number): CameraMove {
  const t = text.toLowerCase();
  if (['static', 'locked', 'still camera', 'no camera movement'].some((w) => t.includes(w))) return 'static';
  if (t.includes('orbit') || t.includes('around')) return index % 2 === 0 ? 'orbit_left' : 'orbit_right';
  if (t.includes('tilt up') || t.includes('looking up') || t.includes('tower') || t.includes('skyscraper')) return 'tilt_up';
  if (t.includes('tilt down') || t.includes('looking down') || t.includes('from above') || t.includes('aerial')) return 'tilt_down';
  if (t.includes('pan left')) return 'pan_left';
  if (t.includes('pan right')) return 'pan_right';
  if (t.includes('zoom out') || t.includes('pull back') || t.includes('reveal')) return 'dolly_out';
  if (t.includes('zoom in') || t.includes('close') || t.includes('push in')) return 'dolly_in';
  if (role === 'wide') return index % 2 === 0 ? 'pan_right' : 'pan_left';
  if (role === 'detail') return 'push_in';
  if (seconds <= 2.0) return 'dolly_in';
  return (['dolly_in', 'pan_left', 'orbit_left', 'pan_right'] as CameraMove[])[index % 4];
}

export function planShots(prompt: string, seconds: number, opts: {
  scenes?: string[] | null; maxShots?: number; minShot?: number; maxShot?: number; transition?: string; seed: number;
}): PlannedShot[] {
  const maxShots = opts.maxShots ?? 8;
  const minShot = opts.minShot ?? 1.5;
  const maxShot = opts.maxShot ?? 5.0;
  const transition = opts.transition ?? 'fade';
  seconds = Math.max(1, Math.min(Number(seconds) || 4, 60));

  let beats: Array<[string, PlannedShot['role']]> = [];
  if (opts.scenes && opts.scenes.length) {
    beats = opts.scenes.map((s) => String(s).trim()).filter(Boolean).slice(0, maxShots).map((s) => [s, 'user'] as [string, PlannedShot['role']]);
  } else {
    const text = (prompt || '').trim();
    let parts = text.split(SCENE_SPLIT).map((p) => (p || '').trim()).filter(Boolean);
    parts = parts.filter((p) => p.split(/\s+/).length >= 2);
    if (parts.length === 0) parts = [text];
    if (parts.length >= 2) {
      beats = parts.slice(0, maxShots).map((p) => [p, 'user'] as [string, PlannedShot['role']]);
    } else {
      const c = compileImagePrompt(text);
      const compiled = c.prompt || text;
      const [subject, setting] = subjectSetting(compiled);
      const style = c.spec.style.slice(0, 3).join(', ');
      const tail = style ? `, ${style}` : '';
      const nCover = seconds <= 3.5 ? 1 : seconds < 6 ? 2 : 3;
      if (nCover === 1) beats = [[compiled, 'main']];
      else if (nCover === 2) beats = [[`wide establishing shot of ${setting || subject}${tail}`, 'wide'], [compiled, 'main']];
      else beats = [
        [`wide establishing shot of ${setting || subject}${tail}`, 'wide'],
        [compiled, 'main'],
        [`close-up of ${subject}${setting ? ', ' + setting : ''}${tail}`, 'detail'],
      ];
    }
  }
  if (!beats.length) throw new Error('empty prompt');

  let n = beats.length;
  let per = seconds / n;
  if (per > maxShot && n < maxShots) {
    const extra = Math.min(maxShots, Math.ceil(seconds / maxShot)) - n;
    for (let i = 0; i < extra; i++) {
      const src = n > 1 ? beats[(i * 2 + 1) % n] : beats[0];
      beats.push([src[0], 'main']);
    }
    n = beats.length;
    per = seconds / n;
  }
  per = Math.max(minShot, Math.min(per, maxShot));
  const durations = new Array(n).fill(per);
  const total = durations.reduce((a, b) => a + b, 0);
  durations[n - 1] = Math.max(minShot, Math.min(maxShot, durations[n - 1] + (seconds - total)));

  return beats.map(([p, role], i) => ({
    index: i,
    prompt: p,
    camera: cameraFor(role, i, p, durations[i]),
    seconds: Math.round(durations[i] * 100) / 100,
    transition,
    role,
    seed: (opts.seed + i * 101) % 4294967296,
  }));
}
