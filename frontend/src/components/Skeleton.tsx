/** Subtle skeleton shimmer for loading panels (not spinners everywhere). */
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`skeleton rounded-md ${className}`}
      aria-hidden
      role="presentation"
    />
  );
}

export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-hidden role="presentation">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton h-9 rounded-md" />
      ))}
    </div>
  );
}

export function SkeletonCards({ cards = 4 }: { cards?: number }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-hidden role="presentation">
      {Array.from({ length: cards }).map((_, i) => (
        <div key={i} className="skeleton h-[92px] rounded-lg" />
      ))}
    </div>
  );
}
