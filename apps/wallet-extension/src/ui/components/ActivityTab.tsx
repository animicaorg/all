import React from 'react';
import type { PendingTx } from '../../types/tx';

interface ActivityTabProps {
  pendingTxs: PendingTx[];
}

function ActivityTab({ pendingTxs }: ActivityTabProps) {
  function formatTimestamp(timestamp: number): string {
    const date = new Date(timestamp);
    const now = Date.now();
    const diff = now - timestamp;
    
    if (diff < 60000) {
      return 'Just now';
    } else if (diff < 3600000) {
      return `${Math.floor(diff / 60000)}m ago`;
    } else if (diff < 86400000) {
      return `${Math.floor(diff / 3600000)}h ago`;
    } else {
      return date.toLocaleDateString();
    }
  }

  function getStatusColor(status: string): string {
    switch (status) {
      case 'confirmed':
        return '#059669';
      case 'included':
      case 'mempool_accepted':
        return '#f59e0b';
      case 'dropped':
      case 'reorged_out':
        return '#dc2626';
      default:
        return '#6b7280';
    }
  }

  function formatAmount(tx: PendingTx): string {
    const payload = tx.signedTx.tx.payload.v as any;
    if (payload.amount !== undefined) {
      const anm = Number(payload.amount) / 1e9;
      return `${anm.toFixed(4)} ANM`;
    }
    return 'N/A';
  }

  return (
    <div>
      <h3 style={{ marginTop: 0, marginBottom: '12px', fontSize: '16px' }}>Recent Activity</h3>
      
      {pendingTxs.length === 0 ? (
        <div style={{ textAlign: 'center', color: '#999', padding: '32px' }}>
          No transactions yet
        </div>
      ) : (
        <div>
          {pendingTxs.map((tx) => (
            <div key={tx.txid} className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                    <span style={{ fontSize: '18px' }}>📤</span>
                    <span style={{ fontWeight: 600 }}>{formatAmount(tx)}</span>
                  </div>
                  <div className="address" style={{ marginBottom: '8px' }}>
                    {tx.txid.slice(0, 32)}...
                  </div>
                  <div style={{ fontSize: '11px', color: '#999' }}>
                    {formatTimestamp(tx.submittedAt)}
                  </div>
                </div>
                <div>
                  <span
                    className="tx-status"
                    style={{ background: getStatusColor(tx.status) + '20', color: getStatusColor(tx.status) }}
                  >
                    {tx.status}
                  </span>
                  {tx.confirmations !== undefined && (
                    <div style={{ fontSize: '11px', color: '#999', marginTop: '4px', textAlign: 'right' }}>
                      {tx.confirmations} conf
                    </div>
                  )}
                </div>
              </div>
              
              {tx.error && (
                <div style={{ marginTop: '8px', padding: '8px', background: '#fee', color: '#c33', borderRadius: '4px', fontSize: '12px' }}>
                  Error: {tx.error}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div style={{ marginTop: '16px', padding: '12px', background: '#e7f3ff', borderRadius: '8px', fontSize: '12px', color: '#0066cc' }}>
        <strong>ℹ️ Info:</strong> Transactions are polled automatically. Status updates may take a few seconds.
      </div>
    </div>
  );
}

export default ActivityTab;
