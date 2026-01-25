import pino from "pino";

export const createLogger = (service: string, level: string) => {
  return pino({
    name: service,
    level,
    base: { service }
  });
};
