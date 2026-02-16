import { WalletPanel } from "./WalletPanel";

export default function IdeHomePage() {
  return (
    <div className="space-y-4">
      <WalletPanel />
      <h1 className="text-2xl font-semibold">Chat IDE</h1>
      <div className="card">
        <form action="/api/chat" method="post" className="space-y-3">
          <textarea className="w-full rounded bg-slate-800 p-3" name="prompt" placeholder="Describe the Animica contract to generate" rows={6} />
          <button className="rounded bg-indigo-500 px-3 py-2">Generate Contract</button>
        </form>
      </div>
    </div>
  );
}
