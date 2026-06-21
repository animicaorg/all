import { Module } from "@nestjs/common";
import { APP_GUARD } from "@nestjs/core";
import { PrismaModule } from "./prisma/prisma.module";
import { AuthModule } from "./auth/auth.module";
import { ApiKeysModule } from "./api-keys/api-keys.module";
import { AdminModule } from "./admin/admin.module";
import { CreditsModule } from "./credits/credits.module";
import { InferenceModule } from "./inference/inference.module";
import { PaymentsModule } from "./payments/payments.module";
import { MiningModule } from "./mining/mining.module";
import { WorkersModule } from "./workers/workers.module";
import { RentalsModule } from "./rentals/rentals.module";
import { ProvidersModule } from "./providers/providers.module";
import { PayoutsModule } from "./payouts/payouts.module";
import { RevenueModule } from "./revenue/revenue.module";
import { BittensorModule } from "./bittensor/bittensor.module";
import { TrialModule } from "./trial/trial.module";
import { HealthController } from "./health.controller";
import { AuthGuard } from "./common/auth.guard";

@Module({
  imports: [PrismaModule, AuthModule, ApiKeysModule, AdminModule, CreditsModule, InferenceModule, PaymentsModule, MiningModule, WorkersModule, RentalsModule, ProvidersModule, PayoutsModule, RevenueModule, BittensorModule, TrialModule],
  controllers: [HealthController],
  providers: [
    // Global auth guard: every route requires a session unless @Public().
    { provide: APP_GUARD, useClass: AuthGuard },
  ],
})
export class AppModule {}
