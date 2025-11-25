// Animica Tokens — TypeScript bundle (generated from JSON sources)
// Light base + Dark overrides + Animation tokens.
// SPDX-License-Identifier: MIT

export type StepScale =
  | "0" | "50" | "100" | "200" | "300" | "400" | "500" | "600" | "700" | "800" | "900";

export interface ColorScale {
  [step: string]: string;
}
export interface Colors {
  primary: ColorScale;
  neutral: ColorScale;
  surface: ColorScale;
  success: ColorScale;
  warning: ColorScale;
  error: ColorScale;
}

export interface Typography {
  fontFamily: { base: string; code: string };
  scale: { xs: number; sm: number; base: number; md: number; lg: number; xl: number; ["2xl"]: number; ["3xl"]: number; ["4xl"]: number };
  lineHeight: { tight: number; normal: number; loose: number };
  tracking: { tight: number; normal: number; loose: number };
  weight: { regular: number; medium: number; semibold: number; bold: number };
}

export interface Space { [k: string]: number }
export interface Radius { sm: number; md: number; lg: number; xl: number; ["2xl"]: number; pill: number }
export interface Shadow { sm: string; md: string; lg: string }

export interface AnimationTokens {
  duration: Record<"instant"|"xxs"|"xs"|"sm"|"md"|"lg"|"xl"|"xxl", number>;
  delay: Record<"none"|"short"|"medium"|"long", number>;
  easing: Record<"linear"|"standard"|"emphasized"|"decelerate"|"accelerate"|"spring-soft"|"spring-bouncy", string>;
  presets: Record<string, {
    duration: number | string;
    easing: string;
    properties: string[];
    iterationCount?: string | number;
  }>;
  reduceMotion: { enabled: boolean; durationMultiplier: number; disablePresets: string[] };
}

export interface ThemeTokens {
  version: string;
  color: Colors;
  typography: Typography;
  space: Space;
  radius: Radius;
  shadow: Shadow;
  animation: AnimationTokens;
}

