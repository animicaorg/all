"use server";

import { revalidatePath } from "next/cache";
import { requireUser } from "@/server/auth";
import { mintApiKey, revokeApiKey } from "@/server/apiAuth";
import { prisma } from "@/server/db";

export interface CreateKeyState {
  raw?: string;
  prefix?: string;
  error?: string;
}

export async function createKeyAction(
  _prev: CreateKeyState,
  formData: FormData,
): Promise<CreateKeyState> {
  try {
    const user = await requireUser();
    const label = (formData.get("label") as string | null)?.slice(0, 80) || undefined;
    const key = await mintApiKey(user.id, label);
    revalidatePath("/keys");
    return { raw: key.raw, prefix: key.prefix };
  } catch (err) {
    return { error: err instanceof Error ? err.message : "Could not create key." };
  }
}

export async function revokeKeyAction(formData: FormData): Promise<void> {
  const user = await requireUser();
  const id = formData.get("id") as string;
  // Ownership check before revoking.
  const key = await prisma.apiKey.findUnique({ where: { id } });
  if (key && key.userId === user.id) {
    await revokeApiKey(id);
  }
  revalidatePath("/keys");
}
