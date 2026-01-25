import { z } from "zod";
import { baseEnvSchema, loadEnv as loadBaseEnv } from "@cex/common";

const matchingEngineEnvSchema = baseEnvSchema.extend({
  SERVICE_NAME: z.string().default("matching-engine"),
  PRICE_DECIMALS: z.coerce.number().default(8),
  SIZE_DECIMALS: z.coerce.number().default(8)
});

export type MatchingEngineEnv = z.infer<typeof matchingEngineEnvSchema>;

export const loadEnv = (): MatchingEngineEnv => {
  return loadBaseEnv(matchingEngineEnvSchema);
};
