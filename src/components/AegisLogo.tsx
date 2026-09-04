/**
 * A blocky "A" monogram in Replit's visual language: discrete, evenly-gapped
 * rounded-square modules on a grid (not a single continuous silhouette) —
 * arranged into a 3-wide x 4-row pixel-font "A" instead of Replit's own
 * abstract zigzag. Built as plain SVG rects, one per module, so each can be
 * staggered independently for the splash screen's build-in animation.
 */
const BLOCK_RADIUS = 7;

const AEGIS_MARK_BLOCKS: { x: number; y: number; w: number; h: number }[] = [
  // apex
  { x: 37, y: 2, w: 26, h: 19 },
  // upper legs
  { x: 3, y: 28, w: 26, h: 19 },
  { x: 71, y: 28, w: 26, h: 19 },
  // crossbar row
  { x: 3, y: 54, w: 26, h: 19 },
  { x: 37, y: 54, w: 26, h: 19 },
  { x: 71, y: 54, w: 26, h: 19 },
  // base / feet
  { x: 3, y: 80, w: 26, h: 19 },
  { x: 71, y: 80, w: 26, h: 19 },
];

export function AegisMark({
  size = 64,
  glow = true,
  animated = false,
  color = '#F4622D',
}: {
  size?: number;
  glow?: boolean;
  animated?: boolean;
  color?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      style={glow ? { filter: 'drop-shadow(0 4px 14px rgba(244,98,45,0.45))' } : undefined}
    >
      {AEGIS_MARK_BLOCKS.map((b, i) => (
        <rect
          key={i}
          x={b.x}
          y={b.y}
          width={b.w}
          height={b.h}
          rx={BLOCK_RADIUS}
          fill={color}
          className={animated ? 'animate-block-pop' : undefined}
          style={
            animated
              ? { transformBox: 'fill-box', transformOrigin: 'center', animationDelay: `${i * 160}ms` }
              : undefined
          }
        />
      ))}
    </svg>
  );
}

export default function AegisLogo({ size = 64, wordmarkClassName = '' }: { size?: number; wordmarkClassName?: string }) {
  return (
    <div className="flex items-center gap-3">
      <AegisMark size={size} />
      <span className={`font-bold tracking-tight text-aegis-text-primary ${wordmarkClassName}`}>AEGIS</span>
    </div>
  );
}
