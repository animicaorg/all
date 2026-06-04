import { Body, Controller, Get, Param, Post, Query } from "@nestjs/common";
import { PayoutsService } from "./payouts.service";
import { Roles } from "../common/auth.guard";
import { CurrentUser } from "../common/current-user.decorator";
import type { AuthUser } from "../common/auth.guard";

@Controller("api/payouts")
export class PayoutsController {
  constructor(private readonly payouts: PayoutsService) {}

  @Get("wallets")
  wallets(@CurrentUser() user: AuthUser) {
    return this.payouts.listWallets(user.id);
  }

  @Post("wallets")
  addWallet(@CurrentUser() user: AuthUser, @Body() dto: { asset: string; address: string; isDefault?: boolean }) {
    return this.payouts.addWallet(user.id, dto);
  }

  @Get("balance")
  async balance(@CurrentUser() user: AuthUser) {
    return { payableUsd: await this.payouts.payableBalanceUsd(user.id) };
  }

  @Get("requests")
  requests(@CurrentUser() user: AuthUser) {
    return this.payouts.listRequests(user.id);
  }

  @Post("requests")
  createRequest(@CurrentUser() user: AuthUser, @Body() dto: { type: string; asset: string; address: string; amountUsd: number }) {
    return this.payouts.createRequest(user.id, dto);
  }
}

@Roles("ADMIN")
@Controller("api/admin")
export class AdminPayoutsController {
  constructor(private readonly payouts: PayoutsService) {}

  @Get("payouts")
  list(@Query("status") status?: string) {
    return this.payouts.listForAdmin(status);
  }

  @Post("payouts/:id/approve")
  approve(@CurrentUser() admin: AuthUser, @Param("id") id: string) {
    return this.payouts.approve(admin.email, id);
  }

  @Post("payouts/:id/reject")
  reject(@CurrentUser() admin: AuthUser, @Param("id") id: string, @Body() body: { note?: string }) {
    return this.payouts.reject(admin.email, id, body?.note);
  }

  @Post("payout-batches")
  batch(@CurrentUser() admin: AuthUser, @Body() body: { requestIds: string[] }) {
    return this.payouts.createBatch(admin.email, body.requestIds ?? []);
  }

  @Post("payout-batches/:id/poll")
  poll(@Param("id") id: string) {
    return this.payouts.pollBatch(id);
  }
}
