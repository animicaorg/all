import { NavLink } from 'react-router-dom';
import { LayoutDashboard, Users, Layers, Settings } from 'lucide-react';

const navItems = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/miners', label: 'Miners', icon: Users },
  { to: '/blocks', label: 'Blocks', icon: Layers },
  { to: '/settings', label: 'Settings', icon: Settings },
];

const Sidebar = () => {
  return (
    <aside className="hidden sm:block w-64 border-r border-white/10 bg-black/40 backdrop-blur">
      <nav className="p-4 space-y-1">
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${
                  isActive ? 'bg-neon/20 text-white' : 'text-white/70 hover:text-white hover:bg-white/5'
                }`
              }
            >
              <Icon className="h-4 w-4" />
              <span>{item.label}</span>
            </NavLink>
          );
        })}
      </nav>
    </aside>
  );
};

export default Sidebar;
