"use client";

import { useEffect, useRef, useState } from "react";

/**
 * ParticleHero — a full-width canvas animation.
 *
 * Particles in the Animica entangled duotone (wave-blue ↔ collapse-violet)
 * drift, connect into a faint constellation, and assemble the "ANIMICA"
 * wordmark by easing toward target points sampled from an offscreen
 * canvas. After assembling, they shimmer in place.
 *
 * - requestAnimationFrame loop, cancelled on unmount.
 * - devicePixelRatio-aware (capped at 2) for crisp rendering.
 * - Reacts subtly to pointer position (gentle repel).
 * - Respects prefers-reduced-motion: renders a static wordmark instead.
 */

const BRAND_COLORS = [
  "#6EA8FE", // wave (probability-blue)
  "#8FA9FF", // wave→collapse blend
  "#A78BFF", // collapse blend
  "#B98CFF", // collapse (measurement-violet)
  "#7CB8FF", // wave (lifted)
];

type Particle = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  tx: number; // target x (wordmark point)
  ty: number; // target y
  baseColor: string;
  size: number;
  phase: number; // for shimmer
};

const PARTICLE_COUNT = 210;
const CONNECT_DIST = 96; // px, in CSS space
const POINTER_RADIUS = 110;

function pickColor(i: number): string {
  // Mostly teal/blue, sprinkle of neon-green accents.
  const r = (i * 2654435761) % 100;
  if (r < 12) return BRAND_COLORS[4];
  return BRAND_COLORS[i % 4];
}

