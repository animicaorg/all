import type { ReactNode } from "react";

/**
 * Page section wrapper with consistent vertical rhythm and an optional
 * heading/eyebrow/description block. Children render below the header.
 */
export function Section({
  children,
  title,
  eyebrow,
  description,
  className = "",
  id,
}: {
  children?: ReactNode;
  title?: ReactNode;
  eyebrow?: ReactNode;
  description?: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section id={id} className={`space-y-6 ${className}`}>
      {(eyebrow || title || description) && (
        <div className="space-y-3">
          {eyebrow && (
            <p className="text-xs font-semibold uppercase tracking-wider text-neon-green/80">
              {eyebrow}
            </p>
          )}
          {title && (
            <h2 className="text-2xl font-semibold tracking-tight text-white md:text-3xl">
              {title}
            </h2>
          )}
          {description && (
            <p className="max-w-2xl text-white/60">{description}</p>
          )}
        </div>
      )}
      {children}
    </section>
  );
}

export default Section;
