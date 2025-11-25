# Animica Motion Guide
_Practical rules for using and authoring motion across Web, Wallet, Explorer, Studio, and Press._

Motion at Animica communicates **state, hierarchy, and causality**. It should be **fast**, **subtle**, and **token-driven** so it feels consistent anywhere a user meets the brand.

See also:
- Tokens: `contrib/tokens/tokens.animations.json`
- CSS builds: `contrib/tokens/build/css/tokens.css` / `tokens.dark.css`
- Lottie assets: `contrib/motion/lottie/*`
- SVG spinner: `contrib/motion/svg/animica-spin.svg`

---

## 1) Core principles

- **Meaningful, not decorative** — Every animation must explain something (loading, success, navigation).
- **Crisp & brief** — Prefer shorter durations; most UI motion completes in ≤ 320 ms.
- **Token-driven** — Use durations/easings from tokens; never hard-code ad-hoc curves.
- **Non-blocking** — Don’t lock the user. Allow input during or immediately after transitions.
- **Accessible by default** — Respect “reduce motion”; offer static fallbacks.

---

## 2) Tokens: durations & easings

The **source of truth** lives in `contrib/tokens/tokens.animations.json`. Build outputs also expose them as:
- CSS: `--anm-anim-duration-*`, `--anm-anim-ease-*`
- TS: `tokens.animations.duration.*`, `tokens.animations.easing.*`
- Dart: `tokens.animations.duration.*`, `tokens.animations.easing.*`

**Duration families (examples):**
- `duration.micro` — tiny affordances (icon nudge, ripple)
- `duration.short` — button state, chip, toast in/out
- `duration.medium` — modal, panel, route transition
- `duration.long` — page-level entrance or choreographed sequences

**Easing families:**
- `easing.standard` — default UI in/out
- `easing.emphasized` — stronger accel/decel for entrances
- `easing.decelerate` — quick start, soft settle (entrances)
- `easing.accelerate` — soft start, quick end (exits)

> ⚠️ If you update token values, rebuild the targets:  
> `node contrib/tokens/scripts/build.mjs`

---

## 3) Platform usage

### 3.1 Web (CSS)
```css
/* Token-driven example */
.card-enter {
  animation: cardIn var(--anm-anim-duration-medium) var(--anm-anim-ease-emphasized);
}
@keyframes cardIn {
  from { transform: translateY(12px); opacity: 0; }
  to   { transform: translateY(0);    opacity: 1; }
}

/* Respect reduced motion */
@media (prefers-reduced-motion: reduce) {
  .card-enter { animation: none; }
}
3.2 React + Lottie (lottie-web)
tsx
Copy code
import lottie from 'lottie-web';
import anim from '/contrib/motion/lottie/loading.orb.json';
import { tokens } from '/contrib/tokens/build/ts/tokens';

export function LoadingOrb() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const inst = lottie.loadAnimation({
      container: ref.current!,
      renderer: 'svg',
      loop: true,
      autoplay: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      animationData: anim,
    });
    // Optional: speed alignment with tokens
    inst.setSpeed( (1000 / parseFloat(tokens.animations.duration.long.replace('ms',''))) );
    return () => inst.destroy();
  }, []);
  return <div role="img" aria-label="Loading…" style={{ width: 120, height: 120 }} ref={ref} />;
}
3.3 Flutter
dart
Copy code
import 'package:flutter/animation.dart';
import 'package:lottie/lottie.dart';
import 'tokens.dart' as T; // contrib/tokens/build/dart/tokens.dart

final curve = Cubic(
  T.tokens.animations.easing.standard.x1,
  T.tokens.animations.easing.standard.y1,
  T.tokens.animations.easing.standard.x2,
  T.tokens.animations.easing.standard.y2,
);

final dur = Duration(milliseconds: T.tokens.animations.duration.shortMs);

AnimatedOpacity(
  duration: dur,
  curve: curve,
  opacity: 1,
  child: Lottie.asset('contrib/motion/lottie/success.check.json'),
);
3.4 iOS (Swift) / Android (Kotlin)
Map tokens to CAMediaTimingFunction(controlPoints: x1:y1:x2:y2:) on iOS.

On Android, map to PathInterpolator(x1, y1, x2, y2) and durations in ms.

4) Patterns & defaults
Pattern	Duration Token	Easing Token	Notes
Button press feedback	duration.micro	easing.standard	Scale 0.98 → 1.00, ≤120 ms
Chip toggle	duration.short	easing.standard	Opacity + color tween
Toast in/out	duration.short	easing.decelerate / easing.accelerate	Slide Y 12px
Modal enter	duration.medium	easing.emphasized	Fade+scale 0.98
Route transition	duration.medium	easing.standard	Translate X ≤24px
Loader loop	duration.long	linear (internally)	Keep < 1.6 s/loop
Success check	duration.short	easing.emphasized	Stroke draw + pop
Error shake	duration.short	custom cubic	3 shakes max, no flash

Micro-interactions

Avoid bouncy, springy tails unless justified; two-phase accel/decel is preferred.

Limit blurs and heavy filters for mobile perf.

5) Lottie authoring checklist
Use vector shapes only; avoid rasterized effects.

Target 30 fps (24–30 OK); keep loops seamless.

File size budgets (rough):

Loaders: ≤ 80–120 KB

Success/Error: ≤ 40–80 KB

Micro sparkline: ≤ 20–40 KB

Remove unused assets; compress JSON (minify on build if needed).

Color/opacity should follow brand tokens (map via layer colors).

6) SVG animation guidance
Prefer CSS animations over SMIL for broader control and theming.

Use currentColor and CSS variables to theme.

Keep stroke widths even (1/2px) to avoid jitter.

If masking/filters are necessary, test on low-end mobile.

7) Accessibility
Always provide a static fallback (icon, image).

Honor reduce motion (web media query, platform flags on mobile).

Avoid high-contrast flashing; comply with WCAG 2.3.1 (≤ 3 flashes/sec).

8) Performance & QA
Profile CPU/GPU usage: loaders should idle < 3% CPU on mid-range mobile.

Validate timing vs tokens in code review.

Test at 1× and 2× scale; check for banding in gradients.

Dark & light backgrounds — ensure adequate contrast or documented constraints.

9) Versioning & releases
Any visual/timing change requires a Visuals entry in contrib/CHANGELOG.md.

Keep old assets if removing would break a release branch; mark deprecated and schedule removal.

10) Snippets
CSS variables quick map

css
Copy code
:root {
  /* from tokens.css build */
  /* --anm-anim-duration-micro: ...; */
  /* --anm-anim-ease-standard: cubic-bezier(...); */
}
React utility

ts
Copy code
export const ease = {
  standard: 'var(--anm-anim-ease-standard)',
  emphasized: 'var(--anm-anim-ease-emphasized)',
};
export const dur = {
  short: 'var(--anm-anim-duration-short)',
  medium: 'var(--anm-anim-duration-medium)',
};
Flutter helper

dart
Copy code
Duration dShort(int fallbackMs) => Duration(
  milliseconds: tokens.animations.duration.shortMs ?? fallbackMs,
);
11) Governance of motion
Design proposes updates (tokens + assets) via PR.

Dev verifies builds (CSS/TS/Dart) and app integrations.

QA signs off against this guide and accessibility requirements.

