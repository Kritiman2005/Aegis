'use client';

import { ChevronDown } from 'lucide-react';
import { SelectHTMLAttributes, forwardRef } from 'react';

// A native <select> restyled to match every other custom control in the
// app (aegis-overlay background, aegis-border, focus ring) instead of the
// browser's own default select chrome — done via appearance-none plus a
// real ChevronDown icon rather than a full custom listbox: the closed-state
// control (what's visible almost all the time) becomes fully custom, while
// the OPEN dropdown list still renders with the OS's native popup styling,
// which isn't restylable without replacing <select> entirely with a
// keyboard-nav'd custom listbox — a much bigger, riskier component this
// didn't need just to fix how every picker in a node's config panel looks
// closed.
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className = '', children, ...props }, ref) => (
    <div className="relative">
      <select
        ref={ref}
        {...props}
        className={`w-full appearance-none bg-aegis-overlay border border-aegis-border rounded-md pl-2 pr-7 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary disabled:opacity-50 disabled:cursor-not-allowed ${className}`}
      >
        {children}
      </select>
      <ChevronDown className="w-3.5 h-3.5 text-aegis-text-muted absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none" />
    </div>
  )
);
Select.displayName = 'Select';
