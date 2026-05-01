import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, RefreshCw, Search, XCircle } from 'lucide-react';
import { apiClient, type Withdrawal } from '../services/api';
import {
  Button,
  EmptyState,
  ErrorPanel,
  LoadingPanel,
  PageHeader,
  PaginationControls,
  Panel,
  PanelHeader,
  StatusBadge,
} from '../components/AdminUI';
import { useAuth } from '../contexts/AuthContext';
import { errorMessage, formatDateTime, formatDecimal, shortId } from '../lib/format';

export default function WithdrawalsPage() {
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('REQUESTED');
  const [page, setPage] = useState(1);
  const [selectedWithdrawal, setSelectedWithdrawal] = useState<Withdrawal | null>(null);
  const [note, setNote] = useState('');

  const params = useMemo(
    () => ({
      page,
      limit: 25,
      query: query || undefined,
      status: status || undefined,
    }),
    [page, query, status]
  );

  const withdrawalsQuery = useQuery({
    queryKey: ['withdrawals', params],
    queryFn: () => apiClient.listWithdrawals(params),
  });

  const invalidateWithdrawals = async () => {
    await queryClient.invalidateQueries({ queryKey: ['withdrawals'] });
  };

  const approveMutation = useMutation({
    mutationFn: (id: string) => apiClient.approveWithdrawal(id, note || undefined),
    onSuccess: async () => {
      setSelectedWithdrawal(null);
      setNote('');
      await invalidateWithdrawals();
    },
  });

  const rejectMutation = useMutation({
    mutationFn: (id: string) => apiClient.rejectWithdrawal(id, note),
    onSuccess: async () => {
      setSelectedWithdrawal(null);
      setNote('');
      await invalidateWithdrawals();
    },
  });

  const retryMutation = useMutation({
    mutationFn: (id: string) => apiClient.retryWithdrawal(id, note || undefined),
    onSuccess: async () => {
      setSelectedWithdrawal(null);
      setNote('');
      await invalidateWithdrawals();
    },
  });

  const withdrawals = withdrawalsQuery.data?.data.withdrawals ?? [];
  const pagination = withdrawalsQuery.data?.data.pagination;
  const counts = withdrawalsQuery.data?.data.statusCounts ?? [];

  return (
    <div className="space-y-6">
      <PageHeader title="Withdrawals" description="Risk review, approval, rejection, and retry queue." />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        {['REQUESTED', 'RISK_REVIEW', 'APPROVED', 'SIGNING', 'BROADCAST', 'CONFIRMED', 'FAILED', 'CANCELED'].map(
          (item) => (
            <button
              key={item}
              type="button"
              onClick={() => {
                setStatus(item);
                setPage(1);
              }}
              className={`rounded-lg border px-3 py-3 text-left text-xs ${
                status === item ? 'border-gray-950 bg-white' : 'border-gray-200 bg-white hover:bg-gray-50'
              }`}
            >
              <div className="font-medium text-gray-950">{item.replace(/_/g, ' ')}</div>
              <div className="mt-1 text-gray-500">{counts.find((row) => row.status === item)?.count ?? 0}</div>
            </button>
          )
        )}
      </div>

      <Panel>
        <div className="grid gap-3 border-b border-gray-200 p-5 md:grid-cols-[1fr_200px_auto]">
          <label className="relative">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
            <input
              type="search"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
              placeholder="Email, address, txid, or withdrawal ID"
              className="h-9 w-full rounded-md border border-gray-300 pl-9 pr-3 text-sm focus:border-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-200"
            />
          </label>
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(1);
            }}
            className="field-input"
          >
            <option value="">All statuses</option>
            <option value="REQUESTED">Requested</option>
            <option value="RISK_REVIEW">Risk review</option>
            <option value="APPROVED">Approved</option>
            <option value="SIGNING">Signing</option>
            <option value="BROADCAST">Broadcast</option>
            <option value="CONFIRMED">Confirmed</option>
            <option value="FAILED">Failed</option>
            <option value="CANCELED">Canceled</option>
          </select>
          <Button type="button" variant="secondary" onClick={() => withdrawalsQuery.refetch()}>
            <Search className="h-4 w-4" />
            Search
          </Button>
        </div>

        {withdrawalsQuery.isLoading ? (
          <div className="p-5">
            <LoadingPanel label="Loading withdrawals" />
          </div>
        ) : withdrawalsQuery.isError ? (
          <div className="p-5">
            <ErrorPanel message={errorMessage(withdrawalsQuery.error, 'Failed to load withdrawals.')} />
          </div>
        ) : withdrawals.length === 0 ? (
          <EmptyState title="No withdrawals found" />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-5 py-3">Request</th>
                  <th className="px-5 py-3">User</th>
                  <th className="px-5 py-3">Asset</th>
                  <th className="px-5 py-3">Amount</th>
                  <th className="px-5 py-3">Destination</th>
                  <th className="px-5 py-3">Status</th>
                  <th className="px-5 py-3">Requested</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {withdrawals.map((withdrawal) => (
                  <tr
                    key={withdrawal.id}
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => {
                      setSelectedWithdrawal(withdrawal);
                      setNote('');
                    }}
                  >
                    <td className="px-5 py-4 font-mono text-xs text-gray-600">{shortId(withdrawal.id)}</td>
                    <td className="px-5 py-4">
                      <div className="font-medium text-gray-950">{withdrawal.user.email ?? 'No email'}</div>
                      <div className="text-xs text-gray-500">{shortId(withdrawal.userId)}</div>
                    </td>
                    <td className="px-5 py-4 text-gray-700">
                      {withdrawal.assetNetwork.asset.symbol} / {withdrawal.assetNetwork.network.code}
                    </td>
                    <td className="px-5 py-4 text-gray-700">
                      {formatDecimal(withdrawal.amount)} fee {formatDecimal(withdrawal.feeAmount)}
                    </td>
                    <td className="px-5 py-4 font-mono text-xs text-gray-600">{shortId(withdrawal.destinationAddress)}</td>
                    <td className="px-5 py-4">
                      <StatusBadge value={withdrawal.status} />
                    </td>
                    <td className="px-5 py-4 text-gray-500">{formatDateTime(withdrawal.requestedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pagination && (
          <PaginationControls page={pagination.page} totalPages={pagination.totalPages} onPageChange={setPage} />
        )}
      </Panel>

      {selectedWithdrawal && (
        <Panel>
          <PanelHeader title="Withdrawal Action" description={shortId(selectedWithdrawal.id)} />
          <div className="grid gap-6 p-5 xl:grid-cols-[1fr_360px]">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <Info label="Provider" value={selectedWithdrawal.provider} />
              <Info label="Risk Score" value={selectedWithdrawal.riskScore ?? 'None'} />
              <Info label="TxID" value={selectedWithdrawal.txid ? shortId(selectedWithdrawal.txid) : 'None'} />
              <Info label="Approvals" value={String(selectedWithdrawal.approvals.length)} />
              <Info label="Destination" value={selectedWithdrawal.destinationAddress} />
              <Info label="Provider Ref" value={selectedWithdrawal.providerRef ?? 'None'} />
              <Info label="Broadcast" value={formatDateTime(selectedWithdrawal.broadcastAt)} />
              <Info label="Confirmed" value={formatDateTime(selectedWithdrawal.confirmedAt)} />
            </div>
            <div className="border-t border-gray-200 pt-5 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0">
              <h3 className="text-sm font-semibold text-gray-950">Decision</h3>
              <textarea
                value={note}
                onChange={(event) => setNote(event.target.value)}
                rows={4}
                placeholder="Action note"
                className="mt-4 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-200"
              />
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  type="button"
                  disabled={
                    !hasPermission('withdrawals:approve') ||
                    approveMutation.isPending ||
                    !['REQUESTED', 'RISK_REVIEW'].includes(selectedWithdrawal.status)
                  }
                  onClick={() => approveMutation.mutate(selectedWithdrawal.id)}
                >
                  <CheckCircle2 className="h-4 w-4" />
                  Approve
                </Button>
                <Button
                  type="button"
                  variant="danger"
                  disabled={
                    !hasPermission('withdrawals:approve') ||
                    rejectMutation.isPending ||
                    note.trim().length < 3 ||
                    !['REQUESTED', 'RISK_REVIEW', 'APPROVED'].includes(selectedWithdrawal.status)
                  }
                  onClick={() => rejectMutation.mutate(selectedWithdrawal.id)}
                >
                  <XCircle className="h-4 w-4" />
                  Reject
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={
                    !hasPermission('withdrawals:sign') ||
                    retryMutation.isPending ||
                    selectedWithdrawal.status !== 'FAILED'
                  }
                  onClick={() => retryMutation.mutate(selectedWithdrawal.id)}
                >
                  <RefreshCw className="h-4 w-4" />
                  Retry
                </Button>
              </div>
              {(approveMutation.isError || rejectMutation.isError || retryMutation.isError) && (
                <div className="mt-3">
                  <ErrorPanel
                    message={errorMessage(
                      approveMutation.error ?? rejectMutation.error ?? retryMutation.error,
                      'Withdrawal action failed.'
                    )}
                  />
                </div>
              )}
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-gray-200 p-4">
      <p className="text-xs uppercase tracking-wide text-gray-500">{label}</p>
      <p className="mt-1 break-words text-sm font-medium text-gray-950">{value}</p>
    </div>
  );
}
