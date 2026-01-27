import { z } from "zod";

const hostPortSchema = (defaultPort: number) =>
  z.object({
    HOST: z.string().default("0.0.0.0"),
    PORT: z.coerce.number().int().min(1).max(65535).default(defaultPort)
  });

export const extendWithHostPort = <T extends z.ZodRawShape>(
  schema: z.ZodObject<T>,
  options: { defaultPort: number }
) => schema.extend(hostPortSchema(options.defaultPort).shape);

export const getHostPort = (
  env: NodeJS.ProcessEnv,
  options: { defaultPort: number }
) => {
  const result = hostPortSchema(options.defaultPort).safeParse(env);
  if (!result.success) {
    const formatted = result.error.flatten().fieldErrors;
    throw new Error(`Invalid host/port configuration: ${JSON.stringify(formatted)}`);
  }
  return result.data;
};
