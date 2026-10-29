# Inspired Automation: email design spec

Version 1.0 (27/10/2026). Owner: Automation Team.

This spec tells you, an AI agent or developer, how to build the HTML body of any email sent by an Inspired automated process through the Microsoft Graph `sendMail` API. Follow it exactly. When something is not covered here, choose the simplest option that fits the rules below.

Base template: `email-base-template.html` (ships with this spec). Always start from it. Do not write an email shell from scratch.

**In Python, do not write the HTML at all.** Use `automation_core.email` (automation-core 1.14.0 or later), which ships with this spec. It renders the template and every component in section 6, applies the formatting rules in section 3, escapes all values, and sends through Graph with attachments of any size. Build an `Email`, call `render()`, and pass the result to `send_email()`; the module docstring has a complete example. The rules below still apply to what you put in it: wording, sentence case, which type to use, what goes in the summary. Write HTML by hand only outside Python.

---

## 1. How an automated email is put together

Every email has four layers. You only write layer 2.

| Layer | What it is | Who provides it |
|---|---|---|
| 1. Shell | Outer tables, fonts, width, Outlook fixes, the Inspired Automation header band | Base template. Never change it. |
| 2. Content | Header, summary, KPIs, tables, links, notes | **You**, built only from the components in section 6 |
| 3. Automation footer | The fixed "sent by Inspired Automation" notice | Base template. Fill `{{PROCESS_NAME}}` only. |
| 4. Signature | Inspired logo, head office, socials | Added by the mail system after sending. **Never include it.** |

Hard rules:

- Never add the Inspired Group logo, signature block, phone number, social icons or "follow us on LinkedIn" text. The system adds them.
- Never add another logo or wordmark. The header band already carries the Inspired Automation wordmark.
- Never remove, reword or restyle the automation footer.
- Never add your own "do not reply" line. The footer already says it.

### The automation footer (mandatory, exact wording)

> This email was sent by Inspired Automation. Please do not reply to this email.
> Should you have any issues, please raise a ticket by emailing automation@inspiredenergy.co.uk.
> Process: {{PROCESS_NAME}}

`{{PROCESS_NAME}}` is the human-readable process name, for example `Data imports MI report (Thames Water reads)`. It lets the reader quote the right process when raising a ticket. The email address is a `mailto:` link.

### Inspired Automation is internal branding

The header band names the team, Inspired Automation, which is an internal brand. The Group brand guidelines' logo rules (one logo, no suffix) cover external communication and do not apply to it. Every email carries the band, so before a process sends to anyone outside the company, raise it with the team: the band would need to become optional.

---

## 2. Brand tokens

Taken from the Inspired Brand Guidelines v1.2 (2025). Use these hex values only. Do not introduce other colours.

### Colours

| Token | Hex | Use |
|---|---|---|
| `midnight` | `#18073a` | Header band, title, section headings, KPI values, table header rule, button |
| `midnight-80` | `#29215a` | Links |
| `purple-60` | `#4d4777` | Sub-headings, labels, meta text, table header text |
| `purple-20` | `#b0adc8` | Decorative only (never text) |
| `black` | `#000000` | All body copy (brand rule: body copy is black) |
| `light-grey` | `#dededd` | Dividers, table row lines, and the email type in the header band |
| `surface` | `#f6f6f5` | KPI tile and code-path background |
| `white` | `#ffffff` | Email background (always set explicitly), wordmark in the header band |

Status colours. Text and background always go together, and the status is always written as a word too ("Completed", "Needs attention", "Failed"). Never use colour on its own to carry meaning.

| Status | Text | Background | Brand source |
|---|---|---|---|
| Success | `#29372a` | `#e4efdf` | Green highlight hues |
| Warning | `#7a4420` | `#f8eddb` | Gold highlight hues |
| Error | `#590006` | `#f2d7d0` | Red highlight hues |
| Info | `#013c4e` | `#deebf3` | Blue highlight hues |

