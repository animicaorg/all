import { defineCollection, z } from "astro:content";

const tutorials = defineCollection({
  type: "content",
  schema: z.object({
    title: z.string(),
    summary: z.string(),
    track: z.enum(["wallet", "aicf", "contracts", "node"]),
    difficulty: z.enum(["intro", "intermediate", "advanced"]),
    order: z.number().int(),
    estimatedMinutes: z.number().int().positive(),
    rewardAnim: z.string(),
    steps: z.number().int().positive(),
    /**
     * Step IDs in the order they appear in the markdown body. The body
     * uses <Step id="..."> markers; this manifest is the authoritative
     * checklist the backend uses for completion + reward calculation.
     */
    stepIds: z.array(z.string()).nonempty(),
  }),
});

export const collections = { tutorials };
