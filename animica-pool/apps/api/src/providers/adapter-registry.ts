import type { ProviderAdapter } from "@animica/shared";
import { buildAdapters } from "@animica/provider-router";
import { providerConfig } from "../config/env";
import { AnimicaWorkerAdapter } from "./animica-worker.adapter";
import type { PrismaService } from "../prisma/prisma.service";

/** All routable providers: the HTTP adapters from config + the DB-backed
 *  internal Animica worker pool. */
export function buildAllAdapters(prisma: PrismaService): ProviderAdapter[] {
  return [...buildAdapters(providerConfig()), new AnimicaWorkerAdapter(prisma)];
}
