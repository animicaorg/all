import { Body, Controller, Get, Patch, Query } from "@nestjs/common";
import { RevenueService } from "./revenue.service";
import { ReferralService } from "./referral.service";
import { Public, Roles } from "../common/auth.guard";
import { CurrentUser } from "../common/current-user.decorator";
import type { AuthUser } from "../common/auth.guard";

@Controller()
export class RevenueController {
  constructor(private readonly revenue: RevenueService, private readonly referrals: ReferralService) {}

  @Public()
  @Get("api/revenue/public")
  publicSummary() {
    return this.revenue.publicSummary();
  }

  @Get("api/referrals")
  myReferrals(@CurrentUser() user: AuthUser) {
    return this.referrals.dashboard(user.id);
  }

  @Roles("ADMIN")
  @Get("api/admin/revenue")
  async adminRevenue(@Query("limit") limit?: string) {
    return { summary: await this.revenue.summary(), ledger: await this.revenue.ledger(limit ? Number(limit) : 100) };
  }

  @Roles("ADMIN")
  @Patch("api/admin/revenue/split")
  setSplit(@CurrentUser() admin: AuthUser, @Body() body: { providers: number; treasury: number; referral: number }) {
    return this.revenue.setSplit(admin.email, body);
  }
}
