import { Body, Controller, Get, Headers, Param, Post } from "@nestjs/common";
import { PaymentsService } from "./payments.service";
import { Public } from "../common/auth.guard";
import { CurrentUser } from "../common/current-user.decorator";
import type { AuthUser } from "../common/auth.guard";

@Controller()
export class PaymentsController {
  constructor(private readonly payments: PaymentsService) {}

  @Public()
  @Get("api/credits/packages")
  packages() {
    return this.payments.packages();
  }

  @Post("api/credits/checkout")
  checkout(@CurrentUser() user: AuthUser, @Body() body: { packageId?: string; amountUsd?: number }) {
    return this.payments.checkout(user.id, body);
  }

  @Public()
  @Post("api/nowpayments/webhook")
  webhook(@Body() body: Record<string, unknown>, @Headers("x-nowpayments-sig") sig: string) {
    return this.payments.handleWebhook(body, sig ?? null);
  }

  @Get("api/nowpayments/payment/:id")
  payment(@Param("id") id: string) {
    return this.payments.getPaymentStatus(id);
  }
}
