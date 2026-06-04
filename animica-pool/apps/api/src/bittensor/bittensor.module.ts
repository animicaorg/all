import { Module } from "@nestjs/common";
import { BittensorService } from "./bittensor.service";
import { BittensorController, BittensorMachineController, BittensorAdminController } from "./bittensor.controller";
import { RevenueModule } from "../revenue/revenue.module";

@Module({
  imports: [RevenueModule],
  controllers: [BittensorController, BittensorMachineController, BittensorAdminController],
  providers: [BittensorService],
  exports: [BittensorService],
})
export class BittensorModule {}
