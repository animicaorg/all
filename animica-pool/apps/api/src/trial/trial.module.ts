import { Module } from "@nestjs/common";
import { PrismaModule } from "../prisma/prisma.module";
import { ApiKeysModule } from "../api-keys/api-keys.module";
import { CreditsModule } from "../credits/credits.module";
import { TrialController } from "./trial.controller";
import { TrialService } from "./trial.service";

@Module({
  imports: [PrismaModule, ApiKeysModule, CreditsModule],
  controllers: [TrialController],
  providers: [TrialService],
})
export class TrialModule {}
