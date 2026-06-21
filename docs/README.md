# PortfolioM Documentation

This directory contains structured documentation for PortfolioM features. Docs are organized by the [Diataxis](https://diataxis.fr/) framework: tutorials, how-to guides, reference, and explanation.

## Drilled-dimension analysis

A set of pages that compare your portfolio's drilled-down fund holdings against the CSI300 index across industry classifications.

| Document | Type | Purpose |
|----------|------|---------|
| [Tutorial: first drilled-dimension analysis](./tutorial-first-drilled-dimension-analysis.md) | Tutorial | Learn to read the panel by comparing one sector against CSI300. |
| [How to read the drilled-dimension panel](./howto-read-drilled-dimension-panel.md) | How-to | Column-by-column guide to the table and colors. |
| [How to add a drilled dimension](./howto-add-drilled-dimension.md) | How-to | Wire a new classification dimension into the API and UI. |
| [Drilled-dimension analysis reference](./reference-dimension-drilled.md) | Reference | API endpoint, response schema, React props, backend functions. |
| [Drill-down math and valuation](./explanation-drilled-dimension-math.md) | Explanation | Why drilled-only, virtual earnings, CSI300 comparison, market filtering. |

## Project data and design docs

- [`../data_get.md`](../data_get.md) — How PortfolioM fetches, caches, and persists market and industry data.
- [`../frontend/DESIGN_SPEC.md`](../frontend/DESIGN_SPEC.md) — UI architecture, tab layout, and visual style.
