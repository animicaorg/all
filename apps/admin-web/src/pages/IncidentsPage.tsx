import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, Search, Send } from 'lucide-react';
import { apiClient, type Incident } from '../services/api';
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

export default function IncidentsPage() {
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [severity, setSeverity] = useState('');
  const [page, setPage] = useState(1);
  const [title, setTitle] = useState('');
  const [newSeverity, setNewSeverity] = useState<Incident['severity']>('MEDIUM');
  const [selectedIncident, setSelectedIncident] = useState<Incident | null>(null);
  const [actionName, setActionName] = useState('');
  const [actionPayload, setActionPayload] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);

  const params = useMemo(
    () => ({
      page,
      limit: 25,
      query: query || undefined,
      status: status || undefined,
      severity: severity || undefined,
    }),
    [page, query, status, severity]
  );

  const incidentsQuery = useQuery({
    queryKey: ['incidents', params],
    queryFn: () => apiClient.listIncidents(params),
  });

  const invalidateIncidents = async () => {
    await queryClient.invalidateQueries({ queryKey: ['incidents'] });
  };

  const createMutation = useMutation({
    mutationFn: () => apiClient.createIncident({ title, severity: newSeverity }),
    onSuccess: async () => {
      setTitle('');
      setNewSeverity('MEDIUM');
      await invalidateIncidents();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, nextStatus }: { id: string; nextStatus: Incident['status'] }) =>
      apiClient.updateIncident(id, { status: nextStatus }),
    onSuccess: async () => {
      setSelectedIncident(null);
      await invalidateIncidents();
    },
  });

  const actionMutation = useMutation({
    mutationFn: () => {
      setActionError(null);
      let payload: unknown = null;
      if (actionPayload.trim()) {
        try {
          payload = JSON.parse(actionPayload);
        } catch {
          setActionError('Action payload JSON is invalid.');
          throw new Error('Action payload JSON is invalid.');
        }
      }
      return apiClient.addIncidentAction(selectedIncident!.id, {
        action: actionName,
        status: 'completed',
        payload,
      });
    },
    onSuccess: async () => {
      setActionName('');
      setActionPayload('');
      setSelectedIncident(null);
      await invalidateIncidents();
    },
  });

  const incidents = incidentsQuery.data?.data.incidents ?? [];
  const pagination = incidentsQuery.data?.data.pagination;

  return (
    <div className="space-y-6">
      <PageHeader title="Incidents" description="Operational incidents and action history." />

      <Panel>
        <PanelHeader title="Create Incident" />
        <form
          className="grid gap-3 p-5 md:grid-cols-[1fr_180px_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            createMutation.mutate();
          }}
        >
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Incident title"
            className="field-input"
          />
          <select
            value={newSeverity}
            onChange={(event) => setNewSeverity(event.target.value as Incident['severity'])}
            className="field-input"
          >
            <option value="LOW">Low</option>
            <option value="MEDIUM">Medium</option>
            <option value="HIGH">High</option>
            <option value="CRITICAL">Critical</option>
          </select>
          <Button type="submit" disabled={!hasPermission('incidents:execute') || title.trim().length < 3}>
            <Plus className="h-4 w-4" />
            Create
          </Button>
        </form>
        {createMutation.isError && (
          <div className="px-5 pb-5">
            <ErrorPanel message={errorMessage(createMutation.error, 'Incident creation failed.')} />
          </div>
        )}
      </Panel>

      <Panel>
        <div className="grid gap-3 border-b border-gray-200 p-5 md:grid-cols-[1fr_180px_180px_auto]">
          <label className="relative">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
            <input
              type="search"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
              placeholder="Title"
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
            <option value="OPEN">Open</option>
            <option value="IN_PROGRESS">In progress</option>
            <option value="RESOLVED">Resolved</option>
            <option value="CLOSED">Closed</option>
          </select>
          <select
            value={severity}
            onChange={(event) => {
              setSeverity(event.target.value);
              setPage(1);
            }}
            className="field-input"
          >
            <option value="">All severities</option>
            <option value="LOW">Low</option>
            <option value="MEDIUM">Medium</option>
            <option value="HIGH">High</option>
            <option value="CRITICAL">Critical</option>
          </select>
          <Button type="button" variant="secondary" onClick={() => incidentsQuery.refetch()}>
            <Search className="h-4 w-4" />
            Search
          </Button>
        </div>

        {incidentsQuery.isLoading ? (
          <div className="p-5">
            <LoadingPanel label="Loading incidents" />
          </div>
        ) : incidentsQuery.isError ? (
          <div className="p-5">
            <ErrorPanel message={errorMessage(incidentsQuery.error, 'Failed to load incidents.')} />
          </div>
        ) : incidents.length === 0 ? (
          <EmptyState title="No incidents found" />
        ) : (
          <div className="divide-y divide-gray-100">
            {incidents.map((incident) => (
              <button
                key={incident.id}
                type="button"
                className="block w-full px-5 py-4 text-left hover:bg-gray-50"
                onClick={() => setSelectedIncident(incident)}
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="font-medium text-gray-950">{incident.title}</div>
                    <div className="mt-1 text-xs text-gray-500">
                      {shortId(incident.id)} by {incident.creator?.email ?? 'admin'} at {formatDateTime(incident.createdAt)}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <StatusBadge value={incident.severity} />
                    <StatusBadge value={incident.status} />
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}

        {pagination && (
          <PaginationControls page={pagination.page} totalPages={pagination.totalPages} onPageChange={setPage} />
        )}
      </Panel>

      {selectedIncident && (
        <Panel>
          <PanelHeader title="Incident Detail" description={selectedIncident.title} />
          <div className="grid gap-6 p-5 xl:grid-cols-[1fr_360px]">
            <div className="space-y-3">
              {selectedIncident.actions.length === 0 ? (
                <EmptyState title="No incident actions recorded" />
              ) : (
                selectedIncident.actions.map((action) => (
                  <div key={action.id} className="rounded-lg border border-gray-200 p-4 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium text-gray-950">{action.action}</span>
                      <StatusBadge value={action.status} />
                    </div>
                    <p className="mt-1 text-xs text-gray-500">
                      {action.creator?.email ?? 'admin'} at {formatDateTime(action.createdAt)}
                    </p>
                    {action.payload ? <div className="mt-3"><JsonBlock value={action.payload} /></div> : null}
                  </div>
                ))
              )}
            </div>
            <div className="border-t border-gray-200 pt-5 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0">
              <h3 className="text-sm font-semibold text-gray-950">Actions</h3>
              <div className="mt-4 flex flex-wrap gap-2">
                {(['IN_PROGRESS', 'RESOLVED', 'CLOSED'] as const).map((nextStatus) => (
                  <Button
                    key={nextStatus}
                    type="button"
                    variant="secondary"
                    disabled={!hasPermission('incidents:execute') || updateMutation.isPending}
                    onClick={() => updateMutation.mutate({ id: selectedIncident.id, nextStatus })}
                  >
                    {nextStatus.replace(/_/g, ' ')}
                  </Button>
                ))}
              </div>
              <div className="mt-5 space-y-3">
                <input
                  value={actionName}
                  onChange={(event) => setActionName(event.target.value)}
                  placeholder="Action name"
                  className="field-input"
                />
                <textarea
                  value={actionPayload}
                  onChange={(event) => setActionPayload(event.target.value)}
                  rows={5}
                  placeholder="Action payload JSON"
                  className="field-input font-mono text-xs"
                />
                <Button
                  type="button"
                  disabled={!hasPermission('incidents:execute') || actionName.trim().length < 2 || actionMutation.isPending}
                  onClick={() => actionMutation.mutate()}
                >
                  <Send className="h-4 w-4" />
                  Add Action
                </Button>
                {(actionError || updateMutation.isError || actionMutation.isError) && (
                  <ErrorPanel
                    message={
                      actionError ??
                      errorMessage(updateMutation.error ?? actionMutation.error, 'Incident action failed.')
                    }
                  />
                )}
              </div>
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}
