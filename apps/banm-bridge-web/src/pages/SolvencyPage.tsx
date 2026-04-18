import { useQuery } from "@tanstack/react-query";
import { getPublicSolvency } from "../lib/api";

export function SolvencyPage() {
  const query = useQuery({
    queryKey: ["solvency"],
    queryFn: getPublicSolvency,
    refetchInterval: 15000
  });

  const data = query.data;
  return (
    <div className="grid">
      <section className="sub-hero">
        <h1 style={{ margin: 0 }}>Operational Solvency</h1>
      </section>
      <section className="section">
        {query.isLoading && <div>Loading solvency metrics...</div>}
        {query.isError && <div>Unable to load solvency metrics.</div>}
        {data && (
          <div className="grid three">
            <Metric label="ANM Reserve (base units)" value={String(data.reserve_anm_confirmed)} />
            <Metric label="BANM Total Supply (wei)" value={String(data.banm_total_supply_wei)} />
            <Metric label="Pending Forward Mints (wei)" value={String(data.pending_forward_mints_wei)} />
            <Metric label="Pending Reverse Releases (ANM base units)" value={String(data.pending_reverse_releases_anm)} />
            <Metric label="Effective Liabilities (wei)" value={String(data.effective_liabilities_wei)} />
            <Metric label="Available Redeemable ANM" value={String(data.available_redeemable_anm)} />
          </div>
        )}
      </section>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="mini-box">
      <div style={{ fontSize: 12, color: "#486079" }}>{label}</div>
      <div className="mono" style={{ fontWeight: 700, marginTop: 6 }}>
        {value}
      </div>
    </div>
  );
}
