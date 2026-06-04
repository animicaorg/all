import { Injectable, BadRequestException, NotFoundException, HttpException, HttpStatus, ServiceUnavailableException } from "@nestjs/common";
import { Prisma, RentalProviderType, RentalStatus } from "@prisma/client";
import { PrismaService } from "../prisma/prisma.service";
import { CreditsService } from "../credits/credits.service";
import { auditLog } from "../common/audit";
import { env } from "../config/env";
import { createInvoice } from "../payments/nowpayments.client";
import type { CreateListingDto, CreateOrderDto } from "./rentals.dto";

@Injectable()
export class RentalsService {
  constructor(private readonly prisma: PrismaService, private readonly credits: CreditsService) {}

  // Only surface REAL rentals: a listing must be backed by a worker that is
  // actually online and heartbeating recently. (No FK relation on workerId, so
  // we resolve the backing workers in a second query.)
  async listListings(providerType?: string) {
    const listings = await this.prisma.rentalListing.findMany({
      where: {
        availabilityStatus: "available",
        workerId: { not: null },
        ...(providerType ? { providerType: providerType as RentalProviderType } : {}),
      },
      orderBy: { pricePerHourUsd: "asc" },
    });
    if (listings.length === 0) return [];
    const freshSince = new Date(Date.now() - RentalsService.WORKER_FRESH_MS);
    const live = await this.prisma.worker.findMany({
      where: { id: { in: listings.map((l) => l.workerId!) }, status: "online", lastSeenAt: { gte: freshSince } },
      select: { id: true },
    });
    const liveSet = new Set(live.map((w) => w.id));
    return listings.filter((l) => l.workerId && liveSet.has(l.workerId));
  }

  // A worker is considered live for marketplace listing if it heartbeat within
  // this window (agent heartbeats every 15–30s; reaper-independent).
  private static readonly WORKER_FRESH_MS = 5 * 60 * 1000;

  async createListing(dto: CreateListingDto) {
    const listing = await this.prisma.rentalListing.create({
      data: {
        providerType: dto.providerType as RentalProviderType,
        title: dto.title, description: dto.description, cpuModel: dto.cpuModel, gpuModel: dto.gpuModel,
        vramGb: dto.vramGb, ramGb: dto.ramGb, storageGb: dto.storageGb, region: dto.region,
        pricePerHourUsd: new Prisma.Decimal(dto.pricePerHourUsd),
        pricePerDayUsd: dto.pricePerDayUsd != null ? new Prisma.Decimal(dto.pricePerDayUsd) : null,
        workerId: dto.workerId, supportedModels: dto.supportedModels ?? [],
      },
    });
    await auditLog(this.prisma, { action: "rental.listing.create", entityType: "RentalListing", entityId: listing.id });
    return listing;
  }

  listOrders(userId: string) {
    return this.prisma.rentalOrder.findMany({ where: { userId }, orderBy: { createdAt: "desc" }, include: { listing: true } });
  }

  async getOrder(userId: string, id: string, isAdmin = false) {
    const order = await this.prisma.rentalOrder.findUnique({ where: { id }, include: { listing: true } });
    if (!order || (order.userId !== userId && !isAdmin)) throw new NotFoundException();
    return order;
  }

  async createOrder(userId: string, dto: CreateOrderDto) {
    const listing = await this.prisma.rentalListing.findUnique({ where: { id: dto.listingId } });
    if (!listing || listing.availabilityStatus !== "available") throw new NotFoundException("listing unavailable");
    const priceUsd = Number(listing.pricePerHourUsd) * dto.hours;

    const order = await this.prisma.rentalOrder.create({
      data: { listingId: listing.id, userId, hours: dto.hours, priceUsd: new Prisma.Decimal(priceUsd), status: "pending_payment" },
    });

    if (dto.payWith === "credits") {
      const ok = await this.credits.deduct(userId, priceUsd, "rental_usage", order.id);
      if (!ok) {
        await this.prisma.rentalOrder.update({ where: { id: order.id }, data: { status: "cancelled" } });
        throw new HttpException({ error: { message: "Insufficient credits", code: "insufficient_credits" } }, HttpStatus.PAYMENT_REQUIRED);
      }
      const active = await this.activatePaidOrder(order.id);
      return { order: active, paidWith: "credits" };
    }

    // crypto
    if (!env().NOWPAYMENTS_API_KEY) throw new ServiceUnavailableException("Crypto payments not configured");
    const invoice = await createInvoice({ priceAmountUsd: priceUsd, orderId: order.id, orderDescription: `Compute rental ${listing.title} (${dto.hours}h)` });
    return { order, paidWith: "crypto", invoiceUrl: invoice.invoice_url };
  }

  /** Mark a paid order active: assign endpoint + window + log revenue. Idempotent. */
  async activatePaidOrder(orderId: string) {
    const order = await this.prisma.rentalOrder.findUnique({ where: { id: orderId } });
    if (!order) throw new NotFoundException();
    if (["active", "completed"].includes(order.status)) return order;

    const e = env();
    const endsAt = new Date(Date.now() + order.hours * 3600 * 1000);
    const base = e.NEXT_PUBLIC_APP_URL.replace(/\/$/, "");

    // Provision: bind a listing's worker if set, else assign an available
    // online rental-capable worker and mark it busy for the rental window.
    const listing = await this.prisma.rentalListing.findUnique({ where: { id: order.listingId } });
    let assignedWorkerId: string | null = listing?.workerId ?? null;
    if (!assignedWorkerId) {
      const free = await this.prisma.worker.findFirst({
        where: { status: "online", isVerified: true, earningMode: { in: ["rental", "all"] } },
        orderBy: { benchmarkScore: "desc" },
      });
      if (free) {
        await this.prisma.worker.update({ where: { id: free.id }, data: { status: "busy" } });
        assignedWorkerId = free.id;
      }
    }
    const endpointUrl = `${base}/rentals/${order.id}/v1`;
    const updated = await this.prisma.rentalOrder.update({
      where: { id: orderId },
      data: { status: "active", startedAt: new Date(), endsAt, endpointUrl, assignedWorkerId },
    });

    // Revenue ledger entry (net = gross for MVP; provider/worker cost wired later).
    const gross = Number(order.priceUsd);
    await this.prisma.revenueLedger.create({
      data: {
        sourceType: "rental", sourceId: order.id,
        grossUsd: new Prisma.Decimal(gross), costUsd: new Prisma.Decimal(0), feeUsd: new Prisma.Decimal(0),
        netUsd: new Prisma.Decimal(gross),
        allocationJson: {
          providersUsd: +(gross * e.SPLIT_PROVIDERS_PERCENT / 100).toFixed(8),
          treasuryUsd: +(gross * e.SPLIT_TREASURY_PERCENT / 100).toFixed(8),
          referralUsd: +(gross * e.SPLIT_REFERRAL_PERCENT / 100).toFixed(8),
        },
      },
    }).catch(() => {});
    await auditLog(this.prisma, { action: "rental.order.active", entityType: "RentalOrder", entityId: order.id, metadata: { gross } });
    return updated;
  }

  async cancelOrder(userId: string, id: string) {
    const order = await this.getOrder(userId, id);
    if (!["pending_payment", "paid", "provisioning"].includes(order.status)) {
      throw new BadRequestException(`Cannot cancel a ${order.status} rental`);
    }
    return this.prisma.rentalOrder.update({ where: { id }, data: { status: "cancelled" as RentalStatus } });
  }
}
