import pino from "pino";

export const logger = pino({
  level: process.env.LOG_LEVEL ?? "info",
  base: { service: "chat-animica" }
});

export function withRequestId(requestId: string) {
  return logger.child({ requestId });
}
