import { Module } from "@nestjs/common";
import { CreditsService } from "./credits.service";
import { CreditsController, AdminCreditsController } from "./credits.controller";

@Module({
  controllers: [CreditsController, AdminCreditsController],
  providers: [CreditsService],
  exports: [CreditsService],
})
export class CreditsModule {}
