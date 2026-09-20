export { type ServiceIconSpec, getServiceIcon } from './serviceIconRegistry';
import { getServiceIcon } from './serviceIconRegistry';

// Rounded logo tile — neutral background so brand colors (including
// low-contrast ones like DuckDB's yellow) always stay readable in both
// themes, with a faint colored ring as the brand hint.
export function ServiceLogo({ serviceKey, size = 'md' }: { serviceKey?: string | null; size?: 'sm' | 'md' }) {
  const { Icon, color } = getServiceIcon(serviceKey);
  const box = size === 'sm' ? 'w-8 h-8 rounded-lg' : 'w-10 h-10 rounded-xl';
  const iconSize = size === 'sm' ? 16 : 20;
  return (
    <div
      className={`${box} bg-aegis-overlay flex items-center justify-center flex-shrink-0 ring-1`}
      style={{ ['--tw-ring-color' as any]: `${color}33` }}
    >
      <Icon size={iconSize} color={color} />
    </div>
  );
}
