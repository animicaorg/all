import { HttpException, HttpStatus, Injectable } from "@nestjs/common";
import { createHash, randomBytes } from "node:crypto";
import { PrismaService } from "../prisma/prisma.service";
import { ApiKeysService } from "../api-keys/api-keys.service";
import { CreditsService } from "../credits/credits.service";

// Free trial API keys — zero-config onboarding for MCP servers / agents / bots.
// Removes the signup wall on the first call (the biggest adoption lever).
//
// Abuse controls (a free-credit endpoint is a magnet, so these are load-bearing):
//   • One trial per network — the trial user's email is a salted hash of the
//     client IP, so the unique-email constraint enforces it with NO schema change.
//   • A global daily cap bounds total free-credit exposure.
//   • A small grant (TRIAL_CREDIT_USD, default $0.50).
//   • Trial users are namespaced (@trial.animica.local) for analytics/cleanup.
const TRIAL_DOMAIN = "trial.animica.local";
const SALT = process.env.TRIAL_IP_SALT || "animica-trial-v1";
const DAILY_CAP = parseInt(process.env.TRIAL_DAILY_CAP || "500", 10);
const TRIAL_USD = parseFloat(process.env.TRIAL_CREDIT_USD || "0.50");

@Injectable()
export class TrialService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly apiKeys: ApiKeysService,
    private readonly credits: CreditsService,
  ) {}

  async issueTrial(ip: string) {
    const ipHash = createHash("sha256").update(`${ip}|${SALT}`).digest("hex").slice(0, 32);
    const email = `trial.${ipHash}@${TRIAL_DOMAIN}`;

    // Global daily cap.
    const since = new Date(Date.now() - 24 * 60 * 60 * 1000);
    const todays = await this.prisma.user.count({
      where: { email: { endsWith: `@${TRIAL_DOMAIN}` }, createdAt: { gte: since } },
    });
    if (todays >= DAILY_CAP) {
      throw new HttpException(
        { error: { message: "Free trials are at capacity for today — grab a key at https://pool.animica.org", type: "trial_capacity" } },
        HttpStatus.SERVICE_UNAVAILABLE,
      );
    }

    // One trial per network.
    const existing = await this.prisma.user.findUnique({ where: { email }, include: { apiKeys: true } });
    if (existing && existing.apiKeys.length > 0) {
      throw new HttpException(
        { error: { message: "A free trial was already claimed for this network. Sign up at https://pool.animica.org", type: "trial_already_claimed" } },
        HttpStatus.TOO_MANY_REQUESTS,
      );
    }

    const user =
      existing ??
      (await this.prisma.user.create({
        data: {
          email,
          passwordHash: createHash("sha256").update(randomBytes(32)).digest("hex"), // unusable; trial users don't log in
          displayName: "Trial (MCP)",
          referralCode: `anmt_${randomBytes(6).toString("hex")}`,
        },
      }));

    const minted = await this.apiKeys.create(user.id, "mcp-trial");
    await this.credits.record(user.id, "admin_adjustment", TRIAL_USD, { note: "free trial grant (MCP)" });

    return {
      api_key: minted.key, // shown once
      prefix: minted.prefix,
      credits_usd: TRIAL_USD,
      base_url: `${process.env.PUBLIC_BASE_URL || "https://pool.animica.org"}/v1`,
      message: "Free trial key — keep it secret. For more credits, sign up at https://pool.animica.org",
    };
  }
}
