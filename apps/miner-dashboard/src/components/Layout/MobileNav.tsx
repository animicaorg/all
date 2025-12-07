import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, Users, Layers, Settings, Menu, X } from 'lucide-react';

const navItems = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/miners', label: 'Miners', icon: Users },
  { to: '/blocks', label: 'Blocks', icon: Layers },
  { to: '/settings', label: 'Settings', icon: Settings },
];

const MobileNav = () => {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      {/* Mobile menu button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="sm:hidden fixed bottom-4 right-4 z-50 h-14 w-14 rounded-full bg-neon shadow-lg flex items-center justify-center text-white hover:bg-neon/90 transition-colors"
        aria-label={isOpen ? 'Close menu' : 'Open menu'}
        aria-expanded={isOpen}
      >
        {isOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
      </button>

      {/* Overlay */}
      {isOpen && (
        <div
          className="sm:hidden fixed inset-0 bg-black/60 backdrop-blur-sm z-40"
          onClick={() => setIsOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Mobile menu */}
      <nav
        className={`sm:hidden fixed bottom-0 left-0 right-0 bg-night/95 backdrop-blur border-t border-white/10 z-40 transition-transform duration-300 ${
          isOpen ? 'translate-y-0' : 'translate-y-full'
        }`}
        aria-label="Mobile navigation"
      >
        <div className="p-4 space-y-1 max-h-[70vh] overflow-y-auto">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                onClick={() => setIsOpen(false)}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-4 py-3 rounded-lg transition-colors min-h-[44px] ${
                    isActive ? 'bg-neon/20 text-white' : 'text-white/70 hover:text-white hover:bg-white/5'
                  }`
                }
              >
                <Icon className="h-5 w-5 flex-shrink-0" />
                <span className="text-base">{item.label}</span>
              </NavLink>
            );
          })}
        </div>
      </nav>
    </>
  );
};

export default MobileNav;
