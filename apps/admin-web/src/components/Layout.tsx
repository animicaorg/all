/**
 * Main admin layout.
 */

import { Outlet, Link, useLocation } from 'react-router-dom';
import { useAuth, type Permission } from '../contexts/AuthContext';
import {
  Home,
  Users,
  FileCheck,
  TrendingUp,
  DollarSign,
  Wallet,
  ArrowUpDown,
  AlertTriangle,
  FileText,
  Settings,
  LogOut,
  Menu,
  X,
} from 'lucide-react';
import { useState } from 'react';
import clsx from 'clsx';

const navigation: Array<{
  name: string;
  href: string;
  icon: typeof Home;
  permission: Permission;
}> = [
  { name: 'Dashboard', href: '/', icon: Home, permission: 'audit:read' },
  { name: 'Users', href: '/users', icon: Users, permission: 'users:read' },
  { name: 'KYC Review', href: '/kyc', icon: FileCheck, permission: 'kyc:read' },
  { name: 'Markets', href: '/markets', icon: TrendingUp, permission: 'markets:read' },
  { name: 'Fees', href: '/fees', icon: DollarSign, permission: 'fees:read' },
  { name: 'Wallets', href: '/wallets', icon: Wallet, permission: 'wallets:read' },
  { name: 'Withdrawals', href: '/withdrawals', icon: ArrowUpDown, permission: 'withdrawals:read' },
  { name: 'Incidents', href: '/incidents', icon: AlertTriangle, permission: 'incidents:read' },
  { name: 'Audit Log', href: '/audit', icon: FileText, permission: 'audit:read' },
  { name: 'BitGo Settings', href: '/settings/bitgo', icon: Settings, permission: 'wallets:read' },
];

function isActivePath(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function Layout() {
  const { admin, logout, hasPermission } = useAuth();
  const location = useLocation();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const visibleNavigation = navigation.filter((item) => hasPermission(item.permission));
  const currentPage = visibleNavigation.find((item) => isActivePath(location.pathname, item.href));

  const renderNavItems = () => (
    <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4">
      {visibleNavigation.map((item) => {
        const isActive = isActivePath(location.pathname, item.href);
        return (
          <Link
            key={item.name}
            to={item.href}
            onClick={() => setSidebarOpen(false)}
            className={clsx(
              'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition',
              isActive ? 'bg-gray-950 text-white' : 'text-gray-700 hover:bg-gray-100 hover:text-gray-950'
            )}
          >
            <item.icon className="h-5 w-5" />
            {item.name}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="min-h-screen bg-gray-50">
      {sidebarOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            className="fixed inset-0 bg-gray-950/40"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close navigation overlay"
          />
          <div className="fixed inset-y-0 left-0 flex w-72 flex-col bg-white shadow-xl">
            <div className="flex h-16 items-center justify-between border-b border-gray-200 px-4">
              <Brand />
              <button
                type="button"
                onClick={() => setSidebarOpen(false)}
                className="rounded-md p-2 text-gray-500 hover:bg-gray-100 hover:text-gray-900"
                aria-label="Close navigation"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            {renderNavItems()}
          </div>
        </div>
      )}

      <div className="hidden lg:fixed lg:inset-y-0 lg:flex lg:w-72 lg:flex-col">
        <div className="flex flex-grow flex-col border-r border-gray-200 bg-white">
          <div className="flex h-16 items-center border-b border-gray-200 px-5">
            <Brand />
          </div>
          {renderNavItems()}
        </div>
      </div>

      <div className="lg:pl-72">
        <div className="sticky top-0 z-20 flex h-16 border-b border-gray-200 bg-white/95 backdrop-blur">
          <button
            type="button"
            className="px-4 text-gray-500 hover:text-gray-900 lg:hidden"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open navigation"
          >
            <Menu className="h-6 w-6" />
          </button>
          <div className="flex min-w-0 flex-1 items-center justify-between gap-4 px-4">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-gray-950">{currentPage?.name ?? 'Admin'}</p>
              <p className="text-xs text-gray-500">Animica CEX operations</p>
            </div>
            <div className="flex items-center gap-3">
              <div className="hidden text-right text-sm sm:block">
                <div className="font-medium text-gray-950">{admin?.email}</div>
                <div className="text-xs text-gray-500">{admin?.role}</div>
              </div>
              <button
                type="button"
                onClick={logout}
                className="rounded-md p-2 text-gray-500 hover:bg-gray-100 hover:text-gray-900"
                title="Logout"
                aria-label="Logout"
              >
                <LogOut className="h-5 w-5" />
              </button>
            </div>
          </div>
        </div>

        <main className="p-4 sm:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-9 w-9 items-center justify-center rounded-md bg-gray-950 text-sm font-semibold text-white">
        A
      </div>
      <div>
        <div className="text-sm font-semibold text-gray-950">Animica Admin</div>
        <div className="text-xs text-gray-500">Exchange console</div>
      </div>
    </div>
  );
}
