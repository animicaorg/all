import { Module } from "@nestjs/common";
import { RevenueController } from "./revenue.controller";
import { RevenueService } from "./revenue.service";
import { ReferralService } from "./referral.service";

@Module({
  controllers: [RevenueController],
  providers: [RevenueService, ReferralService],
  exports: [RevenueService, ReferralService],
})
export class RevenueModule {}
