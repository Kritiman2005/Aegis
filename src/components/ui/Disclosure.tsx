'use client';

import { ChevronRight } from 'lucide-react';
import { useState, ReactNode } from 'react';

// A collapsed-by-default section for a config panel field that's real but
// rarely touched — e.g. per-format engine overrides, or advanced memory
// tuning — so the panel's default view stays short and the common case
// isn't buried under a wall of fields nobody usually changes.
// defaultOpen lets a caller start it open when the section is actually the
// point of that particular node (nothing here is ever hidden permanently,
// just collapsed until asked for).
export function Disclosure({ label, defaultOpen = false, children }: { label: string; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-aegis-border pt-2">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-1.5 text-[10px] font-semibold text-aegis-text-muted uppercase hover:text-aegis-text-secondary transition-colors"
      >
        <ChevronRight className={`w-3 h-3 flex-shrink-0 transition-transform ${open ? 'rotate-90' : ''}`} />
        {label}
      </button>
      {open && <div className="flex flex-col gap-3 mt-2">{children}</div>}
    </div>
  );
}
