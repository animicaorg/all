/**
 * Static tutorial catalog. The web frontend has the markdown bodies; this
 * server-side manifest is the source of truth for IDs, reward amounts, and
 * step counts (all the things that affect payouts).
 *
 * Anything that changes a reward amount or step ID must go through code
 * review here — that's intentional, since these are the load-bearing
 * numbers that drive on-chain payouts.
 */
export interface TutorialDef {
  id: string;
  title: string;
  description: string;
  track: "wallet" | "aicf" | "contracts" | "node";
  difficulty: "intro" | "intermediate" | "advanced";
  rewardAnim: string;
  estimatedMinutes: number;
  steps: Array<{ id: string; title: string; rewardAnim: string }>;
}

export interface BadgeDef {
  id: string;
  title: string;
  description: string;
  /** Tutorials whose all-steps completion awards this badge. */
  requiredTutorials: string[];
  rewardAnim: string;
}

export const TUTORIALS: TutorialDef[] = [
  {
    id: "install-wallet",
    title: "Install the Animica wallet",
    description:
      "Install the browser-extension wallet, create a fresh wallet, back up the seed, fund it from the faucet.",
    track: "wallet",
    difficulty: "intro",
    rewardAnim: "1",
    estimatedMinutes: 5,
    steps: [
      { id: "install", title: "Install the extension", rewardAnim: "0.25" },
      { id: "seed-backup", title: "Create a wallet and back up the seed", rewardAnim: "0.25" },
      { id: "faucet", title: "Fund from the academy faucet", rewardAnim: "0.25" },
      { id: "balance-check", title: "See your balance in the extension", rewardAnim: "0.25" },
    ],
  },
  {
    id: "send-first-tx",
    title: "Send your first transaction",
    description: "Send ANIMICA from your wallet to a friend's address and confirm the transfer on the explorer.",
    track: "wallet",
    difficulty: "intro",
    rewardAnim: "1.5",
    estimatedMinutes: 4,
    steps: [
      { id: "compose", title: "Compose a transfer", rewardAnim: "0.5" },
      { id: "broadcast", title: "Sign and broadcast", rewardAnim: "0.5" },
      { id: "explorer-check", title: "Verify on the explorer", rewardAnim: "0.5" },
    ],
  },
  {
    id: "aicf-first-chat",
    title: "Run your first AICF chat",
    description:
      "Install the animica CLI, run animica chat, send a prompt that's served by a real distributed worker, settle the credit on-chain.",
    track: "aicf",
    difficulty: "intro",
    rewardAnim: "2",
    estimatedMinutes: 8,
    steps: [
      { id: "install-cli", title: "Install the animica CLI", rewardAnim: "0.5" },
      { id: "configure-wallet", title: "Configure the wallet for chat", rewardAnim: "0.5" },
      { id: "first-prompt", title: "Send your first prompt", rewardAnim: "0.5" },
      { id: "settle", title: "Settle the job on-chain", rewardAnim: "0.5" },
    ],
  },
  {
    id: "aicf-worker",
    title: "Become an AICF compute provider",
    description:
      "Detect your hardware, register as an AICF worker, and serve your first inference job for an animica chat user.",
    track: "aicf",
    difficulty: "intermediate",
    rewardAnim: "5",
    estimatedMinutes: 12,
    steps: [
      { id: "hardware", title: "Detect your hardware", rewardAnim: "1" },
      { id: "register", title: "Register the worker", rewardAnim: "1" },
      { id: "first-job", title: "Serve your first job", rewardAnim: "2" },
      { id: "verify-earning", title: "Verify your earning", rewardAnim: "1" },
    ],
  },
  {
    id: "deploy-counter",
    title: "Deploy your first contract",
    description:
      "Deploy the counter contract from animica/stdlib, call it from the wallet, and read the new state on the explorer.",
    track: "contracts",
    difficulty: "intermediate",
    rewardAnim: "3",
    estimatedMinutes: 10,
    steps: [
      { id: "compile", title: "Compile the counter", rewardAnim: "0.5" },
      { id: "deploy", title: "Deploy the bytecode", rewardAnim: "1" },
      { id: "call-increment", title: "Call increment()", rewardAnim: "1" },
      { id: "read-state", title: "Read the counter", rewardAnim: "0.5" },
    ],
  },
  {
    id: "run-a-node",
    title: "Run a local node",
    description:
      "Bring up an animica node with `animica node up`, watch the head height climb, query the local RPC.",
    track: "node",
    difficulty: "intro",
    rewardAnim: "2",
    estimatedMinutes: 8,
    steps: [
      { id: "node-up", title: "Bring up the node", rewardAnim: "0.5" },
      { id: "head-sync", title: "Watch the head height climb", rewardAnim: "0.75" },
      { id: "rpc-query", title: "Query the local RPC", rewardAnim: "0.75" },
    ],
  },
];

export const BADGES: BadgeDef[] = [
  {
    id: "wallet-track-complete",
    title: "Wallet Pilot",
    description: "Completed every tutorial in the Wallet basics track.",
    requiredTutorials: ["install-wallet", "send-first-tx"],
    rewardAnim: "5",
  },
  {
    id: "aicf-track-complete",
    title: "AICF Operator",
    description: "Submitted a chat job and served one as a worker.",
    requiredTutorials: ["aicf-first-chat", "aicf-worker"],
    rewardAnim: "10",
  },
  {
    id: "contracts-track-complete",
    title: "Contracts Engineer",
    description: "Deployed and called a contract end-to-end.",
    requiredTutorials: ["deploy-counter"],
    rewardAnim: "5",
  },
  {
    id: "node-track-complete",
    title: "Node Runner",
    description: "Brought up a node and queried it.",
    requiredTutorials: ["run-a-node"],
    rewardAnim: "5",
  },
];

export function tutorialById(id: string): TutorialDef | undefined {
  return TUTORIALS.find((t) => t.id === id);
}

export function stepById(
  tutorial: TutorialDef,
  stepId: string,
): TutorialDef["steps"][number] | undefined {
  return tutorial.steps.find((s) => s.id === stepId);
}
