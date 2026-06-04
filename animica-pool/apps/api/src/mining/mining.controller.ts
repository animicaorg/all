import { Body, Controller, Get, Patch, Post } from "@nestjs/common";
import { MiningService } from "./mining.service";
import { UpsertMiningAccountDto, SetModeDto, SetPayoutDto } from "./mining.dto";
import { Public } from "../common/auth.guard";
import { CurrentUser } from "../common/current-user.decorator";
import type { AuthUser } from "../common/auth.guard";

@Controller("api/mining")
export class MiningController {
  constructor(private readonly mining: MiningService) {}

  @Get("account")
  account(@CurrentUser() user: AuthUser) {
    return this.mining.getAccount(user.id);
  }

  @Post("account")
  upsert(@CurrentUser() user: AuthUser, @Body() dto: UpsertMiningAccountDto) {
    return this.mining.upsert(user.id, dto);
  }

  @Patch("account/mode")
  setMode(@CurrentUser() user: AuthUser, @Body() dto: SetModeDto) {
    return this.mining.setMode(user.id, dto);
  }

  @Patch("account/payout")
  setPayout(@CurrentUser() user: AuthUser, @Body() dto: SetPayoutDto) {
    return this.mining.setPayout(user.id, dto);
  }

  @Get("stats")
  stats(@CurrentUser() user: AuthUser) {
    return this.mining.stats(user.id);
  }

  @Get("payouts")
  payouts(@CurrentUser() user: AuthUser) {
    return this.mining.payouts(user.id);
  }

  @Public()
  @Get("pool-summary")
  poolSummary() {
    return this.mining.poolSummary();
  }
}
