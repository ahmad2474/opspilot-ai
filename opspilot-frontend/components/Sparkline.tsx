interface SparklineProps {
  values: number[];
  threshold?: number;
  width?: number;
  height?: number;
}

/**
 * Small inline CPU history strip. Deliberately not a generic bar/line
 * chart component — this is the one visual signature of the Resources
 * page, rendering the actual subject matter (CPU over the lookback
 * window) rather than decorating a card with an icon.
 */
export default function Sparkline({ values, threshold = 80, width = 140, height = 32 }: SparklineProps) {
  if (values.length === 0) {
    return <div className="font-mono text-xs text-muted">no datapoints yet</div>;
  }

  const maxVal = Math.max(threshold, ...values, 1);
  const stepX = width / Math.max(values.length - 1, 1);
  const breached = values.some((v) => v > threshold);

  const points = values
    .map((v, i) => {
      const x = i * stepX;
      const y = height - (v / maxVal) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const thresholdY = height - (threshold / maxVal) * height;

  return (
    <svg width={width} height={height} className="overflow-visible" aria-hidden="true">
      <line
        x1={0}
        x2={width}
        y1={thresholdY}
        y2={thresholdY}
        stroke="#6E7681"
        strokeDasharray="2,3"
        strokeWidth={1}
      />
      <polyline
        points={points}
        fill="none"
        stroke={breached ? "#F85149" : "#3FB950"}
        strokeWidth={1.5}
      />
    </svg>
  );
}
