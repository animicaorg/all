import { useMemo, useState } from 'react';
import { type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, Pencil, Plus, Search } from 'lucide-react';
import { apiClient, type FeeSchedule } from '../services/api';
import {
  Button,
  EmptyState,
  ErrorPanel,
  JsonBlock,
  LoadingPanel,
  PageHeader,
  PaginationControls,
  Panel,
  PanelHeader,
  StatusBadge,
} from '../components/AdminUI';
import { useAuth } from '../contexts/AuthContext';
import { errorMessage, formatDate, formatDateTime } from '../lib/format';

const defaultForm = {
  scope: 'GLOBAL' as FeeSchedule['scope'],
  name: '',
  marketId: '',
  makerBps: '10',
  takerBps: '20',
  withdrawalFeeOverride: '',
  rulesJson: '',
  status: 'active',
  effectiveFrom: new Date().toISOString().slice(0, 10),
  effectiveTo: '',
};

export default function FeesPage() {
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const [scope, setScope] = useState('');
  const [status, setStatus] = useState('active');
  const [page, setPage] = useState(1);
  const [editingFee, setEditingFee] = useState<FeeSchedule | null>(null);
  const [form, setForm] = useState(defaultForm);
  const [formError, setFormError] = useState<string | null>(null);

  const params = useMemo(
    () => ({
      page,
      limit: 25,
      scope: scope || undefined,
      status: status || undefined,
    }),
    [page, scope, status]
  );

  const feesQuery = useQuery({
    queryKey: ['fees', params],
    queryFn: () => apiClient.listFees(params),
  });

  const invalidateFees = async () => {
    await queryClient.invalidateQueries({ queryKey: ['fees'] });
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      setFormError(null);
      let rulesJson: unknown = null;
      if (form.rulesJson.trim()) {
        try {
          rulesJson = JSON.parse(form.rulesJson);
        } catch {
          setFormError('Rules JSON is invalid.');
          throw new Error('Rules JSON is invalid.');
        }
      }

      const payload = {
        scope: form.scope,
        name: form.name || null,
        marketId: form.scope === 'MARKET' ? form.marketId || null : null,
        makerBps: Number(form.makerBps),
        takerBps: Number(form.takerBps),
        withdrawalFeeOverride: form.withdrawalFeeOverride || null,
        rulesJson,
        status: form.status,
        effectiveFrom: form.effectiveFrom,
        effectiveTo: form.effectiveTo || null,
      };

      return editingFee ? apiClient.updateFee(editingFee.id, payload) : apiClient.createFee(payload);
    },
    onSuccess: async () => {
      setEditingFee(null);
      setForm(defaultForm);
      await invalidateFees();
    },
  });

  const archiveMutation = useMutation({
    mutationFn: (id: string) => apiClient.archiveFee(id),
    onSuccess: invalidateFees,
  });

  const fees = feesQuery.data?.data.fees ?? [];
  const markets = feesQuery.data?.data.markets ?? [];
  const pagination = feesQuery.data?.data.pagination;

  const startEdit = (fee: FeeSchedule) => {
    setEditingFee(fee);
    setForm({
      scope: fee.scope,
      name: fee.name ?? '',
      marketId: fee.marketId ?? '',
      makerBps: String(fee.makerBps),
      takerBps: String(fee.takerBps),
      withdrawalFeeOverride: fee.withdrawalFeeOverride ?? '',
      rulesJson: fee.rulesJson ? JSON.stringify(fee.rulesJson, null, 2) : '',
      status: fee.status,
      effectiveFrom: fee.effectiveFrom.slice(0, 10),
      effectiveTo: fee.effectiveTo ? fee.effectiveTo.slice(0, 10) : '',
    });
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Fees" description="Maker, taker, withdrawal override, and effective date schedules." />

      <Panel>
        <div className="grid gap-3 border-b border-gray-200 p-5 md:grid-cols-[180px_180px_auto]">
          <select
            value={scope}
            onChange={(event) => {
              setScope(event.target.value);
              setPage(1);
            }}
            className="h-9 rounded-md border border-gray-300 px-3 text-sm focus:border-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-200"
          >
            <option value="">All scopes</option>
            <option value="GLOBAL">Global</option>
            <option value="MARKET">Market</option>
            <option value="USER_TIER">User tier</option>
          </select>
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(1);
            }}
            className="h-9 rounded-md border border-gray-300 px-3 text-sm focus:border-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-200"
          >
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="archived">Archived</option>
          </select>
          <Button type="button" variant="secondary" onClick={() => feesQuery.refetch()}>
            <Search className="h-4 w-4" />
            Refresh
          </Button>
        </div>

        {feesQuery.isLoading ? (
          <div className="p-5">
            <LoadingPanel label="Loading fees" />
          </div>
        ) : feesQuery.isError ? (
          <div className="p-5">
            <ErrorPanel message={errorMessage(feesQuery.error, 'Failed to load fee schedules.')} />
          </div>
        ) : fees.length === 0 ? (
          <EmptyState title="No fee schedules found" />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-5 py-3">Schedule</th>
                  <th className="px-5 py-3">Scope</th>
                  <th className="px-5 py-3">Maker</th>
                  <th className="px-5 py-3">Taker</th>
                  <th className="px-5 py-3">Effective</th>
                  <th className="px-5 py-3">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {fees.map((fee) => (
                  <tr key={fee.id}>
                    <td className="px-5 py-4">
                      <div className="font-medium text-gray-950">{fee.name ?? fee.market?.symbol ?? fee.scope}</div>
                      <div className="text-xs text-gray-500">Updated {formatDateTime(fee.updatedAt)}</div>
                    </td>
                    <td className="px-5 py-4">
                      <StatusBadge value={fee.scope} />
                    </td>
                    <td className="px-5 py-4 text-gray-700">{fee.makerBps} bps</td>
                    <td className="px-5 py-4 text-gray-700">{fee.takerBps} bps</td>
                    <td className="px-5 py-4 text-gray-700">
                      {formatDate(fee.effectiveFrom)} to {formatDate(fee.effectiveTo)}
                    </td>
                    <td className="px-5 py-4">
                      <div className="flex flex-wrap gap-2">
                        <Button type="button" variant="secondary" onClick={() => startEdit(fee)}>
                          <Pencil className="h-4 w-4" />
                          Edit
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          disabled={!hasPermission('fees:write') || fee.status === 'archived' || archiveMutation.isPending}
                          onClick={() => archiveMutation.mutate(fee.id)}
                        >
                          <Archive className="h-4 w-4" />
                          Archive
                        </Button>
                      </div>
                    </td>
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

      <Panel>
        <PanelHeader title={editingFee ? 'Edit Fee Schedule' : 'Create Fee Schedule'} />
        <form
          className="space-y-5 p-5"
          onSubmit={(event) => {
            event.preventDefault();
            saveMutation.mutate();
          }}
        >
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Field label="Scope">
              <select
                value={form.scope}
                onChange={(event) => setForm((prev) => ({ ...prev, scope: event.target.value as FeeSchedule['scope'] }))}
                className="field-input"
              >
                <option value="GLOBAL">Global</option>
                <option value="MARKET">Market</option>
              </select>
            </Field>
            <Field label="Name">
              <input
                value={form.name}
                onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                className="field-input"
              />
            </Field>
            <Field label="Market">
              <select
                value={form.marketId}
                onChange={(event) => setForm((prev) => ({ ...prev, marketId: event.target.value }))}
                disabled={form.scope !== 'MARKET'}
                className="field-input"
              >
                <option value="">Select market</option>
                {markets.map((market) => (
                  <option key={market.id} value={market.id}>
                    {market.symbol}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Status">
              <input
                value={form.status}
                onChange={(event) => setForm((prev) => ({ ...prev, status: event.target.value }))}
                className="field-input"
              />
            </Field>
            <Field label="Maker bps">
              <input
                type="number"
                min="0"
                max="10000"
                value={form.makerBps}
                onChange={(event) => setForm((prev) => ({ ...prev, makerBps: event.target.value }))}
                className="field-input"
              />
            </Field>
            <Field label="Taker bps">
              <input
                type="number"
                min="0"
                max="10000"
                value={form.takerBps}
                onChange={(event) => setForm((prev) => ({ ...prev, takerBps: event.target.value }))}
                className="field-input"
              />
            </Field>
            <Field label="Withdrawal override">
              <input
                value={form.withdrawalFeeOverride}
                onChange={(event) => setForm((prev) => ({ ...prev, withdrawalFeeOverride: event.target.value }))}
                className="field-input"
              />
            </Field>
            <Field label="Effective from">
              <input
                type="date"
                value={form.effectiveFrom}
                onChange={(event) => setForm((prev) => ({ ...prev, effectiveFrom: event.target.value }))}
                className="field-input"
              />
            </Field>
            <Field label="Effective to">
              <input
                type="date"
                value={form.effectiveTo}
                onChange={(event) => setForm((prev) => ({ ...prev, effectiveTo: event.target.value }))}
                className="field-input"
              />
            </Field>
          </div>
          <Field label="Rules JSON">
            <textarea
              value={form.rulesJson}
              onChange={(event) => setForm((prev) => ({ ...prev, rulesJson: event.target.value }))}
              rows={5}
              className="field-input font-mono text-xs"
            />
          </Field>
          {form.rulesJson.trim() && <JsonBlock value={safeJson(form.rulesJson)} />}
          {(formError || saveMutation.isError || archiveMutation.isError) && (
            <ErrorPanel
              message={
                formError ??
                errorMessage(saveMutation.error ?? archiveMutation.error, 'Fee schedule action failed.')
              }
            />
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={!hasPermission('fees:write') || saveMutation.isPending}>
              <Plus className="h-4 w-4" />
              {editingFee ? 'Save Changes' : 'Create Schedule'}
            </Button>
            {editingFee && (
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setEditingFee(null);
                  setForm(defaultForm);
                }}
              >
                Cancel
              </Button>
            )}
          </div>
        </form>
      </Panel>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-gray-700">{label}</span>
      {children}
    </label>
  );
}

function safeJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return { invalid: true };
  }
}
