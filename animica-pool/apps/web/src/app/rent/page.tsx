"use client";

import Link from "next/link";
import { RentListings } from "@/components/RentListings";

export default function RentPage() {
  return (
    <div className="space-y-6">
      <div className="flex gap-3 text-sm">
        <Link href="/rent" className="text-neon-green">All</Link>
        <Link href="/rent/gpu" className="text-white/60 hover:text-white">GPU</Link>
        <Link href="/rent/cpu" className="text-white/60 hover:text-white">CPU</Link>
      </div>
      <RentListings title="Rent compute" />
    </div>
  );
}
