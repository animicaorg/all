import { Module } from "@nestjs/common";
import { PaymentsController } from "./payments.controller";
import { PaymentsService } from "./payments.service";
import { CreditsModule } from "../credits/credits.module";
import { RentalsModule } from "../rentals/rentals.module";
import { PayoutsModule } from "../payouts/payouts.module";
import { RevenueModule } from "../revenue/revenue.module";

@Module({
  imports: [CreditsModule, RentalsModule, PayoutsModule, RevenueModule],
  controllers: [PaymentsController],
  providers: [PaymentsService],
})
export class PaymentsModule {}