export default function ParticleHero({ version }: { version?: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);

  useEffect(() => {
    if (reduced) return; // static fallback rendered in JSX
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let width = 0;
    let height = 0;
    let dpr = 1;
    let particles: Particle[] = [];
    const pointer = { x: -9999, y: -9999, active: false };
    // Phase: 0 = drift toward wordmark, 1 = shimmer in place.
    let assembledAt = 0;
    const startTime = performance.now();

    /** Sample target points for the ANIMICA wordmark from an offscreen canvas. */
    function computeTargets(w: number, h: number): { x: number; y: number }[] {
      const off = document.createElement("canvas");
      const octx = off.getContext("2d");
      if (!octx) return [];
      off.width = Math.max(1, Math.floor(w));
      off.height = Math.max(1, Math.floor(h));
      octx.fillStyle = "#fff";
      octx.textAlign = "center";
      octx.textBaseline = "middle";
      // Scale the font to the canvas width so the wordmark fits.
      const fontSize = Math.min(w * 0.16, h * 0.55, 180);
      octx.font = `800 ${fontSize}px ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`;
      octx.fillText("ANIMICA", w / 2, h * 0.5);

      const img = octx.getImageData(0, 0, off.width, off.height).data;
      const pts: { x: number; y: number }[] = [];
      // Sample on a grid; keep pixels that are part of the glyphs.
      const step = Math.max(3, Math.round(fontSize / 26));
      for (let y = 0; y < off.height; y += step) {
        for (let x = 0; x < off.width; x += step) {
          const alpha = img[(y * off.width + x) * 4 + 3];
          if (alpha > 128) pts.push({ x, y });
        }
      }
      return pts;
    }

    function build() {
      const rect = canvas!.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas!.width = Math.floor(width * dpr);
      canvas!.height = Math.floor(height * dpr);
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);

      const targets = computeTargets(width, height);
      particles = [];
      for (let i = 0; i < PARTICLE_COUNT; i++) {
        const t = targets.length
          ? targets[Math.floor((i / PARTICLE_COUNT) * targets.length)]
          : { x: Math.random() * width, y: Math.random() * height };
        particles.push({
          x: Math.random() * width,
          y: Math.random() * height,
          vx: (Math.random() - 0.5) * 0.3,
          vy: (Math.random() - 0.5) * 0.3,
          tx: t.x,
          ty: t.y,
          baseColor: pickColor(i),
          size: 1.1 + Math.random() * 1.6,
          phase: Math.random() * Math.PI * 2,
        });
      }
      assembledAt = 0;
    }

    function frame(now: number) {
      const elapsed = now - startTime;
      // Ease toward targets for the first ~2.6s, then shimmer.
      const assembling = elapsed < 2600;
      if (!assembling && assembledAt === 0) assembledAt = now;

      ctx!.clearRect(0, 0, width, height);

      for (const p of particles) {
        if (assembling) {
          // Ease toward target point (critically-ish damped).
          p.x += (p.tx - p.x) * 0.045;
          p.y += (p.ty - p.y) * 0.045;
        } else {
          // Shimmer: gentle orbit around the target.
          p.phase += 0.02;
          const wob = 1.4;
          const desiredX = p.tx + Math.cos(p.phase) * wob;
          const desiredY = p.ty + Math.sin(p.phase * 1.3) * wob;
          p.x += (desiredX - p.x) * 0.08;
          p.y += (desiredY - p.y) * 0.08;
        }

        // Pointer repel (subtle parallax/repel).
        if (pointer.active) {
          const dx = p.x - pointer.x;
          const dy = p.y - pointer.y;
          const d2 = dx * dx + dy * dy;
          if (d2 < POINTER_RADIUS * POINTER_RADIUS && d2 > 0.01) {
            const d = Math.sqrt(d2);
            const force = (1 - d / POINTER_RADIUS) * 6;
            p.x += (dx / d) * force;
            p.y += (dy / d) * force;
          }
        }
      }

      // Constellation lines.
      for (let i = 0; i < particles.length; i++) {
        const a = particles[i];
        for (let j = i + 1; j < particles.length; j++) {
          const b = particles[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const d2 = dx * dx + dy * dy;
          if (d2 < CONNECT_DIST * CONNECT_DIST) {
            const alpha = (1 - Math.sqrt(d2) / CONNECT_DIST) * 0.18;
            ctx!.strokeStyle = `rgba(58,155,255,${alpha.toFixed(3)})`;
            ctx!.lineWidth = 0.6;
            ctx!.beginPath();
            ctx!.moveTo(a.x, a.y);
            ctx!.lineTo(b.x, b.y);
            ctx!.stroke();
          }
        }
      }

      // Particles.
      for (const p of particles) {
        const twinkle = 0.6 + 0.4 * Math.sin(p.phase + elapsed * 0.002);
        ctx!.globalAlpha = assembling ? 0.85 : twinkle;
        ctx!.fillStyle = p.baseColor;
        ctx!.shadowBlur = 8;
        ctx!.shadowColor = p.baseColor;
        ctx!.beginPath();
        ctx!.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx!.fill();
      }
      ctx!.globalAlpha = 1;
      ctx!.shadowBlur = 0;

      raf = requestAnimationFrame(frame);
    }

    function onPointerMove(e: PointerEvent) {
      const rect = canvas!.getBoundingClientRect();
      pointer.x = e.clientX - rect.left;
      pointer.y = e.clientY - rect.top;
      pointer.active = true;
    }
    function onPointerLeave() {
      pointer.active = false;
      pointer.x = -9999;
      pointer.y = -9999;
    }
    function onResize() {
      build();
    }

    build();
    raf = requestAnimationFrame(frame);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerleave", onPointerLeave);
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(raf);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerleave", onPointerLeave);
      window.removeEventListener("resize", onResize);
    };
  }, [reduced]);

  return (
    <section className="relative overflow-hidden rounded-3xl border border-white/10 bg-white/[0.03] shadow-glass backdrop-blur-md">
      {/* Accent glows behind the canvas. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -left-24 -top-24 h-72 w-72 rounded-full bg-neon-teal/20 blur-3xl"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-32 right-0 h-80 w-80 rounded-full bg-neon-blue/20 blur-3xl"
      />

      {/* The canvas (or static fallback for reduced motion). */}
      <div className="relative h-[300px] w-full md:h-[380px]">
        {reduced ? (
          <div className="flex h-full w-full items-center justify-center">
            <span className="select-none text-5xl font-extrabold tracking-tightest grad-text md:text-7xl">
              ANIMICA
            </span>
          </div>
        ) : (
          <canvas
            ref={canvasRef}
            aria-hidden
            className="h-full w-full"
            style={{ touchAction: "none" }}
          />
        )}
      </div>

      {/* Title overlay. */}
      <div className="relative z-10 px-6 pb-12 pt-2 md:px-12 md:pb-16">
        <div className="max-w-3xl space-y-4 animate-fade-up">
          <span className="badge">
            <span className="h-1.5 w-1.5 rounded-full bg-neon-green" />
            animica {version || "9.0.8"}
          </span>
          <h1 className="text-3xl font-semibold tracking-tightest text-white md:text-5xl">
            Download the <span className="grad-text">Animica GUI Miner</span>
          </h1>
          <p className="max-w-2xl text-base text-white/70 md:text-lg">
            One desktop app that runs the whole unified flow: proof-of-work
            mining, AI useful-work, and ENA model training — all paid in ANM.
            No terminal required.
          </p>
        </div>
      </div>
    </section>
  );
}
