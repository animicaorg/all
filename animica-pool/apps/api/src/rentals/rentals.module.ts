import { Module } from "@nestjs/common";
import { RentalsController } from "./rentals.controller";
import { RentalsService } from "./rentals.service";
import { CreditsModule } from "../credits/credits.module";

@Module({
  imports: [CreditsModule],
  controllers: [RentalsController],
  providers: [RentalsService],
  exports: [RentalsService],
})
export class RentalsModule {}
