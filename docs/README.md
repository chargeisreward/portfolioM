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

## Analyst panel

The Analyst tab combines 8 company research reports and 3 industry-chain summaries (from the local `researcher/` directory) with your portfolio data on the current business date. It is read-only.

| Document | Type | Purpose |
|----------|------|---------|
| [Tutorial: read your first analyst card](./tutorial-analyst-first-walkthrough.md) | Tutorial | Walk through one folded core-company card and its expanded tabs. |
| [How to ingest analyst data](./howto-analyst-restock-data.md) | How-to | Re-parse `researcher/` and overwrite the analyst tables. |
| [How to add a new company research report](./howto-analyst-add-company-report.md) | How-to | Add a 9th DOCX so a new card appears on the panel. |
| [How to read an industry-chain card](./howto-analyst-read-chain-card.md) | How-to | Interpret the 3-column comparison and the held-stock table. |
| [Analyst API and data model reference](./reference-analyst-api.md) | Reference | 4 endpoints, 3 tables, parser/service modules, React components. |
| [Analyst aggregation math](./explanation-analyst-aggregation-math.md) | Explanation | Why the drilled-only denominator, raw CSI300 weight, and virtual-earnings weighting. |
| [Analyst parser section matching](./explanation-analyst-parser-section-matching.md) | Explanation | How the DOCX parser maps headings to the six report sections. |

## Project data and design docs

- [`../data_get.md`](../data_get.md) — How PortfolioM fetches, caches, and persists market and industry data.
- [`../frontend/DESIGN_SPEC.md`](../frontend/DESIGN_SPEC.md) — UI architecture, tab layout, and visual style.
