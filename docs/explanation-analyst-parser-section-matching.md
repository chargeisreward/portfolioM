# Analyst Parser Section Matching

The DOCX parser extracts the six sections of a company research report from heading paragraphs. This doc explains how it decides what is a heading, which section a heading belongs to, and what happens when nothing matches.

## The problem

The 8 company reports come from different authors and use slightly different heading styles. Some use Word's `Heading 1` styles, others type the title in plain text, others include the title as part of a numbered list. A single heuristic fails; the parser layers two strategies and falls through to the second when the first yields nothing.

## Layer 1 — heading style detection

The preferred path uses `python-docx`. It walks every paragraph and table in document order, asking `Paragraph.style.name`. If the style is `Heading 1` (or `Heading 2`, etc.), the parser treats the paragraph as a heading and prepends `#` (or `##`) to the markdown. Section keywords are then matched against the heading text.

This works for any DOCX authored in Word or LibreOffice with proper heading styles.

## Layer 2 — direct XML fallback

When `python-docx` is unavailable or fails, the parser opens the DOCX as a zip and reads `word/document.xml`. It walks every `w:p` (paragraph) and looks at the `w:pStyle` attribute on the `w:pPr` element. It also reads `word/styles.xml` to map `styleId` → `style name`, so headings defined via custom styles are still recognized.

If no `w:pStyle` is present, the paragraph is treated as plain text.

## The keyword list

Defined as `SECTION_KEYWORDS` in `services/analyst_parser.py:32`:

| Section field | Keywords matched (case-insensitive) |
|---|---|
| `section_1_market_focus` | `Narrative`, `市场为什么关注` |
| `section_2_core_competence` | `Fundamentals`, `核心竞争力`, `核心经营变量` |
| `section_3_supply_demand` | `Industry`, `供需格局`, `竞争格局` |
| `section_4_marginal_change` | `Marginal Change`, `边际变化` |
| `section_5_valuation` | `Valuation`, `怎么估值`, `估值` |
| `section_6_risk` | `Risk`, `Alpha`, `风险是什么`, `市场忽视了什么` |

Matching is substring-based: any heading containing any of the keywords triggers the section. The English keywords are matched in their original case but the DOCX is read as UTF-8, so headings like `Risk Factors` and `risk` both work. The Chinese keywords require an exact substring match — `风险提示` does not match `风险是什么`, only headings that literally contain `风险是什么` will hit section 6 via that keyword.

## The fallback to plain-text

If no paragraph in the entire document starts with `#` after the python-docx pass, the parser concludes the file uses no heading styles. It then runs the same keyword match against every paragraph as plain text — but only for paragraphs that "look like a heading" (short, no terminal punctuation, or wrapped in `#` by hand).

This double-scan is necessary because some authors write headings as bold body paragraphs without applying a `Heading` style. Without the plain-text fallback, those reports would land in the database with all six sections empty.

## How sections accumulate

Once a heading matches, all subsequent paragraphs (and table rows, in document order) are appended to that section's text buffer until the next matching heading. The buffer is concatenated with newlines and stored in the corresponding `section_*` column.

If a section's buffer ends up empty, the database column is `null`, and the pager in the UI shows "本节无内容".

## Special-case: filename as a heading

The parser ignores any paragraph whose text equals the filename minus `.docx`. This is a defensive measure for DOCX files that include the document title in their first paragraph, which would otherwise match no keyword and waste the first section.

## Trade-offs

- **Substring matching** is permissive. A paragraph that says "我们想讨论市场为什么关注这一点" would be treated as the section-1 heading, swallowing everything until the next match. In practice, only the author-written headings contain the keywords, so this has not been an issue.
- **No language detection.** A report that uses both Chinese and English headings will work because the keyword list contains both. A report in a third language would not be parsed into sections; it would land as `raw_text` only, with `report_available: false` on the card.
- **Section 6 (`risk`) accepts four keyword variants** because risk reports vary the most. Section 1 (`market_focus`) accepts only one because the standard narrative framework is stable.

## Related

- [How to add a new company research report](./howto-analyst-add-company-report.md) — practical guidance on authoring a DOCX that the parser recognizes.
- [Reference: analyst API and data model](./reference-analyst-api.md) — the resulting table schema.