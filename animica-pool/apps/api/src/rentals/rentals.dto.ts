import { IsIn, IsInt, IsNumber, IsOptional, IsString, Min } from "class-validator";

export const PROVIDER_TYPES = ["cpu", "gpu", "ai_endpoint", "dedicated_model", "batch_job"] as const;

export class CreateListingDto {
  @IsIn(PROVIDER_TYPES as unknown as string[])
  providerType!: string;

  @IsString()
  title!: string;

  @IsOptional() @IsString() description?: string;
  @IsOptional() @IsString() cpuModel?: string;
  @IsOptional() @IsString() gpuModel?: string;
  @IsOptional() @IsInt() vramGb?: number;
  @IsOptional() @IsInt() ramGb?: number;
  @IsOptional() @IsInt() storageGb?: number;
  @IsOptional() @IsString() region?: string;

  @IsNumber() @Min(0)
  pricePerHourUsd!: number;

  @IsOptional() @IsNumber() pricePerDayUsd?: number;
  @IsOptional() @IsString() workerId?: string;
  @IsOptional() supportedModels?: string[];
}

export class CreateOrderDto {
  @IsString()
  listingId!: string;

  @IsInt() @Min(1)
  hours!: number;

  @IsIn(["credits", "crypto"])
  payWith!: "credits" | "crypto";
}
