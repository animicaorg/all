import { defineConfig } from "astro/config";
import tailwind from "@astrojs/tailwind";

export default defineConfig({
  site: "https://pool.animica.org",
  output: "static",
  integrations: [tailwind({ applyBaseStyles: true })],
  vite: {
    define: {
      __POOL_URL__: JSON.stringify(
        process.env.POOL_URL || "stratum+tcp://pool.animica.org:3333",
      ),
      __MINING_API__: JSON.stringify(
        process.env.MINING_API_BASE_URL ||
          "https://pool.animica.org",
      ),
      __AICF_API__: JSON.stringify(
        process.env.AICF_API_BASE_URL ||
          "https://api.aicf.animica.org",
      ),
    },
  },
});
