import { useEffect, useState } from 'react';
import { useSession } from '../lib/session';
import { usdanApi } from '../lib/api';
import { formatDate } from '../lib/format';

export function TransactionsPage() {
  const { session } = useSession();
  const [items, setItems] = useState<any[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!session) return;
    usdanApi
      .getTransactions(session)
      .then((res) => setItems(res.items))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load transactions'));
  }, [session]);

  return (
    <section className="page">
      <h2>Transactions</h2>
      {!session ? <p className="warning">Create a wallet session to view transaction history.</p> : null}
      {error ? <p className="error">{error}</p> : null}

      <div className="table">
        {items.map((item) => (
          <div key={`${item.type}-${item.id}`} className="row">
            <span>{item.type}</span>
            <span>{item.id}</span>
            <span>{item.status}</span>
            <span>{item.amount}</span>
            <span>{formatDate(item.createdAt)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
