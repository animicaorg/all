import { Controller, Get } from "@nestjs/common";
import { Public } from "./common/auth.guard";

@Controller()
export class HealthController {
  @Public()
  @Get("api/health")
  health() {
    return { ok: true, service: "animica-pool-api", ts: new Date().toISOString() };
  }
}
