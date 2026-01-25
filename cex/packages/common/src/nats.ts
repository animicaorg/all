import { connect, JSONCodec, type NatsConnection } from "nats";
import type { BaseEnv } from "./config/env.js";

export const jsonCodec = JSONCodec();

export const connectNats = async (env: BaseEnv): Promise<NatsConnection> => {
  return connect({
    servers: env.NATS_URL
  });
};
