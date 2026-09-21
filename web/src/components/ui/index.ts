/**
 * Barrel dos primitivos de UI. Espelha o papel de
 * `src/components/ui/index.tsx` do TailAdmin, mas apenas com os
 * componentes que o BETGSN realmente usa.
 */

export { default as Badge } from "./Badge";
export { confidenceTone, type BadgeTone } from "./badgeTone";
export { default as Button } from "./Button";
export { default as Card } from "./Card";
export {
  CalibrationChart,
  ChartLegend,
  ColumnChart,
  HorizontalBars,
  LineChart,
  type BarDatum,
  type LineSeries,
} from "./Charts";
export { Field, Input, SearchInput, Select, Switch, type SelectOption } from "./Input";
export { default as KpiCard } from "./KpiCard";
export { default as ProbBar, ProbCompareBars } from "./ProbBar";
export {
  default as SegmentedControl,
  type Segment,
} from "./SegmentedControl";
export {
  EmptyState,
  ErrorPanel,
  KpiSkeleton,
  Skeleton,
  TableSkeleton,
  ToastStack,
  Tooltip,
} from "./States";
export {
  Table,
  TableShell,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from "./Table";
