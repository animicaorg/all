import { Controller, Headers, Ip, Post } from "@nestjs/common";
import { Public } from "../common/auth.guard";
import { TrialService } from "./trial.service";

// POST /v1/keys/trial — public, no auth. Issues a free trial API key (abuse-
// controlled in TrialService). Powers the MCP server's zero-config onboarding.
@Public()
@Controller("v1/keys")
export class TrialController {
  constructor(private readonly trial: TrialService) {}

  @Post("trial")
  async issue(@Headers("x-forwarded-for") xff: string, @Ip() ip: string) {
    const clientIp = (xff || "").split(",")[0]?.trim() || ip || "0.0.0.0";
    return this.trial.issueTrial(clientIp);
  }
}
