import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, FileCheck, Info, Search, XCircle } from 'lucide-react';
import { apiClient, type KycCase } from '../services/api';
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
import { errorMessage, formatDateTime, shortId } from '../lib/format';

export default function KycPage() {
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('PENDING');
  const [page, setPage] = useState(1);
  const [selectedCase, setSelectedCase] = useState<KycCase | null>(null);
  const [notes, setNotes] = useState('');
  const [riskTier, setRiskTier] = useState('');

  const params = useMemo(
    () => ({
      page,
      limit: 25,
      query: query || undefined,
      status: status || undefined,
    }),
    [page, query, status]
  );

  const kycQuery = useQuery({
    queryKey: ['kyc', params],
    queryFn: () => apiClient.listKyc(params),
  });

  const reviewMutation = useMutation({
    mutationFn: ({
      id,
      action,
    }: {
      id: string;
      action: 'approve' | 'reject' | 'request_info';
    }) =>
      apiClient.reviewKyc(id, {
        action,
        notes: notes || undefined,
        riskTier: riskTier || undefined,
      }),
    onSuccess: async () => {
      setNotes('');
      setRiskTier('');
      setSelectedCase(null);
      await queryClient.invalidateQueries({ queryKey: ['kyc'] });
    },
  });

  const cases = kycQuery.data?.data.cases ?? [];
  const pagination = kycQuery.data?.data.pagination;
  const queueCounts = kycQuery.data?.data.queueCounts ?? [];

  return (
    <div className="space-y-6">
      <PageHeader title="KYC Review" description="Identity cases from the exchange user profile tables." />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {['PENDING', 'REVIEW', 'VERIFIED', 'REJECTED', 'NOT_STARTED'].map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => {
              setStatus(item);
              setPage(1);
            }}
            className={`rounded-lg border px-4 py-3 text-left text-sm ${
              status === item ? 'border-gray-950 bg-white' : 'border-gray-200 bg-white hover:bg-gray-50'
            }`}
          >
            <div className="font-medium text-gray-950">{item.replace(/_/g, ' ')}</div>
            <div className="mt-1 text-gray-500">
              {queueCounts.find((row) => row.status === item)?.count ?? 0}
            </div>
          </button>
        ))}
      </div>

      <Panel>
        <div className="grid gap-3 border-b border-gray-200 p-5 md:grid-cols-[1fr_180px_auto]">
          <label className="relative">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
            <input
              type="search"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
              placeholder="Email, user ID, or case ID"
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
            <option value="PENDING">Pending</option>
            <option value="REVIEW">Review</option>
            <option value="VERIFIED">Verified</option>
            <option value="REJECTED">Rejected</option>
            <option value="NOT_STARTED">Not started</option>
          </select>
          <Button type="button" variant="secondary" onClick={() => kycQuery.refetch()}>
            <Search className="h-4 w-4" />
            Search
          </Button>
        </div>

        {kycQuery.isLoading ? (
          <div className="p-5">
            <LoadingPanel label="Loading KYC queue" />
          </div>
        ) : kycQuery.isError ? (
          <div className="p-5">
            <ErrorPanel message={errorMessage(kycQuery.error, 'Failed to load KYC cases.')} />
          </div>
        ) : cases.length === 0 ? (
          <EmptyState title="No KYC cases found" />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-5 py-3">Applicant</th>
                  <th className="px-5 py-3">Provider</th>
                  <th className="px-5 py-3">Status</th>
                  <th className="px-5 py-3">Risk</th>
                  <th className="px-5 py-3">Submitted</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {cases.map((item) => (
                  <tr
                    key={item.id}
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => {
                      setSelectedCase(item);
                      setNotes(item.notes ?? '');
                      setRiskTier(item.riskTier ?? '');
                    }}
                  >
                    <td className="px-5 py-4">
                      <div className="font-medium text-gray-950">{item.user?.email ?? 'No email'}</div>
                      <div className="text-xs text-gray-500">{shortId(item.id)}</div>
                    </td>
                    <td className="px-5 py-4 text-gray-600">{item.provider}</td>
                    <td className="px-5 py-4">
                      <StatusBadge value={item.status} />
                    </td>
                    <td className="px-5 py-4">
                      <StatusBadge value={item.riskTier ?? 'UNSET'} />
                    </td>
                    <td className="px-5 py-4 text-gray-500">{formatDateTime(item.submittedAt ?? item.createdAt)}</td>
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

      {selectedCase && (
        <Panel>
          <PanelHeader title="Case Review" description={shortId(selectedCase.id)} />
          <div className="grid gap-6 p-5 xl:grid-cols-[1fr_360px]">
            <div className="space-y-5">
              <div className="grid gap-4 md:grid-cols-3">
                <InfoBlock label="Email" value={selectedCase.user?.email ?? 'No email'} />
                <InfoBlock label="Legal Name" value={selectedCase.user?.profile?.legalName ?? 'Not set'} />
                <InfoBlock label="Country" value={selectedCase.user?.profile?.country ?? 'Not set'} />
              </div>
              <div>
                <h3 className="mb-3 text-sm font-semibold text-gray-950">Documents</h3>
                {selectedCase.documents.length === 0 ? (
                  <EmptyState title="No documents recorded" />
                ) : (
                  <div className="space-y-3">
                    {selectedCase.documents.map((document) => (
                      <div key={document.id} className="rounded-lg border border-gray-200 p-4 text-sm">
                        <div className="flex items-center justify-between gap-3">
                          <span className="font-medium text-gray-950">{document.docType}</span>
                          <span className="text-xs text-gray-500">{formatDateTime(document.createdAt)}</span>
                        </div>
                        <div className="mt-2 text-gray-600">{document.storageRef}</div>
                        <div className="mt-1 font-mono text-xs text-gray-500">{shortId(document.sha256)}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {selectedCase.notes && <JsonBlock value={{ notes: selectedCase.notes }} />}
            </div>
            <div className="border-t border-gray-200 pt-5 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0">
              <h3 className="text-sm font-semibold text-gray-950">Decision</h3>
              <div className="mt-4 space-y-3">
                <select
                  value={riskTier}
                  onChange={(event) => setRiskTier(event.target.value)}
                  className="h-9 w-full rounded-md border border-gray-300 px-3 text-sm focus:border-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-200"
                >
                  <option value="">Risk tier</option>
                  <option value="LOW">Low</option>
                  <option value="MEDIUM">Medium</option>
                  <option value="HIGH">High</option>
                </select>
                <textarea
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                  rows={5}
                  placeholder="Review notes"
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-200"
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    disabled={!hasPermission('kyc:review') || reviewMutation.isPending}
                    onClick={() => reviewMutation.mutate({ id: selectedCase.id, action: 'approve' })}
                  >
                    <CheckCircle2 className="h-4 w-4" />
                    Approve
                  </Button>
                  <Button
                    type="button"
                    variant="danger"
                    disabled={!hasPermission('kyc:review') || reviewMutation.isPending}
                    onClick={() => reviewMutation.mutate({ id: selectedCase.id, action: 'reject' })}
                  >
                    <XCircle className="h-4 w-4" />
                    Reject
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={!hasPermission('kyc:review') || reviewMutation.isPending}
                    onClick={() => reviewMutation.mutate({ id: selectedCase.id, action: 'request_info' })}
                  >
                    <FileCheck className="h-4 w-4" />
                    Request Info
                  </Button>
                </div>
                {reviewMutation.isError && (
                  <ErrorPanel message={errorMessage(reviewMutation.error, 'KYC review failed.')} />
                )}
              </div>
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}

function InfoBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-gray-500">
        <Info className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="mt-1 break-words text-sm font-medium text-gray-950">{value}</div>
    </div>
  );
}
