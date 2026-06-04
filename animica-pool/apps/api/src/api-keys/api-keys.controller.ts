import { Body, Controller, Delete, Get, Param, Post } from "@nestjs/common";
import { ApiKeysService } from "./api-keys.service";
import { CurrentUser } from "../common/current-user.decorator";
import type { AuthUser } from "../common/auth.guard";

@Controller("api/api-keys")
export class ApiKeysController {
  constructor(private readonly keys: ApiKeysService) {}

  @Get()
  list(@CurrentUser() user: AuthUser) {
    return this.keys.list(user.id);
  }

  @Post()
  create(@CurrentUser() user: AuthUser, @Body() body: { label?: string }) {
    return this.keys.create(user.id, body?.label);
  }

  @Delete(":id")
  revoke(@CurrentUser() user: AuthUser, @Param("id") id: string) {
    return this.keys.revoke(user.id, id);
  }
}
