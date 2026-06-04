import { IsIn, IsOptional, IsString, Matches, MinLength } from "class-validator";

export const MINING_MODES = ["ANM_ONLY", "XMR_ONLY", "DUAL_50_50"] as const;
export const PAYOUT_ASSETS = ["ANM", "XMR", "SOL", "USDT", "BTC", "SPLIT_ANM_XMR", "ORIGINAL_ASSET"] as const;

export class UpsertMiningAccountDto {
  @IsString()
  @MinLength(3)
  username!: string;

  @IsOptional()
  @IsString()
  workerName?: string;

  @IsIn(MINING_MODES as unknown as string[])
  mode!: string;

  @IsIn(PAYOUT_ASSETS as unknown as string[])
  payoutAsset!: string;

  @Matches(/^anim1[0-9a-z]{30,}$/, { message: "anmAddress must be a bech32 anim1 address" })
  anmAddress!: string;

  @IsOptional()
  @IsString()
  xmrAddress?: string;

  @IsOptional()
  @IsString()
  payoutAddress?: string;
}

export class SetModeDto {
  @IsIn(MINING_MODES as unknown as string[])
  mode!: string;
}

export class SetPayoutDto {
  @IsIn(PAYOUT_ASSETS as unknown as string[])
  payoutAsset!: string;

  @IsOptional()
  @IsString()
  payoutAddress?: string;
}
