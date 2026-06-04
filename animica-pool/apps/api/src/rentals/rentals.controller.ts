import { Body, Controller, Get, Param, Post, Query } from "@nestjs/common";
import { RentalsService } from "./rentals.service";
import { CreateListingDto, CreateOrderDto } from "./rentals.dto";
import { Public, Roles } from "../common/auth.guard";
import { CurrentUser } from "../common/current-user.decorator";
import type { AuthUser } from "../common/auth.guard";

@Controller("api/rentals")
export class RentalsController {
  constructor(private readonly rentals: RentalsService) {}

  @Public()
  @Get("listings")
  listings(@Query("providerType") providerType?: string) {
    return this.rentals.listListings(providerType);
  }

  @Roles("ADMIN")
  @Post("listings")
  createListing(@Body() dto: CreateListingDto) {
    return this.rentals.createListing(dto);
  }

  @Get("orders")
  orders(@CurrentUser() user: AuthUser) {
    return this.rentals.listOrders(user.id);
  }

  @Post("orders")
  createOrder(@CurrentUser() user: AuthUser, @Body() dto: CreateOrderDto) {
    return this.rentals.createOrder(user.id, dto);
  }

  @Get("orders/:id")
  order(@CurrentUser() user: AuthUser, @Param("id") id: string) {
    return this.rentals.getOrder(user.id, id, user.role === "ADMIN");
  }

  @Post("orders/:id/cancel")
  cancel(@CurrentUser() user: AuthUser, @Param("id") id: string) {
    return this.rentals.cancelOrder(user.id, id);
  }
}
