import { defineConfig } from "astro/config";
import tailwind from "@astrojs/tailwind";
import mdx from "@astrojs/mdx";

// Academy frontend. Static-rendered, fetches live data from the academy
// API at runtime via the browser. Tutorial content is authored in the
// /src/content/tutorials/ collection and rendered by [slug].astro.
//
// Environment-overridable defaults:
//   ACADEMY_API_BASE_URL    — backend (rewards, progress, attestations)
//   NODE_RPC_URL            — fallback chain RPC for read-only checks
//   POOL_REWARD_ADDRESS     — display target for the public reward-pool stat
export default defineConfig({
  site: "https://academy.animica.org",
  output: "static",
  integrations: [
    tailwind({ applyBaseStyles: true }),
    mdx(),
  ],
  vite: {
    define: {
      __ACADEMY_API__: JSON.stringify(
        process.env.ACADEMY_API_BASE_URL ||
          "https://api.academy.animica.org",
      ),
      __NODE_RPC__: JSON.stringify(
        process.env.NODE_RPC_URL || "https://rpc.animica.org",
      ),
      __REWARD_POOL_ADDR__: JSON.stringify(
        process.env.POOL_REWARD_ADDRESS ||
          "anim1academy0reward0pool0000000000000000",
      ),
    },
  },
});
