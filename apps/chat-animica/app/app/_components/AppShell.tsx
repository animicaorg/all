"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode } from "react";

const tabs = [
  { href: "/app", label: "Chat" },
  { href: "/app/projects", label: "Projects" },
  { href: "/app/deploys", label: "Deploys" },
  { href: "/account", label: "Account" }
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isChat = pathname === "/app";

  return (
    <div className="min-h-screen pb-20 md:pb-0">
      <header className="sticky top-0 z-20 h-14 border-b border-slate-800 bg-slate-950/95 backdrop-blur md:ml-60">
        <div className="mx-auto flex h-full max-w-6xl items-center justify-between px-4">
          <h1 className="text-base font-semibold">Animica Studio</h1>
          <span className="text-xs text-slate-400">Mobile-first contract IDE</span>
        </div>
      </header>
      <aside className="fixed left-0 top-0 hidden h-screen w-60 border-r border-slate-800 bg-slate-950 p-4 md:block">
        <h2 className="mb-4 font-semibold">Navigation</h2>
        <nav className="space-y-2 text-sm">
          {tabs.map((tab) => (
            <Link key={tab.href} href={tab.href} className={`block rounded-lg px-3 py-2 ${pathname === tab.href ? "bg-indigo-500 text-white" : "text-slate-300 hover:bg-slate-800"}`}>
              {tab.label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="mx-auto max-w-6xl space-y-4 px-3 py-4 md:ml-60 md:px-6">{children}</main>
      {isChat ? (
        <a href="#chat-composer" className="fixed bottom-20 right-4 z-20 rounded-full bg-indigo-500 px-4 py-3 text-sm font-semibold shadow-lg md:hidden" aria-label="New Chat">
          New Chat
        </a>
      ) : null}
      <nav className="fixed bottom-0 left-0 right-0 z-30 grid h-16 grid-cols-4 border-t border-slate-800 bg-slate-950 md:hidden">
        {tabs.map((tab) => (
          <Link key={tab.href} href={tab.href} className={`flex items-center justify-center text-xs ${pathname === tab.href ? "text-indigo-300" : "text-slate-400"}`}>
            {tab.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
