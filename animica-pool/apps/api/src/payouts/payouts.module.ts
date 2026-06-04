import { Module } from "@nestjs/common";
import { PayoutsController, AdminPayoutsController } from "./payouts.controller";
import { PayoutsService } from "./payouts.service";

@Module({
  controllers: [PayoutsController, AdminPayoutsController],
  providers: [PayoutsService],
  exports: [PayoutsService],
})
export class PayoutsModule {}
