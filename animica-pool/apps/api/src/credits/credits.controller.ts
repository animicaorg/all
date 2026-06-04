import { Body, Controller, Get, Param, Post } from "@nestjs/common";
import { CreditsService } from "./credits.service";
import { CurrentUser } from "../common/current-user.decorator";
import { Roles } from "../common/auth.guard";
import type { AuthUser } from "../common/auth.guard";

@Controller("api/credits")
export class CreditsController {
  constructor(private readonly credits: CreditsService) {}

  @Get("balance")
  async balance(@CurrentUser() user: AuthUser) {
    return { balanceUsd: await this.credits.balance(user.id) };
  }

  @Get("ledger")
  ledger(@CurrentUser() user: AuthUser) {
    return this.credits.ledger(user.id);
  }
}

@Roles("ADMIN")
@Controller("api/admin/credits")
export class AdminCreditsController {
  constructor(private readonly credits: CreditsService) {}

  // Grant/deduct credits to a user (dev/demo + support). + grants, - deducts.
  @Post(":userId/adjust")
  adjust(
    @CurrentUser() admin: AuthUser,
    @Param("userId") userId: string,
    @Body() body: { amountUsd: number; note?: string },
  ) {
    return this.credits.adminAdjust(admin.email, userId, Number(body.amountUsd), body.note);
  }
}
