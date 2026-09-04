'use client';

import { Search, Bell, Cloud, CheckCircle2 } from 'lucide-react';

interface HeaderProps {
  activeConnectorName?: string;
  onSearchChange?: (val: string) => void;
  searchPlaceholder?: string;
  onOpenMemory?: () => void;
}

export default function Header({
  activeConnectorName = 'GitHub',
  onSearchChange,
  searchPlaceholder = 'Search Workspace...',
  onOpenMemory,
}: HeaderProps) {
  return (
    <header className="h-16 px-8 border-b border-aegis-border bg-aegis-raised flex items-center justify-between flex-shrink-0">
      {/* Left Title & Status Pill */}
      <div className="flex items-center gap-4">
        <h1 className="text-base font-bold text-aegis-text-primary tracking-tight">
          Aegis Workspace
        </h1>

        {/* Active Connector Pill Badge (matching Image 2) */}
        {activeConnectorName && (
          <div className="flex items-center gap-1.5 px-3 py-1 bg-aegis-overlay border border-aegis-border rounded-full text-xs font-medium text-aegis-text-secondary">
            <span className="w-2 h-2 rounded-full bg-teal-500 animate-pulse" />
            <span>{activeConnectorName} Active</span>
          </div>
        )}
      </div>

      {/* Center Nav Links */}
      <nav className="hidden md:flex items-center gap-6 text-xs font-medium text-aegis-text-secondary">
        <button className="text-aegis-text-primary font-semibold hover:text-aegis-primary-light transition-colors">
          Dashboard
        </button>
        <button className="hover:text-aegis-text-primary transition-colors">Activity</button>
        <button className="hover:text-aegis-text-primary transition-colors">Sync</button>
      </nav>

      {/* Right Actions & Search */}
      <div className="flex items-center gap-4">
        {/* Search Bar */}
        <div className="relative hidden sm:block w-64">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-aegis-text-muted" />
          <input
            type="text"
            placeholder={searchPlaceholder}
            onChange={(e) => onSearchChange?.(e.target.value)}
            className="w-full bg-aegis-overlay border border-aegis-border rounded-full pl-9 pr-8 py-1.5 text-xs text-aegis-text-primary placeholder-gray-400 focus:outline-none focus:border-aegis-primary focus:bg-aegis-raised transition-all"
          />
          <div className="absolute right-3 top-1/2 -translate-y-1/2 flex items-center gap-0.5">
            <span className="text-[10px] text-aegis-text-muted bg-aegis-overlay/60 px-1.5 py-0.5 rounded font-mono">⌘K</span>
          </div>
        </div>

        {/* Icons */}
        <button 
          onClick={onOpenMemory}
          title="Open Global Memory Store"
          className="p-2 text-aegis-primary-light hover:text-aegis-primary-light hover:bg-aegis-primary/10 rounded-full transition-all border border-aegis-primary/20 bg-aegis-raised shadow-sm"
        >
          <Cloud className="w-4 h-4" />
        </button>
        <button className="p-2 text-aegis-text-secondary hover:text-aegis-text-primary hover:bg-aegis-overlay rounded-full transition-all">
          <Bell className="w-4 h-4" />
        </button>

        {/* User Profile Avatar */}
        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-aegis-primary to-aegis-accent flex items-center justify-center text-white text-xs font-bold ring-2 ring-aegis-border cursor-pointer overflow-hidden shadow-sm">
          <img
            src="https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=120&q=80"
            alt="User Avatar"
            className="w-full h-full object-cover"
            onError={(e) => {
              // Fallback to text initials if image fails
              (e.target as HTMLElement).style.display = 'none';
            }}
          />
          <span>AK</span>
        </div>
      </div>
    </header>
  );
}
