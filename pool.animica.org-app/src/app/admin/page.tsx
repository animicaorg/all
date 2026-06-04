import Link from "next/link";
import { prisma } from "@/server/db";
import { getSessionUser } from "@/server/auth";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const user = await getSessionUser();
  if (!user?.isAdmin) {
    return <p className="rounded-lg border border-white/10 bg-white/5 p-4 text-sm">Admin only.</p>;
  }
  const rentals = await prisma.rental.findMany({
    orderBy: { createdAt: "desc" },
    take: 100,
    include: { rig: { select: { workerName: true } } },
  });
  const review = rentals.filter((r) => r.status === "NEEDS_ADMIN_REVIEW").length;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Admin · rentals</h1>
        {review > 0 && (
          <span className="rounded-full bg-amber-500/20 px-3 py-1 text-sm text-amber-300">
            {review} need review
          </span>
        )}
      </div>
      <div className="overflow-x-auto rounded-xl border border-white/10">
        <table className="w-full text-sm">
          <thead className="bg-white/5 text-left text-white/50">
            <tr>
              <th className="px-3 py-2">Rental</th>
              <th className="px-3 py-2">Rig</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Gross</th>
              <th className="px-3 py-2">Coins</th>
              <th className="px-3 py-2">Created</th>
            </tr>
          </thead>
          <tbody>
            {rentals.map((r) => (
              <tr key={r.id} className="border-t border-white/5 hover:bg-white/5">
                <td className="px-3 py-2">
                  <Link href={`/admin/rentals/${r.id}`} className="text-blue-400">
                    {r.id.slice(0, 8)}
                  </Link>
                </td>
                <td className="px-3 py-2">{r.rig.workerName}</td>
                <td className="px-3 py-2">
                  <span className={r.status === "NEEDS_ADMIN_REVIEW" ? "text-amber-300" : ""}>{r.status}</span>
                </td>
                <td className="px-3 py-2">${r.grossUsd}</td>
                <td className="px-3 py-2">{r.coins}</td>
                <td className="px-3 py-2 text-white/50">{r.createdAt.toISOString().slice(0, 16)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
