# PaperAgent Capability Architecture

This document adapts the operating model used by Codex-like agents to
PaperAgent's research domain. The goal is not to copy Codex internals, but to
borrow the useful product patterns: skills as procedural knowledge, tools as
bounded actions, connectors as authenticated long-lived integrations, browser
as a separate web interaction layer, and permission gates around anything with
external effects.

## 1. Core Separation

PaperAgent should keep five capability layers separate:

| Layer | Purpose | Statefulness | Examples |
|---|---|---:|---|
| Skills | How to do research work | Stateless instructions | literature review, reviewer response, experiment design |
| Tools | One-shot executable actions | Stateless per call | PDF read, web search, citation format, chart generation |
| Connectors | Long-lived external sources | Stateful sync | Zotero, email, calendar, local folders |
| Browser | Web exploration and automation | Session-scoped | fetch page, extract HTML, later click/type/download |
| Profiles | Persistent user/project context | Stateful domain memory | language, discipline, research profile, project settings |

The most important rule: skills should not execute external actions, connectors
should not contain research reasoning, and browser automation should not be the
default way to integrate with stable APIs.

## 2. What To Borrow From Codex

Codex-like agents use several patterns that fit PaperAgent well:

1. Progressive disclosure of skills
   - Load only the skills relevant to the current goal.
   - Keep each skill small, scoped, and triggerable.
   - Allow project-level skills to override built-in skills.

2. Tool registry and execution gate
   - Every tool has an ID, schema, permission level, output contract, timeout,
     and provenance.
   - The executor is the only place where tools run.
   - Tool results are structured, not free-form text.

3. Connectors as explicit integrations
   - Long-lived integrations need credentials, sync cursors, cached state, and
     normalized events.
   - Connectors should ingest into domain state rather than directly changing
     drafts.

4. Browser as a special high-risk capability
   - Search/fetch/extract is low-risk read-only web access.
   - Click/type/download/login/submit are higher-risk browser automation
     actions and need stricter permissions.

5. Artifacts and provenance
   - Drafts, evidence cards, references, figures, rendered PDFs, and reports
     should be first-class artifacts.
   - Each artifact should know which sources, tools, and snapshots produced it.

6. Permission levels
   - Local reads and writes are low risk.
   - Network reads are allowed but logged.
   - Downloads, external writes, login, email sending, and submissions require
     explicit approval.
   - Destructive actions are denied unless a future confirmed workflow supports
     them.

## 3. Profiles: The Missing Backbone

To support Chinese/English switching, multiple disciplines, and multiple
research scenarios, PaperAgent needs profiles before it needs many more tools.

Recommended domain models:

```text
domain/
  research_profile.py       # user's long-term research identity
  language_profile.py       # zh/en/bilingual writing and UI preferences
  discipline_profile.py     # discipline rules and terminology
  project_profile.py        # current paper/grant/thesis/experiment context
  source_snapshot.py        # web and file provenance
  imported_file.py          # normalized local/Zotero/imported documents
  opportunity.py            # journal CFP, conference CFP, grant notice
  experiment.py             # experiment plan, variables, protocol, results
```

Minimum fields:

```text
UserResearchProfile
  name, affiliation, roles
  disciplines, keywords, methods
  publications, projects, target journals, target conferences
  preferred languages, writing style, citation styles
  advisor/team constraints

LanguageProfile
  ui_language: zh | en
  writing_language: zh | en | bilingual
  terminology_policy: translate | preserve | bilingual_glossary
  style: journal | thesis | grant | conference | plain

DisciplineProfile
  discipline_id
  theories, methods, datasets, instruments
  common artifact structures
  terminology glossary
  quality checks
  preferred figure/table/formula conventions

ProjectProfile
  project_id, type
  language_profile, discipline_profile
  target venue/funder/school
  deadlines
  sources, references, evidence cards
  drafts, figures, formulas, tasks
```

## 4. Skills Library

Skills should encode research method knowledge. They should be YAML, versioned,
small, and selectable by trigger/scope.

