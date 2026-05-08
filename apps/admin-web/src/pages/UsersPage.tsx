/**
 * Users Page
 * User search, balances, KYC/risk context, and freeze controls.
 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Lock, Search, Unlock } from 'lucide-react';
import { apiClient, type UserSummary } from '../services/api';
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

export default function UsersPage() {
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [freezeReason, setFreezeReason] = useState('');

  const params = useMemo(
    () => ({
      page,
      limit: 25,
      query: query || undefined,
      status: status || undefined,
    }),
    [page, query, status]
  );

  const usersQuery = useQuery({
    queryKey: ['users', params],
    queryFn: () => apiClient.listUsers(params),
  });

  const detailQuery = useQuery({
    queryKey: ['user', selectedId],
    queryFn: () => apiClient.getUser(selectedId!),
    enabled: Boolean(selectedId),
  });

  const freezeMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => apiClient.freezeUser(id, reason),
    onSuccess: async () => {
      setFreezeReason('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['users'] }),
        queryClient.invalidateQueries({ queryKey: ['user', selectedId] }),
      ]);
    },
  });

  const unfreezeMutation = useMutation({
    mutationFn: (id: string) => apiClient.unfreezeUser(id),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['users'] }),
        queryClient.invalidateQueries({ queryKey: ['user', selectedId] }),
      ]);
    },
  });

  const users = usersQuery.data?.data.users ?? [];
  const pagination = usersQuery.data?.data.pagination;
  const detailData = detailQuery.data?.data;
  const selectedUser = detailData?.user;

  return (
    <div className="space-y-6">
      <PageHeader title="Users" description="Account status, balances, risk flags, and support controls." />

      <Panel>
        <div className="grid gap-3 border-b border-gray-200 p-5 lg:grid-cols-[1fr_180px_auto]">
          <label className="relative">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
            <input
              type="search"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
              placeholder="Email or user ID"
              className="h-9 w-full rounded-md border border-gray-300 pl-9 pr-3 text-sm focus:border-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-200"
            />
          </label>
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(1);
            }}
            className="h-9 rounded-md border border-gray-300 px-3 text-sm focus:border-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-200"
          >
            <option value="">All statuses</option>
            <option value="ACTIVE">Active</option>
            <option value="SUSPENDED">Suspended</option>
            <option value="CLOSED">Closed</option>
          </select>
          <Button type="button" variant="secondary" onClick={() => usersQuery.refetch()}>
            <Search className="h-4 w-4" />
            Search
          </Button>
        </div>

        {usersQuery.isLoading ? (
          <div className="p-5">
            <LoadingPanel label="Loading users" />
          </div>
        ) : usersQuery.isError ? (
          <div className="p-5">
            <ErrorPanel message={errorMessage(usersQuery.error, 'Failed to load users.')} />
          </div>
        ) : users.length === 0 ? (
          <EmptyState title="No users found" />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-5 py-3">User</th>
                  <th className="px-5 py-3">Status</th>
                  <th className="px-5 py-3">Role</th>
                  <th className="px-5 py-3">Balances</th>
                  <th className="px-5 py-3">2FA</th>
                  <th className="px-5 py-3">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {users.map((user) => (
                  <tr
                    key={user.id}
                    onClick={() => setSelectedId(user.id)}
                    className={selectedId === user.id ? 'bg-gray-50' : 'cursor-pointer hover:bg-gray-50'}
                  >
                    <td className="px-5 py-4">
                      <div className="font-medium text-gray-950">{user.email ?? 'No email'}</div>
                      <div className="text-xs text-gray-500">{shortId(user.id)}</div>
                    </td>
                    <td className="px-5 py-4">
                      <StatusBadge value={user.status} />
                    </td>
                    <td className="px-5 py-4 text-gray-600">{user.role}</td>
                    <td className="px-5 py-4">
                      <BalanceTotals balances={user.balanceTotals ?? []} />
                    </td>
                    <td className="px-5 py-4">
                      <StatusBadge value={user.twofaEnabled} />
                    </td>
                    <td className="px-5 py-4 text-gray-500">{formatDateTime(user.createdAt)}</td>
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

      {selectedId && (
        <Panel>
          <PanelHeader title="Selected Account" description={shortId(selectedId)} />
          <div className="p-5">
            {detailQuery.isLoading && <LoadingPanel label="Loading account details" />}
            {detailQuery.isError && (
              <ErrorPanel message={errorMessage(detailQuery.error, 'Failed to load account details.')} />
            )}
            {detailData && selectedUser && (
              <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
                <div className="space-y-6">
                  <AccountSummary
                    user={selectedUser}
                    recentOrders={detailData.stats.recentOrders}
                  />
                  <Balances balances={detailData.balances} />
                  <RiskFlags flags={selectedUser.riskFlags} />
                </div>
                <div className="space-y-4">
                  <div className="border-t border-gray-200 pt-5 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0">
                    <h3 className="text-sm font-semibold text-gray-950">Controls</h3>
                    <div className="mt-4 space-y-3">
                      {selectedUser.status === 'SUSPENDED' ? (
                        <Button
                          type="button"
                          variant="secondary"
                          disabled={!hasPermission('users:freeze') || unfreezeMutation.isPending}
                          onClick={() => unfreezeMutation.mutate(selectedUser.id)}
                        >
                          <Unlock className="h-4 w-4" />
                          Unfreeze Account
                        </Button>
                      ) : (
                        <>
                          <textarea
                            value={freezeReason}
                            onChange={(event) => setFreezeReason(event.target.value)}
                            rows={4}
                            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-200"
                            placeholder="Freeze reason"
                          />
                          <Button
                            type="button"
                            variant="danger"
                            disabled={
                              !hasPermission('users:freeze') ||
                              freezeReason.trim().length < 10 ||
                              freezeMutation.isPending
                            }
                            onClick={() => freezeMutation.mutate({ id: selectedUser.id, reason: freezeReason })}
                          >
                            <Lock className="h-4 w-4" />
                            Freeze Account
                          </Button>
                        </>
                      )}
                      {(freezeMutation.isError || unfreezeMutation.isError) && (
                        <ErrorPanel
                          message={errorMessage(
                            freezeMutation.error ?? unfreezeMutation.error,
                            'Account action failed.'
                          )}
                        />
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </Panel>
      )}
    </div>
  );
}

function AccountSummary({
  user,
  recentOrders,
}: {
  user: UserSummary & { profile?: { legalName?: string | null; country?: string | null } | null };
  recentOrders: number;
}) {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <Info label="Email" value={user.email ?? 'No email'} />
      <Info label="Legal Name" value={user.profile?.legalName ?? 'Not set'} />
      <Info label="Country" value={user.profile?.country ?? 'Not set'} />
      <Info label="Recent Orders" value={String(recentOrders)} />
    </div>
  );
}

function BalanceTotals({ balances }: { balances: Array<{ asset: string; total: string }> }) {
  if (balances.length === 0) {
    return <span className="text-xs text-gray-400">No balance</span>;
  }

  const visibleBalances = balances.slice(0, 4);
  const remainingCount = balances.length - visibleBalances.length;

  return (
    <div className="flex max-w-md flex-wrap gap-1.5">
      {visibleBalances.map((balance) => (
        <span
          key={balance.asset}
          className="whitespace-nowrap rounded border border-gray-200 bg-gray-50 px-2 py-1 text-xs text-gray-700"
        >
          <span className="font-medium text-gray-950">{balance.asset}</span>{' '}
          {formatDecimal(balance.total)}
        </span>
      ))}
      {remainingCount > 0 && (
        <span className="whitespace-nowrap rounded border border-gray-200 px-2 py-1 text-xs text-gray-500">
          +{remainingCount} more
        </span>
      )}
    </div>
  );
}

function Balances({ balances }: { balances: Array<{ asset: string; available: string; locked: string; total: string }> }) {
  if (balances.length === 0) {
    return <EmptyState title="No available balances" />;
  }
  return (
    <div className="overflow-hidden rounded-lg border border-gray-200">
      <table className="min-w-full divide-y divide-gray-200 text-sm">
        <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th className="px-4 py-3">Asset</th>
            <th className="px-4 py-3">Available</th>
            <th className="px-4 py-3">Locked</th>
            <th className="px-4 py-3">Total</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {balances.map((balance) => (
            <tr key={balance.asset}>
              <td className="px-4 py-3 font-medium text-gray-950">{balance.asset}</td>
              <td className="px-4 py-3 text-gray-700">{formatDecimal(balance.available)}</td>
              <td className="px-4 py-3 text-gray-700">{formatDecimal(balance.locked)}</td>
              <td className="px-4 py-3 text-gray-950">{formatDecimal(balance.total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RiskFlags({ flags }: { flags: Array<{ id: string; code: string; severity: string; note: string | null; createdAt: string }> }) {
  if (flags.length === 0) {
    return <EmptyState title="No open risk flags" />;
  }
  return (
    <div className="space-y-3">
      {flags.map((flag) => (
        <div key={flag.id} className="rounded-lg border border-gray-200 p-4 text-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="font-medium text-gray-950">{flag.code}</span>
            <StatusBadge value={flag.severity} />
          </div>
          {flag.note && <p className="mt-2 text-gray-600">{flag.note}</p>}
          <p className="mt-2 text-xs text-gray-500">{formatDateTime(flag.createdAt)}</p>
        </div>
      ))}
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
