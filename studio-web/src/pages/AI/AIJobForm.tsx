import * as React from "react";

/**
 * Reusable AI Job form for model/prompt/units with cost preview.
 * It does not own any queue state — callers pass an onSubmit handler.
 */
export type AIJobFormValues = {
  model: string;
  prompt: string;
  maxUnits: number;
};

export type ModelSpec = {
  id: string;
  label?: string;
  /** Optional price per AI unit in the caller's currency (e.g., USD). */
  pricePerUnit?: number;
};

export interface AIJobFormProps {
  onSubmit: (values: AIJobFormValues) => Promise<void> | void;
  busy?: boolean;

  /** Available models to choose from. Defaults to small demo set. */
  models?: ModelSpec[];
  /** Which model is pre-selected. Defaults to first in the list. */
  defaultModel?: string;

  /** Starting units; user can change. Defaults to 256. */
  defaultUnits?: number;
  /** Upper bound for units input. Defaults to 2048. */
  maxUnitsCap?: number;

  /**
   * Custom cost estimator. If provided, it takes precedence over built-in
   * price-per-unit math. Return a {label, value} pair, where label is what to
   * render (e.g., "~$0.12"), and value is the numeric amount for semantics.
   */
  estimate?: (units: number, modelId: string) => { label: string; value: number } | null;

  /** Character limit for prompt textarea. Defaults to 4000. */
  promptLimit?: number;

  /** Optional demo filler */
  demo?: { model?: string; prompt?: string; units?: number };
}

export default function AIJobForm(props: AIJobFormProps) {
  const {
    onSubmit,
    busy = false,
    models = defaultModels,
    defaultModel,
    defaultUnits = 256,
    maxUnitsCap = 2048,
    estimate,
    promptLimit = 4000,
    demo,
  } = props;

  const initialModel = defaultModel ?? (models[0]?.id ?? "animica/gpt-mini");
  const [model, setModel] = React.useState<string>(initialModel);
  const [prompt, setPrompt] = React.useState<string>("");
  const [units, setUnits] = React.useState<number>(defaultUnits);
  const [submitting, setSubmitting] = React.useState<boolean>(false);
  const [error, setError] = React.useState<string | null>(null);

  const chars = prompt.length;
  const over = chars > promptLimit;

  const costLabel = React.useMemo(() => {
    if (estimate) {
      const res = estimate(units, model);
      if (res && typeof res.label === "string") return res.label;
    }
    const m = models.find((x) => x.id === model);
    if (m && typeof m.pricePerUnit === "number") {
      const v = m.pricePerUnit * units;
      return `~${formatCurrency(v)}`;
    }
    return `~${units} AIU`;
  }, [estimate, units, model, models]);

  function onFillDemo() {
    setModel(demo?.model ?? "animica/gpt-mini");
    setPrompt(
      demo?.prompt ??
        "Write a short haiku about deterministic blockchains and verifiable AI results."
    );
    setUnits(demo?.units ?? 128);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!prompt.trim()) {
      setError("Prompt cannot be empty.");
      return;
    }
    if (over) {
      setError(`Prompt exceeds ${promptLimit} characters.`);
      return;
    }
    try {
      setSubmitting(true);
      await onSubmit({ model, prompt, maxUnits: units });
    } catch (err: any) {
      setError(err?.message ?? String(err));
    } finally {
      setSubmitting(false);
    }
  }

  const disabled = submitting || busy;

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Field label="Model">
          <select
            className="w-full border rounded px-3 py-2 text-sm bg-white"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={disabled}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label ?? m.id}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Max AI Units">
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={32}
              step={32}
              max={maxUnitsCap}
              className="w-28 border rounded px-3 py-2 text-sm"
              value={units}
              onChange={(e) => setUnits(sanitizeUnits(e.target.value, maxUnitsCap))}
              disabled={disabled}
            />
            <input
              type="range"
              min={32}
              max={maxUnitsCap}
              step={32}
              className="flex-1"
              value={units}
              onChange={(e) => setUnits(Number(e.target.value))}
              disabled={disabled}
            />
          </div>
          <div className="text-xs text-[color:var(--muted,#6b7280)] mt-1">
            Estimate: <span className="font-medium">{costLabel}</span>
          </div>
        </Field>

        <div className="flex items-end gap-2">
          <button
            type="button"
            className="px-3 py-2 border rounded text-sm"
            onClick={onFillDemo}
            disabled={disabled}
          >
            Fill demo
          </button>
        </div>
      </div>

      <Field label="Prompt">
        <textarea
          className="w-full border rounded px-3 py-2 text-sm min-h-[140px]"
          placeholder="Describe your prompt…"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          disabled={disabled}
          maxLength={promptLimit ? undefined : undefined}
        />
        <div
          className={`text-xs mt-1 ${
            over ? "text-red-700" : "text-[color:var(--muted,#6b7280)]"
          }`}
        >
          {chars}/{promptLimit} characters
        </div>
      </Field>

      {error && (
        <div
          role="alert"
          className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2"
        >
          {error}
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          type="submit"
          className="px-4 py-2 rounded bg-blue-600 text-white text-sm disabled:opacity-60"
          disabled={disabled}
        >
          {submitting ? "Enqueuing…" : "Enqueue Job"}
        </button>
        <span className="text-xs text-[color:var(--muted,#6b7280)]">
          Jobs execute off-chain; results are referenced by proofs on-chain.
        </span>
      </div>
    </form>
  );
}

/* --------------------------------- UI bits -------------------------------- */

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-1">
        {label}
      </div>
      {children}
    </label>
  );
}

/* --------------------------------- utils --------------------------------- */

export const defaultModels: ModelSpec[] = [
  { id: "animica/gpt-mini", label: "animica/gpt-mini (demo)", pricePerUnit: 0.0005 },
  { id: "animica/gpt-medium", label: "animica/gpt-medium", pricePerUnit: 0.001 },
  { id: "animica/llama-lite", label: "animica/llama-lite", pricePerUnit: 0.0004 },
];

function formatCurrency(v: number, currency = "USD", locale?: string) {
  try {
    return new Intl.NumberFormat(locale ?? undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: v < 0.1 ? 4 : 2,
    }).format(v);
  } catch {
    // Fallback if Intl not available or currency unknown
    return `$${v.toFixed(4)}`;
  }
}

function sanitizeUnits(value: string, cap: number): number {
  const n = Math.max(32, Math.min(cap, Number(value || 0)));
  // snap to step=32
  return Math.round(n / 32) * 32;
}
