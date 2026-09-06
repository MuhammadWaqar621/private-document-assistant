import { useMemo } from "react";
import {
  ArcElement,
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LineElement,
  LinearScale,
  PointElement,
  RadialLinearScale,
  Title,
  Tooltip,
} from "chart.js";
import { Bar, Doughnut, Line, Pie, Radar } from "react-chartjs-2";

ChartJS.register(
  CategoryScale,
  LinearScale,
  RadialLinearScale,
  BarElement,
  LineElement,
  PointElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
  Filler,
);

// A palette matching the app's brand color, cycled across datasets/slices -
// distinct enough to stay readable, not a jarring rainbow.
const PALETTE = [
  "#7161ec", // brand-500
  "#22c55e", // emerald-500
  "#f59e0b", // amber-500
  "#ef4444", // red-500
  "#0ea5e9", // sky-500
  "#ec4899", // pink-500
  "#84cc16", // lime-500
  "#a855f7", // purple-500
];

type ChartSpec = {
  type?: "bar" | "line" | "pie" | "doughnut" | "radar";
  title?: string;
  labels: string[];
  datasets: { label?: string; data: number[] }[];
};

function isValidSpec(value: unknown): value is ChartSpec {
  if (!value || typeof value !== "object") return false;
  const spec = value as Partial<ChartSpec>;
  return (
    Array.isArray(spec.labels) &&
    Array.isArray(spec.datasets) &&
    spec.datasets.length > 0 &&
    spec.datasets.every((d) => d && Array.isArray(d.data))
  );
}

/**
 * Renders a ```chart fenced code block (see MarkdownMessage.tsx) as a real
 * Chart.js chart instead of printing its JSON as text - the model is
 * instructed (AGENT_SYSTEM_PROMPT in rag.py) to emit that block whenever a
 * question calls for a graph/chart, whether the numbers came from a
 * retrieved document or the model's own general knowledge. Chart.js gives
 * hover tooltips and a click-to-toggle legend for free, which is what
 * makes this "interactive" rather than a static image.
 */
export default function ChartBlock({ raw }: { raw: string }) {
  const spec = useMemo(() => {
    try {
      const parsed = JSON.parse(raw.trim());
      return isValidSpec(parsed) ? parsed : null;
    } catch {
      return null;
    }
  }, [raw]);

  if (!spec) {
    // Malformed chart JSON - fall back to showing it as plain code rather
    // than silently dropping the model's output.
    return (
      <pre className="mb-2 overflow-x-auto rounded-lg bg-slate-100 p-3 text-xs last:mb-0 dark:bg-slate-800">
        <code>{raw}</code>
      </pre>
    );
  }

  const type = spec.type ?? "bar";
  const isCircular = type === "pie" || type === "doughnut";

  const data = {
    labels: spec.labels,
    datasets: spec.datasets.map((dataset, i) => {
      const color = PALETTE[i % PALETTE.length];
      return isCircular
        ? {
            label: dataset.label,
            data: dataset.data,
            backgroundColor: spec.labels.map((_, j) => PALETTE[j % PALETTE.length]),
            borderWidth: 1,
          }
        : {
            label: dataset.label ?? `Series ${i + 1}`,
            data: dataset.data,
            backgroundColor: type === "line" ? `${color}33` : color,
            borderColor: color,
            borderWidth: 2,
            pointBackgroundColor: color,
            fill: type === "line",
            tension: 0.3,
          };
    }),
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        display: spec.datasets.length > 1 || isCircular,
        position: "bottom" as const,
        labels: { boxWidth: 12, font: { size: 11 } },
      },
      title: {
        display: Boolean(spec.title),
        text: spec.title ?? "",
        font: { size: 13, weight: "bold" as const },
      },
    },
    scales: isCircular
      ? undefined
      : {
          x: { grid: { display: false } },
          y: { beginAtZero: true },
        },
  };

  const ChartComponent = { bar: Bar, line: Line, pie: Pie, doughnut: Doughnut, radar: Radar }[type];

  return (
    <div className="mb-2 rounded-lg border border-slate-200 bg-white p-3 last:mb-0 dark:border-slate-700 dark:bg-slate-950">
      <div style={{ height: 280 }}>
        <ChartComponent data={data} options={options} />
      </div>
    </div>
  );
}
