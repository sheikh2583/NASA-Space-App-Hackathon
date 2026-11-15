---
id: "T64"
title: "Make area comparison results visible and actionable"
kind: "agent"
owner: "Sheikh"
authors: ["Sheikh","Shammo"]
phase: "P2"
size: "S"
needs: []
requires: []
branch: "ws1/T64-compare-feedback"
touches: ["ws5_frontend/src/App.tsx","ws5_frontend/src/App.test.tsx","ws5_frontend/src/components/SeriesChart.tsx","ws5_frontend/src/styles.css","ws5_frontend/src/i18n/en.json"]
creates: ["ws5_frontend/src/lib/compare.ts"]
checks: ["CHECK-PLAN","CHECK-WEB"]
---
## Goal
Make the existing area comparison action understandable and verifiable when it loads, succeeds, or fails.

## Steps
1. Preserve the existing `/aoi/compare` request and current primary-area/date/resolution semantics.
2. Show loading, success, and failure feedback beside the Compare control; identify the selected area and tell users the result appears in the chart above.
3. Label the comparison series with the selected area's name and align paired points by date, not array position.
4. Clear stale comparison/feedback when the selected area or primary analysis request changes.
5. Add focused static component and pure frontend regressions for all feedback states and date alignment.
6. Run CHECK-PLAN and CHECK-WEB; report that browser deployment still requires the normal merge and Render verification.

## Acceptance
- [ ] A successful comparison is identifiable by area and visible in the chart.
- [ ] Loading and request failure are visible; errors are no longer silently swallowed.
- [ ] Comparison values align by date and stale results clear when inputs change.

## Stop and ask if
The existing API response cannot support matching comparison dates without changing its scientific or public data contract.
