export interface ModeOption {
  code: string;
  name: string;
  description: string;
}

export const ASSISTANT_MODES: ModeOption[] = [
  { code: 'general', name: 'General', description: 'Open-ended assistant.' },
  { code: 'coding', name: 'Coding', description: 'Pair programmer with GitHub + GitLab tools.' },
  { code: 'animica_blockchain', name: 'Animica Helper', description: 'Wallet, explorer, node, ecosystem Q&A.' },
  { code: 'marketing', name: 'Marketing', description: 'Landing copy, launch posts.' },
  { code: 'art_prompt', name: 'Art Prompts', description: 'Image-gen prompts.' },
  { code: 'business', name: 'Business', description: 'Lean canvas, GTM, pricing.' },
];

export function AssistantModePicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (code: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-md border border-white/8 bg-ink-900/40 px-2 py-1.5 text-sm text-ink-100 focus:border-accent-500 focus:outline-none"
    >
      {ASSISTANT_MODES.map((m) => (
        <option key={m.code} value={m.code}>{m.name}</option>
      ))}
    </select>
  );
}
