import { Module } from "@nestjs/common";
import { MiningController } from "./mining.controller";
import { MiningService } from "./mining.service";

@Module({
  controllers: [MiningController],
  providers: [MiningService],
})
export class MiningModule {}
