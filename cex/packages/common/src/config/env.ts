import { z } from "zod";

export const baseEnvSchema = z.object({
  SERVICE_NAME: z.string().min(1),
  PORT: z.coerce.number().default(3000),
  LOG_LEVEL: z.string().default("info"),
  NATS_URL: z.string().url(),
  REDIS_URL: z.string().url(),
  DB_HOST: z.string().min(1),
  DB_PORT: z.coerce.number().default(5432),
  DB_USER: z.string().min(1),
  DB_PASSWORD: z.string().min(1),
  DB_NAME: z.string().min(1)
});

export type BaseEnv = z.infer<typeof baseEnvSchema>;

export const loadEnv = <T extends z.ZodTypeAny>(schema: T) => {
  const result = schema.safeParse(process.env);
  if (!result.success) {
    const formatted = result.error.flatten().fieldErrors;
    throw new Error(`Invalid environment configuration: ${JSON.stringify(formatted)}`);
  }
  return result.data as z.infer<T>;
};