/* ---------------- Light (base) ---------------- */
export const light: ThemeTokens = {
  version: "1.0.0",
  color: {
    primary: { "50":"#EEF3FF","100":"#DCE7FF","200":"#C3D6FF","300":"#A7C1FF","400":"#7FA2FF","500":"#4B7DFF","600":"#2E63FF","700":"#254FCC","800":"#1C3D99","900":"#132966" },
    neutral: { "50":"#F6F8FF","100":"#ECEFFC","200":"#E1E6F5","300":"#CDD4E6","400":"#B4BED3","500":"#98A4BD","600":"#7B88A3","700":"#5E6B88","800":"#39425A","900":"#0E1222" },
    surface: { "0":"#FFFFFF","50":"#F8FAFF","100":"#F2F5FF","800":"#121623","900":"#0A0C14" },
    success: { "50":"#EAF6F1","100":"#CDEBDD","200":"#A0DCC4","300":"#72CCA9","400":"#45BD90","500":"#2CAF7E","600":"#22A06B","700":"#1B7C54","800":"#145D40","900":"#0E402C" },
    warning: { "50":"#FFF7E6","100":"#FFE8B3","200":"#FFD780","300":"#FEC54D","400":"#F2B32A","500":"#E8A41F","600":"#DFA71B","700":"#B77E12","800":"#8E5F0C","900":"#693F07" },
    error:   { "50":"#FDEBEB","100":"#F9CFCF","200":"#F2A8A8","300":"#EB8282","400":"#E86767","500":"#E65B5B","600":"#E45757","700":"#B74141","800":"#8D3131","900":"#622121" }
  },
  typography: {
    fontFamily: {
      base: 'Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif',
      code: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace'
    },
    scale: { xs:12, sm:14, base:16, md:18, lg:20, xl:24, "2xl":32, "3xl":40, "4xl":48 },
    lineHeight: { tight:1.15, normal:1.5, loose:1.7 },
    tracking: { tight:-0.01, normal:0, loose:0.01 },
    weight: { regular:400, medium:500, semibold:600, bold:700 }
  },
  space: { "1":4,"2":8,"3":12,"4":16,"5":20,"6":24,"8":32,"10":40,"12":48,"16":64,"20":80,"24":96 },
  radius: { sm:6, md:10, lg:14, xl:18, "2xl":24, pill:9999 },
  shadow: { sm:"0 1px 2px rgba(0,0,0,0.06)", md:"0 2px 8px rgba(0,0,0,0.08)", lg:"0 8px 24px rgba(0,0,0,0.10)" },
  animation: {
    duration: { instant:0, xxs:80, xs:120, sm:160, md:220, lg:300, xl:450, xxl:700 },
    delay: { none:0, short:60, medium:120, long:240 },
    easing: {
      linear:"cubic-bezier(0.000, 0.000, 1.000, 1.000)",
      standard:"cubic-bezier(0.200, 0.000, 0.000, 1.000)",
      emphasized:"cubic-bezier(0.200, 0.000, 0.000, 1.000)",
      decelerate:"cubic-bezier(0.000, 0.000, 0.000, 1.000)",
      accelerate:"cubic-bezier(0.300, 0.000, 1.000, 1.000)",
      "spring-soft":"cubic-bezier(0.16, 1, 0.3, 1)",
      "spring-bouncy":"cubic-bezier(0.34, 1.56, 0.64, 1)"
    },
    presets: {
      "fade-in":   { duration:160, easing:"cubic-bezier(0.000, 0.000, 0.000, 1.000)", properties:["opacity"] },
      "fade-out":  { duration:160, easing:"cubic-bezier(0.300, 0.000, 1.000, 1.000)", properties:["opacity"] },
      "slide-up-in":   { duration:220, easing:"cubic-bezier(0.200, 0.000, 0.000, 1.000)", properties:["transform","opacity"] },
      "slide-down-in": { duration:220, easing:"cubic-bezier(0.200, 0.000, 0.000, 1.000)", properties:["transform","opacity"] },
      "scale-in":  { duration:220, easing:"cubic-bezier(0.16, 1, 0.3, 1)", properties:["transform","opacity"] },
      "scale-out": { duration:220, easing:"cubic-bezier(0.300, 0.000, 1.000, 1.000)", properties:["transform","opacity"] },
      "tooltip":   { duration:120, easing:"cubic-bezier(0.000, 0.000, 0.000, 1.000)", properties:["transform","opacity"] },
      "drawer":    { duration:300, easing:"cubic-bezier(0.200, 0.000, 0.000, 1.000)", properties:["transform","opacity"] },
      "modal":     { duration:300, easing:"cubic-bezier(0.200, 0.000, 0.000, 1.000)", properties:["transform","opacity","backdrop-filter"] },
      "spinner":   { duration:1000, easing:"cubic-bezier(0.000, 0.000, 1.000, 1.000)", iterationCount:"infinite", properties:["transform"] }
    },
    reduceMotion: { enabled:true, durationMultiplier:0.0, disablePresets:["spinner"] }
  }
};

/* ---------------- Dark (overrides merged onto light) ---------------- */
export const darkOverrides: Partial<ThemeTokens> = {
  color: {
    surface: { "0":"#0D0F18","50":"#0F1220","100":"#121623","800":"#0B0E17","900":"#070A11" },
    neutral: { "50":"#E8ECF8","100":"#D5DBEF","200":"#C2CBE3","300":"#A7B2CF","400":"#8E9BBD","500":"#7C8AAE","600":"#AEB8CF","700":"#C7CFE0","800":"#E1E6F5","900":"#F6F8FF" },
    primary: { "400":"#8AA9FF","500":"#5E86FF","600":"#4B7DFF","700":"#2E63FF","800":"#254FCC" },
    success: { "400":"#50C79B","500":"#3AB889","600":"#2CAF7E" },
    warning: { "400":"#F4BE45","500":"#EFB435","600":"#E3A822" },
    error:   { "400":"#F07474","500":"#EA6060","600":"#E45757" }
  },
  shadow: { sm:"0 1px 1px rgba(0,0,0,0.35)", md:"0 2px 6px rgba(0,0,0,0.40)", lg:"0 8px 18px rgba(0,0,0,0.44)" }
};

