# Animica Motion

Motion assets (Lottie JSON, lightweight SVG animations, and previews) shared across **Website**, **Explorer**, **Wallet Extension**, **Flutter Wallet**, **Studio**, and **press**. Motion follows our design tokens and respects platform “reduce motion” settings.

See also: `contrib/motion/MOTION_GUIDE.md` for detailed principles, timing curves, and review checklists.

---

## What’s inside

contrib/motion/
├─ README.md
├─ MOTION_GUIDE.md # Principles, do’s/don’ts, QA checklist
├─ lottie/
│ ├─ loading.orb.json # looping loader (orb pulsing)
│ ├─ success.check.json # success morph checkmark
│ ├─ error.shake.json # failure shake + tint
│ └─ gamma.spark.json # Γ sparkline micro-anim (dashboards)
├─ svg/
│ └─ animica-spin.svg # CSS-driven spinner (fallback/minimal)
└─ previews/
└─ loading.gif # visual reference for PRs / docs

csharp
Copy code

**Naming**: `action.subject.variant.json` (e.g., `success.check.json`)  
**Duration targets**: micro (≤600ms), small (600–900ms), medium (900–1400ms). Loaders can loop at ~1.2–1.6s.

---

## Using Lottie

### Web (React + lottie-web)
```ts
import lottie from 'lottie-web';
import animationData from '/contrib/motion/lottie/loading.orb.json';

export function LoadingOrb() {
  const ref = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    const inst = lottie.loadAnimation({
      container: ref.current!,
      renderer: 'svg',
      loop: true,
      autoplay: true,
      animationData,
    });
    return () => inst.destroy();
  }, []);
  return <div aria-label="Loading…" role="img" style={{ width: 120, height: 120 }} ref={ref} />;
}
Respect reduced motion (web):

css
Copy code
@media (prefers-reduced-motion: reduce) {
  .lottie { animation: none !important; }
}
In JS, conditionally avoid autoplay or render a static frame when prefers-reduced-motion: reduce.

Flutter
dart
Copy code
import 'package:lottie/lottie.dart';
import 'package:flutter/widgets.dart';

class LoadingOrb extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final reduceMotion = MediaQuery.of(context).disableAnimations
        || MediaQuery.of(context).accessibilityFeatures.disableAnimations;
    if (reduceMotion) {
      return Image.asset('assets/images/loader_static.png', width: 120, height: 120);
    }
    return Lottie.asset('contrib/motion/lottie/loading.orb.json', width: 120, height: 120, repeat: true);
  }
}
iOS (Swift)
Use [Lottie iOS]. Load from the repo path or bundle.

Disable animations if UIAccessibility.isReduceMotionEnabled == true.

Android (Kotlin)
Use [Lottie Android] LottieAnimationView.

Check Settings.Global.TRANSITION_ANIMATION_SCALE or app-level preference to reduce motion.

File size & performance
Targets: loaders ≤ 80–120 KB, success/error ≤ 40–80 KB, micro sparklines ≤ 20–40 KB.

Prefer vector shapes; avoid embedded rasters.

Limit blur, heavy masks, and expressions.

Set frame rate ~30fps (24–30 is fine) unless there’s a strong reason to go higher.

Test CPU impact on low-end mobile; animations should idle < 3% CPU in dev tools.

Optimize JSON:

bash
Copy code
# Strip metadata & whitespace (node)
npx jsonminify contrib/motion/lottie/loading.orb.json > contrib/motion/lottie/loading.orb.min.json
Tokens: durations & easings
Use the canonical timings from contrib/tokens/tokens.animations.json:

duration.micro, duration.short, duration.medium, duration.long

easing.standard, easing.emphasized, easing.decelerate

Map these when authoring AE/Bodymovin or Rive equivalents so motion feels consistent across apps.

Accessibility
Always provide a non-animated fallback (static frame, SVG icon).

Respect platform settings (prefers-reduced-motion on web; platform accessibility flags on mobile).

Avoid excessive flashing; follow WCAG 2.3.1 guidelines (no more than three flashes in any one second).

Adding a new animation
Design in After Effects or as pure SVG.

Export with Bodymovin (Lottie) using vector shapes; avoid “convert to raster”.

Name using the convention above.

Place under contrib/motion/lottie/ (or svg/).

Preview: drop a small GIF/MP4 in previews/ for PR diffs (optional).

Document timing & intended usage in MOTION_GUIDE.md (append to the Catalog section).

Run SVGO for any SVG animations using contrib/icons/svgo.config.json.

QA checklist (quick)
 File size within target; no rasters embedded.

 Looks crisp at 1× and 2×; no banding.

 Works on dark & light backgrounds (or documented constraints).

 Honors reduced motion settings.

 Loops seamlessly (for loaders).

 Token-aligned durations/easings.

Versioning
Bump Visuals section in contrib/CHANGELOG.md when animations change in look or timing.

Keep old assets when removal would break a release branch, then deprecate.

Licenses
All motion assets © Animica. Internal reuse across Animica products is permitted. Third-party assets, if any, must be listed in contrib/LICENSING.md.

