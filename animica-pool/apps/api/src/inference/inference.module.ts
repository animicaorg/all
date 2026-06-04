import { Module } from "@nestjs/common";
import { InferenceController, AiUiController } from "./inference.controller";
import { InferenceService } from "./inference.service";
import { ApiKeysModule } from "../api-keys/api-keys.module";
import { CreditsModule } from "../credits/credits.module";
import { ProvidersModule } from "../providers/providers.module";
import { RevenueModule } from "../revenue/revenue.module";

@Module({
  imports: [ApiKeysModule, CreditsModule, ProvidersModule, RevenueModule],
  controllers: [InferenceController, AiUiController],
  providers: [InferenceService],
})
export class InferenceModule {}
