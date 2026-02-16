import Link from "next/link";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="space-y-4">
      <nav className="flex gap-4 text-sm text-slate-300">
        <Link href="/app">IDE</Link>
        <Link href="/app/projects">Projects</Link>
        <Link href="/app/deploys">Deploys</Link>
        <Link href="/account">Account</Link>
      </nav>
      {children}
    </div>
  );
}