```text
skills/library/
  common/
    academic_writing.yaml
    bilingual_academic_style.yaml
    literature_review.yaml
    evidence_synthesis.yaml
    citation_integrity.yaml
    source_credibility.yaml

  scenario/
    journal_submission.yaml
    conference_submission.yaml
    grant_application.yaml
    grant_midterm_report.yaml
    thesis_planning.yaml
    thesis_defense.yaml
    experiment_design.yaml
    reviewer_response.yaml
    advisor_feedback.yaml
    research_design.yaml
    methodology.yaml
    paper_revision.yaml

  discipline/
    management.yaml
    computer_science.yaml
    medicine.yaml
    education.yaml
    economics.yaml
    materials.yaml
```

Important skill families:

- Language skills: bilingual writing, Chinese academic style, English academic
  style, terminology consistency.
- Scenario skills: journal, conference, grant, thesis, experiment.
- Discipline skills: field-specific methods, standards, formulas, figures, and
  evaluation criteria.
- Revision skills: advisor feedback, reviewer response, structured revision,
  similarity-aware rewriting.

Project skills should override built-ins when the same ID exists. This lets a
student encode advisor-specific rules without changing the global library.

## 5. Tools Library

Tools are executable capabilities. Each tool should be deterministic when
possible, testable offline, and permission-gated.

```text
tools/
  web.py
    web_search
    page_fetch
    html_extract
    source_snapshot

  importers.py
    file_import
    pdf_import
    docx_import
    markdown_import
    bibtex_import
    ris_import

  references.py
    reference_resolve
    citation_format
    bibliography_render
    doi_lookup

  evidence.py
    evidence_extract
    evidence_synthesis
    claim_extractor
    source_quote_verify

  writing.py
    document_write
    structured_edit
    outline_builder
    draft_render
    bilingual_rewrite

  formulas.py
    formula_write
    formula_check
    symbol_table_builder
    latex_render

  figures.py
    chart_generate
    diagram_generate
    figure_caption
    figure_quality_check
    poster_layout

  data_analysis.py
    python_analysis
    table_clean
    statistical_test
    regression
    qualitative_coding_assist

  quality.py
    citation_check
    evidence_check
    method_check
    language_check
    submission_check
    thesis_check
```

Tool output must include:

```text
status
data
errors
warnings
provenance
source_snapshots
latency_ms
```

## 6. Connectors Library

Connectors ingest external systems into normalized PaperAgent state. They should
never silently modify external systems.

```text
connectors/
  base.py
  local_folder.py
  zotero.py
  email.py
  calendar.py
  openalex.py
  crossref.py
  semantic_scholar.py
  google_drive.py
  onedrive.py
  dropbox.py
  overleaf.py
  obsidian.py
  notion.py
```

Priority:

1. LocalFolderConnector
   - Watch PDFs, DOCX, Markdown, BibTeX, RIS, data files.
   - Emit file import events.

2. ZoteroConnector
   - Sync items, collections, tags, notes, PDFs, and BibTeX metadata.
   - Maintain cursor/cache.

3. EmailConnector
   - Parse submission decisions, reviewer comments, advisor feedback, CFPs,
     grant notices, school notices.
   - Early version should parse email only, not log into submission systems.

4. CalendarConnector
   - Sync deadlines, meetings, conference dates, submission milestones.

5. OpenAlex/Crossref/SemanticScholar connectors
   - Maintain metadata cache and quotas.
   - Power reference resolution and literature radar.

## 7. Browser Layer

Browser should be two-stage.

MVP read-only browser-like layer:

```text
browser/
  web_search
  page_fetch
  html_extract
  source_snapshot
  robots_policy
  rate_limiter
  url_dedup
```

Supports:

- Grant discovery
- Journal CFP radar
- Conference CFP radar
- School/department notice tracking
- Journal author guideline extraction
- Policy document search

Later browser automation:

```text
browser_automation/
  browser_open
  browser_click
  browser_type
  browser_extract
  browser_screenshot
  browser_download
  browser_session_state
```

Automation must be permission-gated:

