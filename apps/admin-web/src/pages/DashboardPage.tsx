/**
 * Dashboard Page
 * System overview with live admin metrics.
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Activity, AlertTriangle, ArrowUpDown, FileCheck, ShieldCheck, TrendingUp, Users } from 'lucide-react';
import { apiClient } from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { Button, ErrorPanel, LoadingPanel, PageHeader, Panel, PanelHeader, StatusBadge } from '../components/AdminUI';
import { errorMessage, formatDateTime, formatNumber, shortId } from '../lib/format';

export default function DashboardPage() {
  const { admin } = useAuth();
  const [showBootstrapNotice, setShowBootstrapNotice] = useState(false);

  const overviewQuery = useQuery({
    queryKey: ['overview'],
    queryFn: () => apiClient.getOverview(),
  });

  const healthQuery = useQuery({
    queryKey: ['health'],
    queryFn: () => apiClient.getHealth(),
  });

  useEffect(() => {
    const flag = localStorage.getItem('admin_bootstrap_created');
    if (flag) {
      setShowBootstrapNotice(true);
      localStorage.removeItem('admin_bootstrap_created');
    }
  }, []);

  if (overviewQuery.isLoading) {
    return <LoadingPanel label="Loading dashboard" />;
  }

  if (overviewQuery.isError || !overviewQuery.data) {
    return <ErrorPanel message={errorMessage(overviewQuery.error, 'Failed to load dashboard.')} />;
  }

  const overview = overviewQuery.data.data;
  const health = healthQuery.data;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        description={`Signed in as ${admin?.email ?? 'admin'}`}
        actions={
          <Button type="button" variant="secondary" onClick={() => overviewQuery.refetch()}>
            <Activity className="h-4 w-4" />
            Refresh
          </Button>
        }
      />

      {showBootstrapNotice && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          Admin initialized successfully.
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          title="Active Users"
          value={formatNumber(overview.metrics.users.active)}
          subtitle={`${formatNumber(overview.metrics.users.total)} total accounts`}
          icon={Users}
        />
        <MetricCard
          title="KYC Queue"
          value={formatNumber(overview.metrics.kyc.pending)}
          subtitle="Pending or review cases"
          icon={FileCheck}
        />
        <MetricCard
          title="Withdrawal Queue"
          value={formatNumber(overview.metrics.withdrawals.pending)}
          subtitle="Awaiting risk or approval"
          icon={ArrowUpDown}
        />
        <MetricCard
          title="Open Incidents"
          value={formatNumber(overview.metrics.incidents.open)}
          subtitle={`${formatNumber(overview.metrics.markets.halted)} halted markets`}
          icon={AlertTriangle}
        />
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <Panel className="xl:col-span-2">
          <PanelHeader title="Recent Audit Events" />
          <div className="divide-y divide-gray-100">
            {overview.recentAudit.length === 0 ? (
              <div className="px-5 py-8 text-sm text-gray-500">No audit events recorded.</div>
            ) : (
              overview.recentAudit.map((entry) => (
                <div key={entry.id} className="grid gap-3 px-5 py-4 text-sm sm:grid-cols-[1fr_auto]">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-gray-950">{entry.action}</span>
                      <StatusBadge value={entry.actorType} />
                    </div>
                    <p className="mt-1 truncate text-gray-500">
                      {entry.actor} on {entry.entityType} {shortId(entry.entityId)}
                    </p>
                  </div>
                  <span className="text-gray-500">{formatDateTime(entry.createdAt)}</span>
                </div>
              ))
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Service Health" />
          <div className="space-y-4 p-5">
            <div className="flex items-center justify-between">
              <span className="text-sm text-gray-500">Admin API</span>
              <StatusBadge value={health?.status ?? (healthQuery.isError ? 'degraded' : 'checking')} />
            </div>
            {health?.checks &&
              Object.entries(health.checks).map(([name, check]) => (
                <div key={name} className="flex items-center justify-between border-t border-gray-100 pt-3">
                  <span className="text-sm capitalize text-gray-600">{name}</span>
                  <StatusBadge value={check.status} />
                </div>
              ))}
            <div className="grid grid-cols-2 gap-3 border-t border-gray-100 pt-4 text-sm">
              <Info label="New users 24h" value={formatNumber(overview.metrics.users.new24h)} />
              <Info label="Trades 24h" value={formatNumber(overview.metrics.trades.last24h)} />
            </div>
          </div>
        </Panel>
      </div>

      <Panel>
        <PanelHeader title="Operational Queues" />
        <div className="grid grid-cols-1 gap-3 p-5 md:grid-cols-2 xl:grid-cols-4">
          <QueueLink href="/kyc" icon={FileCheck} label="KYC Review" value={overview.metrics.kyc.pending} />
          <QueueLink
            href="/withdrawals"
            icon={ArrowUpDown}
            label="Withdrawals"
            value={overview.metrics.withdrawals.pending}
          />
          <QueueLink href="/markets" icon={TrendingUp} label="Markets" value={overview.metrics.markets.total} />
          <QueueLink href="/audit" icon={ShieldCheck} label="Audit Trail" value={overview.recentAudit.length} />
        </div>
      </Panel>
    </div>
  );
}

function MetricCard({
  title,
  value,
  subtitle,
  icon: Icon,
}: {
  title: string;
  value: string;
  subtitle: string;
  icon: typeof Activity;
}) {
  return (
    <Panel className="p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-gray-500">{title}</p>
          <p className="mt-2 text-3xl font-semibold text-gray-950">{value}</p>
          <p className="mt-1 text-sm text-gray-500">{subtitle}</p>
        </div>
        <div className="rounded-md bg-gray-100 p-3 text-gray-700">
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </Panel>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-gray-500">{label}</p>
      <p className="mt-1 font-semibold text-gray-950">{value}</p>
    </div>
  );
}

function QueueLink({
  href,
  icon: Icon,
  label,
  value,
}: {
  href: string;
  icon: typeof Activity;
  label: string;
  value: number;
}) {
  return (
    <Link
      to={href}
      className="flex items-center justify-between rounded-md border border-gray-200 px-4 py-3 text-sm transition hover:border-gray-300 hover:bg-gray-50"
    >
      <span className="flex items-center gap-3 font-medium text-gray-800">
        <Icon className="h-4 w-4 text-gray-500" />
        {label}
      </span>
      <span className="font-semibold text-gray-950">{formatNumber(value)}</span>
    </Link>
  );
}
