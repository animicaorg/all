import type { ReactNode } from "react";

/**
 * Premium hero block: optional eyebrow badge, large title (with gradient
 * emphasis via <span className="grad-text"> in the title), subtitle, and a
 * row of CTAs passed as children.
 */
export function Hero({
  title,
  subtitle,
  eyebrow,
  children,
  className = "",
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  eyebrow?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`relative overflow-hidden rounded-3xl border border-white/10 bg-white/[0.03] px-6 py-14 shadow-glass backdrop-blur-md md:px-12 md:py-20 ${className}`}
    >
      {/* Accent glow */}
      <div
        aria-hidden
        className="pointer-events-none absolute -left-24 -top-24 h-72 w-72 rounded-full bg-neon-green/20 blur-3xl"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-32 right-0 h-80 w-80 rounded-full bg-neon-blue/20 blur-3xl"
      />
      <div className="relative max-w-3xl space-y-6 animate-fade-up">
        {eyebrow && (
          <div className="inline-flex">
            <span className="badge">{eyebrow}</span>
          </div>
        )}
        <h1 className="text-4xl font-semibold tracking-tightest text-white md:text-6xl">
          {title}
        </h1>
        {subtitle && (
          <p className="max-w-2xl text-lg text-white/70 md:text-xl">{subtitle}</p>
        )}
        {children && <div className="flex flex-wrap gap-3 pt-2">{children}</div>}
      </div>
    </section>
  );
}

export default Hero;
