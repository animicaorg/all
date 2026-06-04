"use server";

import { redirect } from "next/navigation";
import { requireUser } from "@/server/auth";
import { createCreditPurchase, PurchaseAmountError } from "@/server/billing";

export interface PurchaseState {
  error?: string;
}

export async function startPurchaseAction(
  _prev: PurchaseState,
  formData: FormData,
): Promise<PurchaseState> {
  let invoiceUrl: string;
  try {
    const user = await requireUser();
    const amountUsd = String(formData.get("amountUsd") ?? "").trim();
    const { invoiceUrl: url } = await createCreditPurchase({ userId: user.id, amountUsd });
    invoiceUrl = url;
  } catch (err) {
    if (err instanceof PurchaseAmountError) return { error: err.message };
    return {
      error:
        err instanceof Error
          ? `Could not start checkout: ${err.message}`
          : "Could not start checkout.",
    };
  }
  // Outside try/catch: redirect() throws a control-flow signal by design.
  redirect(invoiceUrl);
}
