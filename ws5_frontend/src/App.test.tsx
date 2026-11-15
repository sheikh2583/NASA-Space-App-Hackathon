import { describe, expect, it } from "vitest";
import { analysisArea, CompareCard } from "./App";
import type { AreaOption } from "./areas";
import { renderHtml, textOf } from "./test/render";
import { comparisonByDate } from "./lib/compare";

const areas: AreaOption[] = [
  { id: "primary", name: "Dhaka", country: "BD", cellCount: 1, request: { bbox: [89, 22, 90, 23] } },
  { id: "other", name: "Punjab", country: "IN", cellCount: 1, request: { bbox: [74, 29, 76, 31] } },
];

describe("analysis transport", () => {
  const option: AreaOption = { id: "catalog-id", name: "Area", country: "BD", cellCount: 1,
    request: { geometry: { type: "Polygon", coordinates: [[[90, 23], [91, 23], [91, 24], [90, 23]]] } } };
  it("uses the ID and preserves original geometry for map consumers", () => {
    expect(analysisArea(option)).toEqual({ area_id: option.id });
    expect(option.request.geometry).toBeDefined();
  });
  it("keeps custom/example geometry and bbox requests", () => {
    expect(analysisArea({ ...option, cellCount: undefined })).toEqual(option.request);
    const sample = { ...option, cellCount: undefined, request: { bbox: [89, 23, 90, 24] as [number, number, number, number] } };
    expect(analysisArea(sample)).toEqual(sample.request);
  });
});

describe("area comparison feedback", () => {
  const render = (state: "loading" | "success" | "error") => renderHtml(
    <CompareCard compact={false} areas={areas} selectedId="primary" value="other" comparisonName="Punjab"
      state={state} onChange={() => undefined} onCompare={() => undefined} />,
  );

  it("shows progress and disables repeat submissions while loading", async () => {
    const html = await render("loading");
    expect(textOf(html)).toContain("Loading the Punjab comparison");
    expect(html).toContain("disabled");
  });

  it("points users to the chart after success", async () => {
    const html = await render("success");
    expect(textOf(html)).toContain("Punjab is added as the blue line in the chart above.");
    expect(html).toContain('role="status"');
  });

  it("announces a failed request instead of silently doing nothing", async () => {
    const html = await render("error");
    expect(textOf(html)).toContain("Could not compare with Punjab");
    expect(html).toContain('role="alert"');
  });
});

describe("comparison date alignment", () => {
  const point = (t: string, value: number) => ({ t, value, lo: null, hi: null, source: "observed_viirs" as const,
    modeled_share: 0, drift_flag: false });

  it("pairs values by date even when the API returns a different order", () => {
    const aligned = comparisonByDate([point("2026-01-05", 1), point("2026-01-12", 2)],
      [point("2026-01-12", 20), point("2026-01-05", 10)]);
    expect(aligned.map((item) => item.comparison)).toEqual([10, 20]);
  });

  it("leaves dates with no matching comparison point empty", () => {
    const aligned = comparisonByDate([point("2026-01-05", 1)], [point("2026-01-12", 20)]);
    expect(aligned[0].comparison).toBeUndefined();
  });
});
