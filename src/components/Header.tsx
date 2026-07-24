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
    <header className="h-16 px-8 border-b border-gray-200 bg-white flex items-center justify-between flex-shrink-0">
      {/* Left Title & Status Pill */}
      <div className="flex items-center gap-4">
        <h1 className="text-base font-bold text-gray-900 tracking-tight">
          Aegis Workspace
        </h1>

        {/* Active Connector Pill Badge (matching Image 2) */}
        {activeConnectorName && (
          <div className="flex items-center gap-1.5 px-3 py-1 bg-gray-100 border border-gray-200 rounded-full text-xs font-medium text-gray-700">
            <span className="w-2 h-2 rounded-full bg-teal-500 animate-pulse" />
            <span>{activeConnectorName} Active</span>
          </div>
        )}
      </div>

      {/* Center Nav Links */}
      <nav className="hidden md:flex items-center gap-6 text-xs font-medium text-gray-500">
        <button className="text-gray-900 font-semibold hover:text-black transition-colors">
          Dashboard
        </button>
        <button className="hover:text-gray-900 transition-colors">Activity</button>
        <button className="hover:text-gray-900 transition-colors">Sync</button>
      </nav>

      {/* Right Actions & Search */}
      <div className="flex items-center gap-4">
        {/* Search Bar */}
        <div className="relative hidden sm:block w-64">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            placeholder={searchPlaceholder}
            onChange={(e) => onSearchChange?.(e.target.value)}
            className="w-full bg-gray-50 border border-gray-200 rounded-full pl-9 pr-8 py-1.5 text-xs text-gray-800 placeholder-gray-400 focus:outline-none focus:border-gray-400 focus:bg-white transition-all"
          />
          <div className="absolute right-3 top-1/2 -translate-y-1/2 flex items-center gap-0.5">
            <span className="text-[10px] text-gray-400 bg-gray-200/60 px-1.5 py-0.5 rounded font-mono">⌘K</span>
          </div>
        </div>

        {/* Icons */}
        <button 
          onClick={onOpenMemory}
          title="Open Global Memory Store"
          className="p-2 text-indigo-500 hover:text-indigo-600 hover:bg-indigo-50 rounded-full transition-all border border-indigo-100 bg-white shadow-sm"
        >
          <Cloud className="w-4 h-4" />
        </button>
        <button className="p-2 text-gray-500 hover:text-gray-900 hover:bg-gray-100 rounded-full transition-all">
          <Bell className="w-4 h-4" />
        </button>

        {/* User Profile Avatar */}
        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-xs font-bold ring-2 ring-gray-100 cursor-pointer overflow-hidden shadow-sm">
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
