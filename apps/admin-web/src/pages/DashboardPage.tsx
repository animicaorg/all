/**
 * Dashboard Page
 * System overview with health metrics
 */

import { useEffect, useState } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { Activity, Users, AlertTriangle, ArrowUpDown } from 'lucide-react';

export default function DashboardPage() {
  const { admin } = useAuth();
  const [showBootstrapNotice, setShowBootstrapNotice] = useState(false);

  useEffect(() => {
    const flag = localStorage.getItem('admin_bootstrap_created');
    if (flag) {
      setShowBootstrapNotice(true);
      localStorage.removeItem('admin_bootstrap_created');
    }
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <p className="mt-1 text-sm text-gray-500">
          Welcome back, {admin?.email}
        </p>
      </div>
      {showBootstrapNotice && (
        <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded">
          Admin initialized successfully.
        </div>
      )}

      {/* Metrics */}
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          title="System Status"
          value="Healthy"
          icon={Activity}
          color="green"
          subtitle="All systems operational"
        />
        <MetricCard
          title="Active Users"
          value="12,543"
          icon={Users}
          color="blue"
          subtitle="+12% from last month"
        />
        <MetricCard
          title="Pending Withdrawals"
          value="23"
          icon={ArrowUpDown}
          color="yellow"
          subtitle="Awaiting approval"
        />
        <MetricCard
          title="Active Incidents"
          value="0"
          icon={AlertTriangle}
          color="green"
          subtitle="No active incidents"
        />
      </div>

      {/* Quick Actions */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">Recent Activity</h2>
          <div className="space-y-3">
            <ActivityItem
              action="User registration"
              user="user@example.com"
              time="2 minutes ago"
            />
            <ActivityItem
              action="KYC approved"
              user="john.doe@example.com"
              time="15 minutes ago"
            />
            <ActivityItem
              action="Withdrawal approved"
              user="jane.smith@example.com"
              time="1 hour ago"
            />
          </div>
        </div>

        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">Quick Actions</h2>
          <div className="space-y-2">
            <QuickActionButton href="/kyc">Review KYC Queue (5)</QuickActionButton>
            <QuickActionButton href="/withdrawals">Approve Withdrawals (23)</QuickActionButton>
            <QuickActionButton href="/users">Manage Users</QuickActionButton>
            <QuickActionButton href="/markets">Market Controls</QuickActionButton>
          </div>
        </div>
      </div>

      {/* System Info */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-medium text-gray-900 mb-4">System Information</h2>
        <dl className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          <SystemInfoItem label="Admin Role" value={admin?.role || 'N/A'} />
          <SystemInfoItem label="Last Login" value={admin?.lastLoginAt ? new Date(admin.lastLoginAt).toLocaleString() : 'First login'} />
          <SystemInfoItem label="Session Status" value="Active" />
        </dl>
      </div>
    </div>
  );
}

function MetricCard({
  title,
  value,
  icon: Icon,
  color,
  subtitle,
}: {
  title: string;
  value: string;
  icon: any;
  color: 'green' | 'blue' | 'yellow' | 'red';
  subtitle: string;
}) {
  const colorClasses = {
    green: 'bg-green-100 text-green-600',
    blue: 'bg-blue-100 text-blue-600',
    yellow: 'bg-yellow-100 text-yellow-600',
    red: 'bg-red-100 text-red-600',
  };

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <div className="flex items-center">
        <div className={`p-3 rounded-lg ${colorClasses[color]}`}>
          <Icon className="w-6 h-6" />
        </div>
        <div className="ml-5">
          <p className="text-sm font-medium text-gray-500">{title}</p>
          <p className="text-2xl font-semibold text-gray-900">{value}</p>
          <p className="text-sm text-gray-500 mt-1">{subtitle}</p>
        </div>
      </div>
    </div>
  );
}

function ActivityItem({ action, user, time }: { action: string; user: string; time: string }) {
  return (
    <div className="flex items-start space-x-3 text-sm">
      <div className="flex-shrink-0 w-2 h-2 mt-2 bg-blue-500 rounded-full" />
      <div className="flex-1 min-w-0">
        <p className="text-gray-900">
          {action} <span className="font-medium">{user}</span>
        </p>
        <p className="text-gray-500">{time}</p>
      </div>
    </div>
  );
}

function QuickActionButton({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      className="block w-full px-4 py-2 text-sm font-medium text-blue-600 bg-blue-50 rounded-md hover:bg-blue-100 text-center"
    >
      {children}
    </a>
  );
}

function SystemInfoItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-sm font-medium text-gray-500">{label}</dt>
      <dd className="mt-1 text-sm text-gray-900">{value}</dd>
    </div>
  );
}