| Permission | Examples |
|---|---|
| network_read | search, fetch, extract |
| external_download | download PDF or guideline file |
| external_login | log into journal/funder/site |
| external_write | fill profile, send email, modify external calendar |
| external_submit | submit manuscript, submit grant, submit form |
| destructive_external | delete, withdraw, cancel |

## 8. Multilingual Support

Chinese/English support should be both product-level and research-level:

- UI language: menus, labels, system messages.
- Writing language: Chinese, English, bilingual.
- Citation style: APA, GB/T 7714, Chicago, IEEE, Vancouver.
- Terminology policy: preserve original term, translate term, bilingual glossary.
- Output conventions: Chinese thesis, English journal article, bilingual abstract.

Add:

```text
skills/library/common/bilingual_academic_style.yaml
tools/writing.py::bilingual_rewrite
tools/references.py::citation_format(style="gbt7714")
domain/language_profile.py
```

## 9. Formula Support

Formula support should not be only text generation.

Capabilities:

- Write LaTeX formulas.
- Build a symbol table.
- Check repeated symbols and undefined variables.
- Check units and dimensions when available.
- Render to Markdown, LaTeX, DOCX, and PDF targets.
- Link formulas to paragraphs, figures, and derivation notes.

Suggested artifacts:

```text
FormulaArtifact
  formula_id
  latex
  display_name
  symbols
  assumptions
  source_or_derivation
  used_in_sections
```

## 10. Figure And Diagram Support

Figure support should split data charts from conceptual diagrams.

Capabilities:

- Statistical charts from data.
- Conceptual framework diagrams.
- Technical route diagrams.
- Experiment workflow diagrams.
- System architecture diagrams.
- Poster and presentation figures.
- Caption and figure-list generation.
- Resolution, font, color, and journal compliance checks.

Recommended tools:

```text
chart_generate       # matplotlib/plotly style statistical charts
diagram_generate     # Mermaid/Graphviz/Draw.io-like diagrams
figure_caption
figure_quality_check
figure_export
```

Recommended artifacts:

```text
FigureArtifact
  figure_id
  kind
  source_data
  code_or_spec
  image_path
  caption
  provenance
  target_requirements
```

## 11. Opportunity Radar

PaperAgent should support opportunities across journals, conferences, grants,
and school notices.

```text
Opportunity
  opportunity_id
  type: journal_cfp | conference_cfp | grant | school_notice | policy
  title
  source_url
  source_snapshot_id
  deadline
  eligibility
  discipline_tags
  keywords
  match_score
  recommended_action
```

Sources:

- Web search/fetch/extract.
- Email connector.
- Calendar connector.
- OpenAlex/Crossref/SemanticScholar for journal metadata.
- Local manually added URLs.

## 12. Recommended Implementation Order

1. Profile backbone
   - `LanguageProfile`
   - `DisciplineProfile`
   - `UserResearchProfile`
   - `ProjectProfile`

2. File and reference ingestion
   - `file_import`
   - BibTeX/RIS import
   - `reference_resolve`
   - `LocalFolderConnector`
   - `ZoteroConnector`

3. Web and opportunity radar
   - Extend existing `web_search/page_fetch/html_extract`
   - Add `Opportunity`
   - Add journal/conference/grant notice extraction

4. Writing and revision
   - `structured_edit`
   - `document_write`
   - `citation_format`
   - `draft_render`
   - reviewer/advisor revision workflows

5. Discipline and scenario expansion
   - thesis, conference, experiment, grant midterm
   - management/computer science/medicine/education/economics/materials

6. Formula and figures
   - `formula_check`
   - `symbol_table_builder`
   - `chart_generate`
   - `diagram_generate`

7. Browser automation
   - Only after permission model, artifacts, and audit logging are mature.

## 13. Design Principle

PaperAgent should not be a pile of prompts. It should be a research operating
system:

- Profiles tell it who the researcher is.
- Projects tell it what the current work is.
- Skills tell it how to think.
- Tools let it act safely.
- Connectors keep it synchronized with the researcher's world.
- Browser lets it inspect the web.
- Kernels and permission gates keep outputs auditable and safe.
