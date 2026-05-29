import type { RiskLevel } from "./constants";

export interface RiskInput {
  ageMinutes: number;
  hasWebsite: boolean;
  hasGithub: boolean;
  verifiedCreator: boolean;
  liquidityAnm: number;
  holderConcentration?: number | null;
  reportsOpen: number;
  adminWarning?: boolean;
}

export interface RiskSignal {
  code: string;
  label: string;
  severity: "info" | "warn" | "danger";
}

export interface RiskAssessment {
  level: RiskLevel;
  score: number;
  signals: RiskSignal[];
}

export function assessRisk(input: RiskInput): RiskAssessment {
  const signals: RiskSignal[] = [];
  let score = 0;

  if (input.adminWarning) {
    signals.push({ code: "ADMIN", label: "Admin warning", severity: "danger" });
    score += 50;
  }
  if (input.ageMinutes < 60) {
    signals.push({ code: "NEW", label: "Brand-new project", severity: "info" });
    score += 5;
  } else if (input.ageMinutes < 60 * 24) {
    signals.push({ code: "YOUNG", label: "<24h old", severity: "info" });
    score += 2;
  }
  if (!input.verifiedCreator) {
    signals.push({ code: "UNVERIFIED", label: "Unverified creator", severity: "warn" });
    score += 10;
  }
  if (!input.hasWebsite) {
    signals.push({ code: "NO_WEB", label: "No website", severity: "warn" });
    score += 5;
  }
  if (!input.hasGithub) {
    signals.push({ code: "NO_GH", label: "No GitHub", severity: "info" });
    score += 2;
  }
  if (input.liquidityAnm < 100) {
    signals.push({ code: "LOW_LIQ", label: "Low liquidity", severity: "warn" });
    score += 8;
  }
  if (typeof input.holderConcentration === "number" && input.holderConcentration > 0.5) {
    signals.push({
      code: "CONC",
      label: `Top holders >${Math.round(input.holderConcentration * 100)}%`,
      severity: "warn"
    });
    score += 15;
  }
  if (input.reportsOpen > 0) {
    signals.push({
      code: "REPORTS",
      label: `${input.reportsOpen} open report${input.reportsOpen === 1 ? "" : "s"}`,
      severity: input.reportsOpen >= 3 ? "danger" : "warn"
    });
    score += Math.min(40, input.reportsOpen * 8);
  }

  let level: RiskLevel = "UNKNOWN";
  if (score >= 40) level = "HIGH";
  else if (score >= 18) level = "MEDIUM";
  else if (score >= 0) level = "LOW";

  return { level, score, signals };
}
