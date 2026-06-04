import { Controller, Get } from "@nestjs/common";
import { PrismaService } from "../prisma/prisma.service";
import { Roles } from "../common/auth.guard";

@Roles("ADMIN")
@Controller("api/admin")
export class AdminController {
  constructor(private readonly prisma: PrismaService) {}

  // System overview shell — counts now; revenue/payout aggregates land in later phases.
  @Get("overview")
  async overview() {
    const since = new Date(Date.now() - 24 * 60 * 60 * 1000);
    const [users, miners, workers, activeRentals, inferenceToday, pendingPayouts] = await Promise.all([
      this.prisma.user.count(),
      this.prisma.miningAccount.count(),
      this.prisma.worker.count({ where: { status: "online" } }),
      this.prisma.rentalOrder.count({ where: { status: "active" } }),
      this.prisma.inferenceRequest.count({ where: { createdAt: { gte: since } } }),
      this.prisma.payoutRequest.count({ where: { status: { in: ["pending", "pending_review"] } } }),
    ]);
    return {
      totalUsers: users,
      miningAccounts: miners,
      activeWorkers: workers,
      activeRentals,
      inferenceRequestsToday: inferenceToday,
      pendingPayouts,
    };
  }

  @Get("users")
  async users() {
    return this.prisma.user.findMany({
      select: { id: true, email: true, role: true, isActive: true, createdAt: true },
      orderBy: { createdAt: "desc" },
      take: 200,
    });
  }
}
