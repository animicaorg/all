import { Body, Controller, Get, Headers, Param, Patch, Post } from "@nestjs/common";
import { BittensorService } from "./bittensor.service";
import { Public, Roles } from "../common/auth.guard";
import { CurrentUser } from "../common/current-user.decorator";
import type { AuthUser } from "../common/auth.guard";

// Session routes — rig owners enroll workers as SN51 executors.
@Controller("api/bittensor")
export class BittensorController {
  constructor(private readonly bittensor: BittensorService) {}

  @Public()
  @Get("overview")
  overview() {
    return this.bittensor.overview();
  }

  @Public()
  @Get("earnings")
  earnings() {
    return this.bittensor.recentEarnings();
  }

  @Get("eligibility/:workerId")
  eligibility(@CurrentUser() user: AuthUser, @Param("workerId") workerId: string) {
    return this.bittensor.eligibility(user.id, workerId);
  }

  @Post("executors/enroll")
  enroll(
    @CurrentUser() user: AuthUser,
    @Body() body: { workerId: string; gpuType?: string; gpuCount?: number; pricePerHourUsd?: number },
  ) {
    return this.bittensor.enroll(user.id, body);
  }

  @Get("executors")
  myExecutors(@CurrentUser() user: AuthUser) {
    return this.bittensor.myExecutors(user.id);
  }

  @Get("executors/:id/provision")
  provision(@CurrentUser() user: AuthUser, @Param("id") id: string) {
    return this.bittensor.provision(user.id, id);
  }
}

// Machine routes — authenticated by the worker token (Bearer anm_worker_…).
// Powers both the worker agent's health reports and the `animica bittensor`
// pip CLI self-service flow (status → enroll → provision).
@Public()
@Controller("api/bittensor")
export class BittensorMachineController {
  constructor(private readonly bittensor: BittensorService) {}

  @Post("executor/report")
  report(@Headers("authorization") auth: string, @Body() body: { running: boolean; executorUuid?: string }) {
    return this.bittensor.reportExecutor(auth, body);
  }

  @Get("executor/me")
  me(@Headers("authorization") auth: string) {
    return this.bittensor.executorMe(auth);
  }

  @Post("executor/enroll")
  enroll(@Headers("authorization") auth: string, @Body() body: { gpuType?: string; gpuCount?: number; pricePerHourUsd?: number }) {
    return this.bittensor.executorEnroll(auth, body ?? {});
  }

  @Get("executor/provision")
  provision(@Headers("authorization") auth: string) {
    return this.bittensor.executorProvision(auth);
  }
}

// Admin routes — miner identity, earnings recording, slashes, pause flag.
@Roles("ADMIN")
@Controller("api/admin/bittensor")
export class BittensorAdminController {
  constructor(private readonly bittensor: BittensorService) {}

  @Get("miners")
  miners() {
    return this.bittensor.adminListMiners();
  }

  @Post("miners")
  setMiner(
    @CurrentUser() user: AuthUser,
    @Body() body: { netuid?: number; hotkeySs58: string; status?: string; registrationBurnTao?: number; notes?: string },
  ) {
    return this.bittensor.adminSetMiner(user.email, body);
  }

  @Get("executors")
  executors() {
    return this.bittensor.adminListExecutors();
  }

  @Patch("executors/:id")
  updateExecutor(
    @CurrentUser() user: AuthUser,
    @Param("id") id: string,
    @Body() body: { status?: string; executorUuid?: string; collateralTao?: number; pricePerHourUsd?: number },
  ) {
    return this.bittensor.adminUpdateExecutor(user.email, id, body);
  }

  @Post("earnings")
  recordEarning(
    @CurrentUser() user: AuthUser,
    @Body() body: {
      minerId?: string; executorId?: string; kind: "emission" | "rental" | "adjustment";
      taoAmount?: number; alphaAmount?: number; usdValue?: number;
      periodStart: string; periodEnd: string; raw?: unknown;
    },
  ) {
    return this.bittensor.adminRecordEarning(user.email, body);
  }

  @Post("executors/:id/slash")
  slash(
    @CurrentUser() user: AuthUser,
    @Param("id") id: string,
    @Body() body: { usdValue?: number; taoAmount?: number; note?: string },
  ) {
    return this.bittensor.adminSlash(user.email, id, body);
  }

  @Post("flags")
  setFlags(@CurrentUser() user: AuthUser, @Body() body: { miningEnabled: boolean }) {
    return this.bittensor.setMiningEnabled(user.email, !!body.miningEnabled);
  }

  @Get("treasury")
  treasury() {
    return this.bittensor.treasuryProgress();
  }

  @Post("poll")
  poll() {
    return this.bittensor.pollOnce("manual");
  }

  @Get("poll")
  pollStatus() {
    return this.bittensor.pollStatus();
  }
}
