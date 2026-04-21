import { useMutation, useQuery } from "@tanstack/react-query";
import { fetchReports, hideToken } from "../lib/api";

export function AdminPage() {
  const reportsQ = useQuery({ queryKey: ["reports"], queryFn: fetchReports });

  const hideM = useMutation({
    mutationFn: ({ tokenId, hidden }: { tokenId: string; hidden: boolean }) => hideToken(tokenId, hidden),
    onSuccess() {
      reportsQ.refetch();
    }
  });

  return (
    <section className="stack-lg">
      <div className="card">
        <h2>Admin Moderation</h2>
        <p className="muted">Review reports and hide/unhide abusive token pages.</p>
      </div>

      <section className="card">
        <h3>Reports</h3>
        <ul className="list-clean">
          {(reportsQ.data ?? []).map((report) => (
            <li key={report.id}>
              <div className="row-between">
                <span>
                  {report.tokenId} · {report.reason} · {new Date(report.createdAt).toLocaleString()}
                </span>
                <div className="inline-actions">
                  <button className="btn-ghost" onClick={() => hideM.mutate({ tokenId: report.tokenId, hidden: true })}>Hide</button>
                  <button className="btn-ghost" onClick={() => hideM.mutate({ tokenId: report.tokenId, hidden: false })}>Unhide</button>
                </div>
              </div>
            </li>
          ))}
          {reportsQ.isLoading ? <li className="muted">Loading reports...</li> : null}
        </ul>
      </section>
    </section>
  );
}
