import type { SummaryPoint } from "../types";

/** Attach comparison values to the primary date axis, independent of response ordering. */
export function comparisonByDate(primary: SummaryPoint[], comparison?: SummaryPoint[]) {
  const values = new Map(comparison?.map((point) => [point.t, point.value] as const));
  return primary.map((point) => ({
    ...point,
    comparison: point.complete === false ? undefined : values.get(point.t),
  }));
}
