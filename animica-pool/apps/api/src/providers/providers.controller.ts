import { Body, Controller, Get, Param, Patch, Post } from "@nestjs/common";
import { ProvidersService } from "./providers.service";
import { Public, Roles } from "../common/auth.guard";
import { CurrentUser } from "../common/current-user.decorator";
import type { AuthUser } from "../common/auth.guard";

@Controller()
export class ProvidersController {
  constructor(private readonly providers: ProvidersService) {}

  @Public()
  @Get("api/providers")
  list() {
    return this.providers.list();
  }

  @Roles("ADMIN")
  @Post("api/providers/:name/test")
  test(@Param("name") name: string) {
    return this.providers.test(name);
  }

  @Roles("ADMIN")
  @Get("api/admin/providers")
  adminList() {
    return this.providers.list();
  }

  @Roles("ADMIN")
  @Patch("api/admin/providers/:name")
  setConfig(@CurrentUser() user: AuthUser, @Param("name") name: string, @Body() dto: { isEnabled?: boolean; priority?: number }) {
    return this.providers.setConfig(user.email, name, dto);
  }
}
