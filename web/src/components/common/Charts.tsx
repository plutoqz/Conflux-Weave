import React from "react";
import { cn } from "@/lib/utils";

interface DonutSegment {
  label: string;
  value: number;
  color: string;
}

interface DonutChartProps {
  segments: DonutSegment[];
  size?: number;
  strokeWidth?: number;
  centerTitle?: string;
  centerValue?: string;
}

export const DonutChart: React.FC<DonutChartProps> = ({
  segments,
  size = 130,
  strokeWidth = 16,
  centerTitle = "",
  centerValue = "",
}) => {
  const total = segments.reduce((sum, s) => sum + (s.value || 0), 0);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const center = size / 2;

  let accumulatedOffset = 0;

  return (
    <div className="flex items-center justify-center gap-6 p-2">
      <div className="relative shrink-0" style={{ width: size, height: size }}>
        <svg viewBox={`0 0 ${size} ${size}`} className="w-full h-full transform -rotate-90">
          <circle
            cx={center}
            cy={center}
            r={radius}
            fill="none"
            stroke="hsl(var(--muted))"
            strokeWidth={strokeWidth}
          />
          {segments.map((seg, i) => {
            if (seg.value <= 0 || total <= 0) return null;
            const ratio = seg.value / total;
            const strokeDash = ratio * circumference;
            const strokeOffset = -accumulatedOffset;
            accumulatedOffset += strokeDash;

            return (
              <circle
                key={i}
                cx={center}
                cy={center}
                r={radius}
                fill="none"
                stroke={seg.color}
                strokeWidth={strokeWidth}
                strokeDasharray={`${strokeDash} ${Math.max(0, circumference - strokeDash)}`}
                strokeDashoffset={strokeOffset}
                strokeLinecap="round"
                className="transition-all duration-500 hover:opacity-80"
              />
            );
          })}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center text-center pointer-events-none">
          {centerValue && (
            <span className="text-base font-bold font-mono text-foreground">{centerValue}</span>
          )}
          {centerTitle && (
            <span className="text-xs text-foreground/75 font-serif-academic font-medium">{centerTitle}</span>
          )}
        </div>
      </div>

      <div className="flex flex-col space-y-1.5 text-xs sm:text-sm font-serif-academic">
        {segments.map((seg, i) => {
          const pct = total > 0 ? Math.round((seg.value / total) * 100) : 0;
          return (
            <div key={i} className="flex items-center space-x-2">
              <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ backgroundColor: seg.color }} />
              <span className="text-foreground/80 font-medium">{seg.label}:</span>
              <span className="font-mono text-foreground font-semibold">
                {seg.value} ({pct}%)
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
};

interface HistogramProps {
  data: Array<{ label: string; value: number; color?: string }>;
  height?: number;
}

const HIST_DEFAULT_COLORS = [
  "#1B4931", // Oxford Forest Green
  "#2D6A4F", // Deep Forest Green
  "#0F766E", // Deep Teal
  "#475569", // Academic Slate
];

export const HistogramChart: React.FC<HistogramProps> = ({ data, height = 96 }) => {
  const maxVal = Math.max(...data.map((d) => d.value), 1);

  return (
    <div className="w-full pt-3 pb-1">
      {/* Bars container with bottom baseline */}
      <div
        className="flex items-end justify-between gap-4 px-3 pb-1 border-b border-border/70"
        style={{ height: `${height}px` }}
      >
        {data.map((item, idx) => {
          const heightPct = Math.max(8, Math.round((item.value / maxVal) * 100));
          const barColor = item.color || HIST_DEFAULT_COLORS[idx % HIST_DEFAULT_COLORS.length];
          return (
            <div
              key={idx}
              className="flex-1 flex flex-col items-center justify-end h-full group cursor-default"
            >
              <span className="text-xs font-mono font-bold text-foreground/85 group-hover:text-foreground mb-1 transition-colors">
                {item.value}
              </span>
              <div
                className="w-[55%] min-w-[20px] max-w-[42px] rounded-t-md transition-all duration-300 shadow-2xs group-hover:brightness-110"
                style={{
                  height: `${heightPct}%`,
                  backgroundColor: barColor,
                }}
                title={`${item.label}: ${item.value} 份`}
              />
            </div>
          );
        })}
      </div>

      {/* Independent label row below baseline: zero risk of overflow or squishing */}
      <div className="flex items-center justify-between gap-4 px-3 pt-2">
        {data.map((item, idx) => (
          <span
            key={idx}
            className="flex-1 text-center text-xs sm:text-sm font-serif-academic text-foreground/80 font-medium whitespace-nowrap"
          >
            {item.label}
          </span>
        ))}
      </div>
    </div>
  );
};
