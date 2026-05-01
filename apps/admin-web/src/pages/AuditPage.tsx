import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { apiClient, type AuditLog } from '../services/api';
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
import { errorMessage, formatDateTime, shortId } from '../lib/format';

export default function AuditPage() {
  const [actor, setActor] = useState('');
  const [action, setAction] = useState('');
  const [entityType, setEntityType] = useState('');
  const [page, setPage] = useState(1);
  const [selectedLog, setSelectedLog] = useState<AuditLog | null>(null);

  const params = useMemo(
    () => ({
      page,
      limit: 50,
      actor: actor || undefined,
      action: action || undefined,
      entityType: entityType || undefined,
    }),
    [page, actor, action, entityType]
  );

  const auditQuery = useQuery({
    queryKey: ['audit', params],
    queryFn: () => apiClient.listAudit(params),
  });

  const logs = auditQuery.data?.data.logs ?? [];
  const pagination = auditQuery.data?.data.pagination;

  return (
    <div className="space-y-6">
      <PageHeader title="Audit Log" description="Immutable admin and system action history." />

      <Panel>
        <div className="grid gap-3 border-b border-gray-200 p-5 md:grid-cols-[1fr_220px_180px_auto]">
          <label className="relative">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
            <input
              type="search"
              value={actor}
              onChange={(event) => {
                setActor(event.target.value);
                setPage(1);
              }}
              placeholder="Actor email or ID"
              className="h-9 w-full rounded-md border border-gray-300 pl-9 pr-3 text-sm focus:border-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-200"
            />
          </label>
          <input
            value={action}
            onChange={(event) => {
              setAction(event.target.value);
              setPage(1);
            }}
            placeholder="Action"
            className="field-input"
          />
          <input
            value={entityType}
            onChange={(event) => {
              setEntityType(event.target.value);
              setPage(1);
            }}
            placeholder="Entity"
            className="field-input"
          />
          <Button type="button" variant="secondary" onClick={() => auditQuery.refetch()}>
            <Search className="h-4 w-4" />
            Search
          </Button>
        </div>

        {auditQuery.isLoading ? (
          <div className="p-5">
            <LoadingPanel label="Loading audit log" />
          </div>
        ) : auditQuery.isError ? (
          <div className="p-5">
            <ErrorPanel message={errorMessage(auditQuery.error, 'Failed to load audit log.')} />
          </div>
        ) : logs.length === 0 ? (
          <EmptyState title="No audit logs found" />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-5 py-3">Time</th>
                  <th className="px-5 py-3">Actor</th>
                  <th className="px-5 py-3">Action</th>
                  <th className="px-5 py-3">Entity</th>
                  <th className="px-5 py-3">Request</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {logs.map((log) => (
                  <tr key={log.id} className="cursor-pointer hover:bg-gray-50" onClick={() => setSelectedLog(log)}>
                    <td className="px-5 py-4 text-gray-500">{formatDateTime(log.createdAt)}</td>
                    <td className="px-5 py-4">
                      <div className="font-medium text-gray-950">
                        {log.actorAdmin?.email ?? log.actor?.email ?? log.actorType}
                      </div>
                      <div className="text-xs text-gray-500">{log.ip ?? 'No IP'}</div>
                    </td>
                    <td className="px-5 py-4">
                      <StatusBadge value={log.action} />
                    </td>
                    <td className="px-5 py-4 text-gray-700">
                      {log.entityType} {shortId(log.entityId)}
                    </td>
                    <td className="px-5 py-4 font-mono text-xs text-gray-500">{shortId(log.requestId)}</td>
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

      {selectedLog && (
        <Panel>
          <PanelHeader title="Audit Detail" description={shortId(selectedLog.id)} />
          <div className="grid gap-5 p-5 lg:grid-cols-3">
            <div>
              <h3 className="mb-2 text-sm font-semibold text-gray-950">Metadata</h3>
              <JsonBlock value={selectedLog.metadata} />
            </div>
            <div>
              <h3 className="mb-2 text-sm font-semibold text-gray-950">Before</h3>
              <JsonBlock value={selectedLog.before} />
            </div>
            <div>
              <h3 className="mb-2 text-sm font-semibold text-gray-950">After</h3>
              <JsonBlock value={selectedLog.after} />
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}