Do not use the full-strength brand Blue `#498ca7`, Green `#677a64`, Gold `#cc8943` or Red `#82162d` for text smaller than 18px. Gold and Blue fail contrast on white. The brand guidelines say to use these accents "in small doses", so one status banner per email is the norm.

### Typography

Font stack (always this, in this order):

```
font-family: Montserrat, Aptos, 'Segoe UI', Arial, sans-serif;
```

Montserrat is the corporate typeface. Most recipients do not have it installed, so Aptos (the brand's approved Microsoft fallback) usually renders. Never load web fonts with `@import` or `<link>`. The base template forces Aptos/Arial in Outlook desktop to stop a Times New Roman fallback.

| Role | Size / line height | Weight | Colour |
|---|---|---|---|
| Header band wordmark | 15px / 20px | 600 "Inspired", 400 "Automation" | `white` |
| Header band email type | 12px / 20px | 600 | `light-grey` |
| Title (one per email) | 24px / 32px | 300 (Light) | `midnight` |
| Meta line (under title) | 13px / 20px | 400 | `purple-60` |
| Section heading | 16px / 24px | 600 | `midnight` |
| Sub-heading | 14px / 20px | 500 | `purple-60` |
| Body | 14px / 22px | 400 | `black` |
| Table cell | 14px / 20px | 400 | `black` |
| Table header | 12px / 16px | 600 | `purple-60` |
| KPI value | 28px / 34px | 600 | `midnight` |
| KPI label | 13px / 18px | 400 | `purple-60` |
| Small print / footer | 12px / 18px | 400 | `purple-60` |

Nothing smaller than 12px. Nothing bold except headings, KPI values, table totals, the status word and the header band.

### Spacing

Use multiples of 8px: 8, 16, 24, 32, 40. Content padding is 32px left and right on desktop. Put spacing on `<td>` padding, never on `<p>` or `<div>` margins (Outlook ignores many margins).

| Gap | Size |
|---|---|
| Between sections | 32px |
| Section heading to its content | 12px |
| Between paragraphs | 16px |
| Between KPI tiles | 8px |

### Layout

- Content width: **640px**, single column.
- **Left-aligned**, not centred. The system signature is left-aligned, so a centred body looks broken next to it.
- A `midnight` header band across the top, 48px tall, is the only decorative element. It holds the Inspired Automation wordmark on the left and the email type on the right, both as live text: no image, so nothing for Outlook to block, and its own dark background, so dark mode leaves it as it is.
- Background white. No background images, no gradients, no shadows, no rounded-corner dependence (Outlook renders them square, which is fine).

---

## 3. Writing rules

- **Sentence case** everywhere: titles, headings, labels, buttons. Capitals only for acronyms (MI, MPAN, SECR, ESOS) and proper nouns. Never ALL CAPS labels.
- UK English (organise, colour, licence).
- **Lead with the outcome.** The first paragraph says in one or two sentences what happened and whether the reader needs to act. Example: "11,832 Thames Water reads have been imported to date. 37,733 rows failed and are listed in the attached report."
- If the reader must do something, say exactly what, in a Warning or Error status banner, before any detail.
- One idea per paragraph. Paragraphs of three sentences at most.
- Explain internal terms in plain words, or move them to "About this report" at the bottom (for example "superseded reads are excluded").
- Do not use emoji.

### Numbers, dates and times

- Thousands separators always: `27,674`, not `27674`.
- Right-align every number column in tables.
- Percentages to one decimal place: `55.8%`.
- Money: `£1,234.56`.
- Dates: `DD/MM/YYYY` (`27/10/2026`). Spell out the weekday only when it helps (`Tue 27/10/2026`).
- Times: 24-hour with the zone, in UK local time: `14:30 GMT` or `14:30 BST`. Only use UTC if the process genuinely needs it, and say so.
- Write counts with their unit: "3 meters", "240 rows".

### Subject line

```
{Status prefix: }{Process or data source}: {what this is} | {DD/MM/YYYY}
```

- `Thames Water reads: data imports MI report | 27/10/2026`
- `Action needed: CCL rate capture: 2 rates missing | 27/10/2026`
- `Failed: Sugar to HubSpot sync | 27/10/2026 06:00 GMT`

Status prefix is only used for `Action needed:` or `Failed:`. Routine reports have no prefix. Keep subjects under 80 characters.

### Preview text

Every email sets the hidden preheader (`{{PREHEADER}}`) to a one-sentence summary, 50 to 110 characters. It shows in the Outlook message list. Never leave it empty, otherwise Outlook shows the first text it finds.

---

## 4. Email types

Pick one. It decides which components you use.

| Type | Purpose | Required components | Status banner |
|---|---|---|---|
| **Report** | Scheduled MI or summary | Header, summary, KPI row, at least one table, files, about | Only if something needs attention |
| **Alert** | A process failed or needs a person | Header, status banner (Error or Warning), summary, "What to do" list, details table | Always |
| **Notification** | Something finished or changed | Header, status banner (Success or Info), summary, optional details | Always |
| **Request** | A person must review or approve | Header, status banner (Warning), summary, button, details | Always |

Order of components is always: header, status banner, summary, KPIs, sections (tables or lists), files and links, about this report, then the footer.

---

## 5. Outlook and Graph technical rules

Outlook for Windows renders HTML with the Word engine. Build for it first.

**Must:**

- Use `<table role="presentation">` for all layout, with `cellpadding="0" cellspacing="0" border="0"`.
- Put all styles inline on each element. The `<style>` block in the template is only for resets and Outlook fixes.
- Set widths with the `width` attribute and in `style`.
- Set background colours with both `bgcolor="#..."` and `style="background-color:#..."`.
- Use real `<th scope="col">` for data table headers.
- Set `lang="en-GB"` on `<html>`.
- Keep the total HTML under 100 KB.
- Send with `"body": {"contentType": "HTML", "content": "<html>..."}`.
- Escape all data values (`&`, `<`, `>`) before inserting them. Data from files and databases is untrusted.

**Must not:**

- Use flexbox, CSS grid, `position`, `float`, CSS variables, `calc()`, `@media`-dependent layout, or `<div>`-based layout.
- Use background images, SVG, base64 `data:` images, video, forms or JavaScript.
- Use web fonts.
- Embed large tables. Show at most **15 rows** in any table in the body. Put the rest in the attached file and say how many rows are not shown.
- Paste raw UNC paths or URLs as visible text (see section 6.8).

**Images:** avoid them. If a chart is truly needed, attach a PNG as an inline attachment (`isInline: true`, `contentId`) and reference it with `cid:`, 600px wide, exported at 2x, with meaningful `alt` text that states the key number.

**Attachments:** Graph `sendMail` accepts attachments up to 3 MB in a single request. Larger files (up to 150 MB) need a draft message plus an upload session. If a file is over 10 MB, link to its location instead of attaching it.

**Importance:** set `"importance": "high"` only for the Alert type.

**Dark mode:** Outlook may invert colours. Always set explicit white backgrounds and black or midnight text so inversion stays readable. Do not use colour-only text for meaning.

---

## 6. Components

All components live in the base template between the `CONTENT START` and `CONTENT END` comments. Copy the snippet, keep its inline styles, change only the text and values.

### 6.1 Header

Title and meta line, under the header band.

- The email type is shown in the header band, not here: `Automated report`, `Automated alert`, `Automated notification` or `Action needed`. Fill `{{EMAIL_TYPE}}` with it.
- Title: the name of the report or event, sentence case, no date (the date goes in the meta line).
- Meta line: when it was generated and the data scope. Example: `Generated Tue 27/10/2026 14:30 GMT · All-time read rows`.

### 6.2 Status banner

A full-width tinted box: status word in bold, then one sentence. Maximum one per email.

```
Needs attention  3 meters have had no import in the last 7 days.
```

### 6.3 Summary

One or two short paragraphs of body text that state the outcome. Required in every email.

### 6.4 KPI row

Two to four headline numbers in tiles (`surface` background, 16px padding). Each tile is a value plus a label.

- 4 KPIs: 2 x 2 grid of tiles.
- 2 or 3 KPIs: one row.
- Label is sentence case and short: `Imported`, `Failed matching`.
- An optional third line gives context in small print: `23.9% of all rows`.
- Never more than four. Everything else goes in a table.

### 6.5 Data table

- Full content width (576px).
- Header row: `purple-60` 12px 600 text on white, with a 2px `midnight` bottom border.
- Body rows: 10px vertical padding, 1px `light-grey` bottom border. No zebra stripes, no vertical borders.
- First column left-aligned, number columns right-aligned.
- Group related rows under a group row (600 weight, `purple-60`) rather than making a second table. For example, "Failed matching" groups "Multiple matches" and "No match".
- Optional total row: 600 weight, 2px `midnight` top border.
- If rows were cut: a small-print line under the table: `Showing 15 of 2,341 rows. The full list is in the attached report.`

### 6.6 Bullet or numbered list

For "What to do" steps in Alerts. Use real `<ul>`/`<ol>`, 14px body text, at most 6 items.

### 6.7 Button

Only for the Request type, or when a single link is the main action. One button per email. `midnight` background, white 14px 600 text, 12px x 24px padding, label is a verb phrase: `Review the exceptions`.

### 6.8 Files and links

This replaces long pasted file paths, which wrap badly and are not clickable when they contain spaces.

- Attachments: list file name and size: `ThamesWater_MI_20261027_141648.xlsx (5 MB), attached`.
- Network locations: a short descriptive link, then the full path in a small grey monospace block so it can be copied.
  - Link `href` is the `file:` URL with spaces encoded as `%20`: `file://///inspiredenergysolutions.local/DFS/Public/!IES/BPI/Automation%20Team/...`
  - Visible link text: `Open the report folder`.
  - Path block: `font-family: Consolas, 'Courier New', monospace; font-size: 12px; background: #f6f6f5; padding: 8px 12px; word-break: break-all;`
- Prefer a SharePoint or web link over a UNC path when one exists.

### 6.9 About this report

Small print at the bottom of the content, above the footer. It covers definitions, data scope, exclusions and the schedule. Example: `Counts include all read rows imported since go-live. Superseded reads are excluded. This report runs every weekday at 14:30.`

---

## 7. Checklist before sending

An agent must confirm all of these:

- [ ] Started from `email-base-template.html`; shell, header band and footer untouched; `{{EMAIL_TYPE}}` and `{{PROCESS_NAME}}` filled in.
- [ ] No logo besides the header band's wordmark, no signature, socials or extra "do not reply" text.
- [ ] Subject follows section 3; preheader set.
- [ ] First paragraph states the outcome and any action.
- [ ] Only brand tokens used; status shown as a word as well as a colour.
- [ ] Sentence case; UK English; DD/MM/YYYY; times carry a zone.
- [ ] Numbers have thousands separators and are right-aligned.
- [ ] Tables are 15 rows or fewer; anything cut is flagged.
- [ ] No raw paths or URLs as visible text.
- [ ] No flexbox, grid, divs for layout, web fonts, SVG or base64 images.
- [ ] All data values HTML-escaped.
- [ ] HTML under 100 KB; attachments under 3 MB, or sent via upload session.

---

## 8. Worked example: what changes in the Thames Water MI email

| Before | After |
|---|---|
| Heading with no context; numbers float far from their labels | Header band naming the email type, then title and meta line; KPIs in a 2 x 2 tile grid with labels attached |
| "Fail categories" listed separately with no link to the totals | One table grouping categories under Failed matching (Multiple matches, No match) and Failed validation (Negative read, Negative date, Same day), plus System error, with % of rows |
| "Stale meters: 3" buried in the list | Warning status banner at the top: "3 meters have had no import in the last 7 days." |
| Long UNC path wrapping across the page, half linked | "Open the report folder" link plus a copyable path block |
| "Sent automatically by the DataImports MI report. Please do not reply." | Standard automation footer with the process name |
| UTC time | UK local time with zone |
