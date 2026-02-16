import jwt from "jsonwebtoken";
import { env } from "@/src/server/env";

export type SessionPayload = {
  userId: string;
  email: string;
};

export function signToken(payload: SessionPayload, expiresIn: string | number = "7d") {
  return jwt.sign(payload, env.JWT_SECRET, { expiresIn });
}

export function verifyToken(token: string): SessionPayload | null {
  try {
    return jwt.verify(token, env.JWT_SECRET) as SessionPayload;
  } catch {
    return null;
  }
}
