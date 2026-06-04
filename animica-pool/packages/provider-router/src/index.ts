import type { ProviderAdapter } from "@animica/shared";
import type { ProviderRouterConfig } from "./config.js";
import { MockProvider, BittensorProvider, OpenRouterProvider, TargonProvider, RunPodProvider, AkashProvider, HttpOpenAiProvider } from "./adapters.js";

export * from "./config.js";
export * from "./router.js";
export { HttpOpenAiProvider, OpenRouterProvider, TargonProvider } from "./adapters.js";
export { estimateTokens, requestInputText } from "./util.js";

/** Build the HTTP provider adapter set from config. Disabled adapters are still
 *  returned (the router filters by isEnabled()) so health pages can list them.
 *  The DB-backed `animica_worker` adapter is added by the API layer. */
export function buildAdapters(cfg: ProviderRouterConfig): ProviderAdapter[] {
  return [
    new BittensorProvider(cfg.bittensor),
    new OpenRouterProvider(cfg.openrouter),
    new TargonProvider(cfg.targon),
    new RunPodProvider(cfg.runpod),
    new AkashProvider(cfg.akash),
    new HttpOpenAiProvider("lambda", cfg.lambda),
    new HttpOpenAiProvider("spheron", cfg.spheron),
    new HttpOpenAiProvider("hyperbolic", cfg.hyperbolic),
    new HttpOpenAiProvider("render", cfg.render),
    new HttpOpenAiProvider("ionet", cfg.ionet),
    new MockProvider(cfg.mock),
  ];
}