/* ------------- Utilities ------------- */

/** Deep merge of tokens (simple object merge for our shape). */
export function mergeTokens<A extends object, B extends object>(base: A, overrides: B): A & B {
  const out: any = Array.isArray(base) ? [...(base as any)] : { ...(base as any) };
  for (const [k, v] of Object.entries(overrides as any)) {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      (out as any)[k] = mergeTokens((out as any)[k] ?? {}, v as any);
    } else {
      (out as any)[k] = v;
    }
  }
  return out;
}

/** Merged dark theme */
export const dark: ThemeTokens = mergeTokens(light, darkOverrides);

/**
 * Inject CSS variables into the current document (light or dark).
 * Call with `dark` to set dark tokens; pass a prefix to avoid collisions.
 */
export function applyCssVariables(tokens: ThemeTokens, opts: { root?: HTMLElement; prefix?: string } = {}): void {
  const root = opts.root ?? (document?.documentElement as HTMLElement);
  const p = (opts.prefix ?? "anm").replace(/-+$/,"");

  // Colors
  for (const [group, scale] of Object.entries(tokens.color)) {
    for (const [step, hex] of Object.entries(scale)) {
      root.style.setProperty(`--${p}-color-${group}-${step}`, String(hex));
    }
  }
  // Typography
  root.style.setProperty(`--${p}-typography-font-base`, tokens.typography.fontFamily.base);
  root.style.setProperty(`--${p}-typography-font-code`, tokens.typography.fontFamily.code);
  for (const [k, v] of Object.entries(tokens.typography.scale)) root.style.setProperty(`--${p}-typography-size-${k}`, v + "px");
  for (const [k, v] of Object.entries(tokens.typography.lineHeight)) root.style.setProperty(`--${p}-typography-line-${k}`, String(v));
  for (const [k, v] of Object.entries(tokens.typography.tracking)) root.style.setProperty(`--${p}-typography-track-${k}`, String(v) + "em");
  for (const [k, v] of Object.entries(tokens.typography.weight)) root.style.setProperty(`--${p}-typography-weight-${k}`, String(v));

  // Space
  for (const [k, v] of Object.entries(tokens.space)) root.style.setProperty(`--${p}-space-${k}`, v + "px");

  // Radius
  root.style.setProperty(`--${p}-radius-sm`, tokens.radius.sm + "px");
  root.style.setProperty(`--${p}-radius-md`, tokens.radius.md + "px");
  root.style.setProperty(`--${p}-radius-lg`, tokens.radius.lg + "px");
  root.style.setProperty(`--${p}-radius-xl`, tokens.radius.xl + "px");
  root.style.setProperty(`--${p}-radius-2xl`, tokens.radius["2xl"] + "px");
  root.style.setProperty(`--${p}-radius-pill`, tokens.radius.pill + "px");

  // Shadows
  root.style.setProperty(`--${p}-shadow-sm`, tokens.shadow.sm);
  root.style.setProperty(`--${p}-shadow-md`, tokens.shadow.md);
  root.style.setProperty(`--${p}-shadow-lg`, tokens.shadow.lg);
}

/** Helper to pick tokens based on an explicit mode or system preference. */
export type Mode = "light" | "dark" | "auto";
export function getTokens(mode: Mode = "auto"): ThemeTokens {
  if (mode === "light") return light;
  if (mode === "dark") return dark;
  if (typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return dark;
  }
  return light;
}

export default { light, dark, darkOverrides, mergeTokens, applyCssVariables, getTokens };
