import { Body, Controller, Delete, Get, Headers, Param, Post } from "@nestjs/common";
import { WorkersService } from "./workers.service";
import { Public } from "../common/auth.guard";
import { CurrentUser } from "../common/current-user.decorator";
import type { AuthUser } from "../common/auth.guard";

// Session routes (logged-in user manages worker profiles).
@Controller("api/workers")
export class WorkersController {
  constructor(private readonly workers: WorkersService) {}

  @Get()
  listMine(@CurrentUser() user: AuthUser) {
    return this.workers.listMine(user.id);
  }

  @Post()
  create(@CurrentUser() user: AuthUser, @Body() body: { name: string; earningMode?: string; region?: string }) {
    return this.workers.create(user.id, body);
  }

  @Get(":id/stats")
  stats(@CurrentUser() user: AuthUser, @Param("id") id: string) {
    return this.workers.stats(user.id, id);
  }

  @Post(":id/token")
  regenerateToken(@CurrentUser() user: AuthUser, @Param("id") id: string) {
    return this.workers.regenerateToken(user.id, id);
  }

  @Post(":id/rental")
  listForRent(@CurrentUser() user: AuthUser, @Param("id") id: string, @Body() body: { pricePerHourUsd: number; title?: string }) {
    return this.workers.listForRent(user.id, id, body);
  }

  @Delete(":id/rental")
  unlistRental(@CurrentUser() user: AuthUser, @Param("id") id: string) {
    return this.workers.unlistRental(user.id, id);
  }

  @Delete(":id")
  remove(@CurrentUser() user: AuthUser, @Param("id") id: string) {
    return this.workers.remove(user.id, id);
  }
}

// Machine routes — authenticated by the worker token (Bearer anm_worker_…).
@Public()
@Controller("api/workers")
export class WorkerMachineController {
  constructor(private readonly workers: WorkersService) {}

  @Post("register")
  register(@Headers("authorization") auth: string, @Body() body: any) {
    return this.workers.register(auth, body);
  }

  @Post("heartbeat")
  heartbeat(@Headers("authorization") auth: string) {
    return this.workers.heartbeat(auth);
  }

  @Post("benchmark")
  benchmark(@Headers("authorization") auth: string, @Body() body: { score: number; details?: unknown }) {
    return this.workers.benchmark(auth, body);
  }

  @Get("me")
  me(@Headers("authorization") auth: string) {
    return this.workers.me(auth);
  }

  @Get("earnings")
  earnings(@Headers("authorization") auth: string) {
    return this.workers.earnings(auth);
  }

  @Get("jobs")
  jobs() {
    return this.workers.claimableJobs();
  }

  @Post("jobs/:id/claim")
  claim(@Headers("authorization") auth: string, @Param("id") id: string) {
    return this.workers.claimJob(auth, id);
  }

  @Post("jobs/:id/complete")
  complete(@Headers("authorization") auth: string, @Param("id") id: string, @Body() body: { rewardUsd?: number }) {
    return this.workers.completeJob(auth, id, body);
  }
}
