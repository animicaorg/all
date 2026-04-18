import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getOrderStatus } from "../lib/api";

export function StatusPage() {
  const { orderId } = useParams();
  const query = useQuery({
    queryKey: ["order-status", orderId],
    queryFn: () => getOrderStatus(orderId || ""),
    enabled: Boolean(orderId),
    refetchInterval: 7000
  });

  return (
    <div className="grid">
      <section className="sub-hero">
        <h1 style={{ margin: 0 }}>Order Status</h1>
      </section>
      <section className="section">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <div style={{ fontSize: 13, color: "#486079" }}>Order ID</div>
            <div className="mono">{orderId}</div>
          </div>
          <Link className="btn secondary" to="/bridge">
            New Order
          </Link>
        </div>
        {query.isLoading && <div style={{ marginTop: 12 }}>Loading order...</div>}
        {query.isError && <div style={{ marginTop: 12 }}>Unable to load order status.</div>}
        {query.data?.order && (
          <div style={{ marginTop: 12 }} className="status-grid">
            <Data label="Direction" value={query.data.order.direction} />
            <Data label="Current State" value={query.data.order.status} />
            <Data label="Deposit Seen?" value={query.data.order.deposit_tx_hash ? "yes" : "no"} />
            <Data
              label="Confirmations"
              value={`${query.data.order.confirmation_count_current}/${query.data.order.confirmation_count_required}`}
            />
            <Data label="Settlement TX" value={query.data.order.settlement_tx_hash || "pending"} />
            <Data label="Release TX" value={query.data.order.release_tx_hash || "n/a"} />
            <Data label="Created" value={new Date(query.data.order.created_at).toLocaleString()} />
            <Data label="Expires" value={new Date(query.data.order.expires_at).toLocaleString()} />
            <Data label="Source Address" value={query.data.order.source_address} />
            <Data label="Destination Address" value={query.data.order.destination_address} />
            <Data
              label="Claim Code"
              value={
                query.data.order.claim_code_required
                  ? query.data.order.claim_code_confirmed
                    ? "required and confirmed"
                    : "required and pending"
                  : "not required"
              }
            />
            <Data
              label="Manual Review"
              value={query.data.order.manual_review_required ? query.data.order.manual_review_reason || "required" : "no"}
            />
          </div>
        )}
        {query.data?.events?.length ? (
          <div style={{ marginTop: 14 }}>
            <h3 style={{ margin: 0 }}>Event Stream</h3>
            <div className="grid" style={{ marginTop: 10 }}>
              {query.data.events.map((event: any) => (
                <div key={event.id} className="mini-box">
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className="badge">{event.to_status}</span>
                    <span style={{ fontSize: 12 }}>{new Date(event.created_at).toLocaleString()}</span>
                  </div>
                  <div style={{ marginTop: 6, fontSize: 14 }}>{event.reason || "state transition"}</div>
                  {event.payload && Object.keys(event.payload).length > 0 && (
                    <pre style={{ margin: "8px 0 0", fontSize: 12 }}>{JSON.stringify(event.payload, null, 2)}</pre>
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function Data({ label, value }: { label: string; value: string }) {
  return (
    <div className="mini-box">
      <div style={{ fontSize: 12, color: "#486079" }}>{label}</div>
      <div className="mono" style={{ marginTop: 4 }}>
        {value}
      </div>
    </div>
  );
}
