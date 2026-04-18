import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import {
  fetchAdminSolvency,
  fetchOrderDetail,
  fetchOrders,
  markManualReview,
  retryOrder,
  setPauseFlag
} from "../lib/api";

export function DashboardPage() {
  const auth = useAuth();
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("");
  const [directionFilter, setDirectionFilter] = useState("");
  const [selectedOrderId, setSelectedOrderId] = useState<string>("");
  const [manualReason, setManualReason] = useState("manual_review_requested");

  const ordersQuery = useQuery({
    queryKey: ["admin-orders", statusFilter, directionFilter],
    queryFn: () =>
      fetchOrders(auth.token || "", {
        status: statusFilter || undefined,
        direction: directionFilter || undefined,
        limit: 500
      })
  });

  const orderDetailQuery = useQuery({
    queryKey: ["admin-order-detail", selectedOrderId],
    queryFn: () => fetchOrderDetail(auth.token || "", selectedOrderId),
    enabled: Boolean(selectedOrderId)
  });

  const solvencyQuery = useQuery({
    queryKey: ["admin-solvency"],
    queryFn: () => fetchAdminSolvency(auth.token || ""),
    refetchInterval: 15000
  });

  const retryMutation = useMutation({
    mutationFn: (orderId: string) => retryOrder(auth.token || "", orderId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-orders"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-order-detail"] });
    }
  });

  const manualMutation = useMutation({
    mutationFn: (orderId: string) => markManualReview(auth.token || "", orderId, manualReason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-orders"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-order-detail"] });
    }
  });

  const pauseMutation = useMutation({
    mutationFn: ({ flag, paused }: { flag: string; paused: boolean }) => setPauseFlag(auth.token || "", flag, paused),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-solvency"] });
    }
  });

  const rows = useMemo(() => ordersQuery.data?.items || [], [ordersQuery.data?.items]);

  return (
    <div className="admin-page">
      <header className="admin-topbar">
        <div>
          <strong>BANM Bridge Admin</strong>
          <div style={{ fontSize: 12, color: "#8ea4bb" }}>
            user: {auth.username} role: {auth.role}
          </div>
        </div>
        <button
          onClick={() => {
            auth.clearAuth();
          }}
        >
          Sign Out
        </button>
      </header>

      <section className="admin-grid">
        <div className="admin-panel">
          <h3>Reserve & Solvency</h3>
          {solvencyQuery.data?.public && (
            <div className="admin-kpi-grid">
              <Kpi label="Reserve ANM" value={String(solvencyQuery.data.public.reserve_anm_confirmed)} />
              <Kpi label="BANM Supply (wei)" value={String(solvencyQuery.data.public.banm_total_supply_wei)} />
              <Kpi label="Pending Forward" value={String(solvencyQuery.data.public.pending_forward_mints_wei)} />
              <Kpi label="Pending Reverse" value={String(solvencyQuery.data.public.pending_reverse_releases_anm)} />
            </div>
          )}
          <div className="admin-row">
            <button onClick={() => pauseMutation.mutate({ flag: "bridge_paused", paused: true })}>Pause All</button>
            <button onClick={() => pauseMutation.mutate({ flag: "bridge_paused", paused: false })}>Unpause All</button>
            <button onClick={() => pauseMutation.mutate({ flag: "bridge_paused_forward", paused: true })}>Pause Forward</button>
            <button onClick={() => pauseMutation.mutate({ flag: "bridge_paused_reverse", paused: true })}>Pause Reverse</button>
          </div>
        </div>

        <div className="admin-panel">
          <h3>Orders</h3>
          <div className="admin-row">
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">All Statuses</option>
              {[
                "CREATED",
                "AWAITING_DEPOSIT",
                "DEPOSIT_SEEN",
                "CONFIRMING",
                "CONFIRMED",
                "READY_TO_SETTLE",
                "SETTLEMENT_SUBMITTED",
                "SETTLEMENT_CONFIRMED",
                "COMPLETED",
                "EXPIRED",
                "REJECTED",
                "FAILED",
                "MANUAL_REVIEW",
                "CANCELLED"
              ].map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
            <select value={directionFilter} onChange={(e) => setDirectionFilter(e.target.value)}>
              <option value="">All Directions</option>
              <option value="ANM_TO_BANM">ANM_TO_BANM</option>
              <option value="BANM_TO_ANM">BANM_TO_ANM</option>
            </select>
          </div>
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Order</th>
                  <th>Direction</th>
                  <th>Status</th>
                  <th>Amount In</th>
                  <th>Settlement Tx</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row: any) => (
                  <tr
                    key={row.order_id}
                    onClick={() => setSelectedOrderId(row.order_id)}
                    className={selectedOrderId === row.order_id ? "selected" : ""}
                  >
                    <td className="mono">{row.order_id}</td>
                    <td>{row.direction}</td>
                    <td>{row.status}</td>
                    <td className="mono">{row.amount_in}</td>
                    <td className="mono">{row.settlement_tx_hash || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="admin-panel">
          <h3>Order Detail</h3>
          {!selectedOrderId && <div>Select an order in the table.</div>}
          {orderDetailQuery.data?.order && (
            <>
              <div className="admin-kpi-grid">
                <Kpi label="Order ID" value={orderDetailQuery.data.order.order_id} />
                <Kpi label="Direction" value={orderDetailQuery.data.order.direction} />
                <Kpi label="Status" value={orderDetailQuery.data.order.status} />
                <Kpi
                  label="Confirmations"
                  value={`${orderDetailQuery.data.order.confirmation_count_current}/${orderDetailQuery.data.order.confirmation_count_required}`}
                />
              </div>
              <div className="admin-row">
                <button onClick={() => retryMutation.mutate(selectedOrderId)}>Retry Settlement Step</button>
                <input value={manualReason} onChange={(e) => setManualReason(e.target.value)} />
                <button onClick={() => manualMutation.mutate(selectedOrderId)}>Move To Manual Review</button>
              </div>
              <div className="admin-event-stream">
                {orderDetailQuery.data.events.map((event: any) => (
                  <div key={event.id} className="admin-event">
                    <div className="admin-row" style={{ justifyContent: "space-between" }}>
                      <strong>{event.to_status}</strong>
                      <span>{new Date(event.created_at).toLocaleString()}</span>
                    </div>
                    <div>{event.reason}</div>
                    <pre>{JSON.stringify(event.payload || {}, null, 2)}</pre>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="admin-kpi">
      <div>{label}</div>
      <strong className="mono">{value}</strong>
    </div>
  );
}

