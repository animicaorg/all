"use client";

import { useFormState, useFormStatus } from "react-dom";
import { createKeyAction, type CreateKeyState } from "./actions";

const initial: CreateKeyState = {};

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      disabled={pending}
      className="rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
    >
      {pending ? "Creating…" : "Create key"}
    </button>
  );
}

export function KeyManager() {
  const [state, action] = useFormState(createKeyAction, initial);
  return (
    <div className="space-y-3">
      <form action={action} className="flex items-end gap-3">
        <label className="flex-1 text-sm">
          <span className="mb-1 block text-white/60">Label (optional)</span>
          <input
            name="label"
            placeholder="e.g. production"
            className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm outline-none focus:border-blue-400"
          />
        </label>
        <SubmitButton />
      </form>

      {state.error && <p className="text-sm text-red-400">{state.error}</p>}

      {state.raw && (
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4">
          <p className="text-sm text-emerald-300">
            Copy this key now — it won’t be shown again:
          </p>
          <code className="mt-2 block break-all rounded-lg bg-black/40 px-3 py-2 text-sm">
            {state.raw}
          </code>
        </div>
      )}
    </div>
  );
}
