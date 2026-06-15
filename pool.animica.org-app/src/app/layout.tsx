import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { getSessionUser } from "@/server/auth";

export const metadata: Metadata = {
  title: "Animica Rig Rental",
  description: "Rent out your mining rig, or rent hashpower to mine ANM or Monero.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const user = await getSessionUser();
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header className="border-b border-white/10">
          <nav className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
            <Link href="/" className="font-semibold tracking-tight">
              Animica <span className="text-blue-400">Rig Rental</span>
            </Link>
            <div className="flex items-center gap-4 text-sm">
              <Link href="/about-ena" className="hover:text-blue-300">About ENA</Link>
              <Link href="/training-pools" className="hover:text-blue-300">Training Pools</Link>
              <Link href="/mining-onboard" className="hover:text-blue-300">Mining</Link>
              <Link href="/download" className="hover:text-blue-300">Download</Link>
              <Link href="/browse" className="hover:text-blue-300">Rent</Link>
              <Link href="/owner" className="hover:text-blue-300">Rent out</Link>
              {user?.isAdmin && (
                <Link href="/admin" className="hover:text-blue-300">Admin</Link>
              )}
              {user ? (
                <span className="text-white/60">{user.email}</span>
              ) : (
                <Link href="/login" className="rounded bg-blue-600 px-3 py-1.5 text-white hover:bg-blue-500">
                  Sign in
                </Link>
              )}
            </div>
          </nav>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-8">{children}</main>
      </body>
    </html>
  );
}
