# Contributing to Animica Docs

Thanks for helping improve the docs! This guide covers **style rules**, how we **test docs**, how to add **diagrams**, and our **PR review** flow. It’s short, practical, and opinionated.

---

## 1) Style rules

### Voice & grammar
- **Audience:** technically proficient builders; assume basic blockchain & dev tooling literacy.
- **Tone:** clear, direct, and friendly. Prefer **active voice** and present tense.
- **English:** American English spelling.
- **Concise:** aim for short paragraphs and scannable sections.
- **Inclusive:** avoid idioms and culturally-specific slang.

### Markdown conventions
- **Headings:** Sentence case. One `# H1` per page (page title).
- **Line width:** soft-wrap at ~100 characters (no hard breaks needed).
- **Lists:** prefer `-` for bullets; use numbered lists for ordered steps.
- **Code spans:** backticks for identifiers and file names (e.g., \`core/types/tx.py\`).
- **Callouts:** use blockquotes for tips/warnings:
  > **Note:** Keep examples minimal and runnable.

### Terminology
- **Product names:** “Animica” (project), **PoIES** (consensus), **DA** (Data Availability), **AICF** (AI Compute Fund).
- **Address format:** bech32m with HRP `anim` unless the context explicitly differs.
- **Chains:** prefer **“Animica mainnet/testnet/localnet”** with exact chain IDs when relevant.

### File & page naming
- **Reference docs / top landings:** `UPPERCASE.mdx` (e.g., `OVERVIEW.mdx`).
- **Articles, guides, blog:** `kebab-case.md(x)` (e.g., `poies-deep-dive.mdx`).
- **Images/diagrams:** `kebab-case.svg/png` under the nearest feature directory.

---

## 2) Code & configuration snippets

### General rules
- Use fenced code blocks with a language tag:
  ```bash
  # good
  animica --version

	•	Prefer copy-pasteable commands. Avoid prompts like $ inside the command unless demonstrating output.
	•	Show inputs and outputs separately. Example:

# command
omni-sdk call --rpc http://127.0.0.1:8545 ...

# sample output (truncated)


	•	Mark non-essential lines with comments rather than interactive prompts.

Language tags we commonly use
	•	bash, sh, json, yaml, toml, python, ts, rust, mermaid.

Security
	•	Never include real tokens, API keys, or private material.
	•	Redact secrets with XXXXXXXXXXXXXXXX or REDACTED.

⸻

3) Diagrams

We accept two approaches:

A) Mermaid (preferred for quick diagrams)
	•	Inline in Markdown using mermaid fences:

flowchart LR
  ZK[Proof Envelope] -->|map| Verifier
  Verifier -->|policy| Decision{Accept?}
  Decision -->|yes| OK[OK]
  Decision -->|no| Fail[Error]


	•	Keep diagrams accessible: add a short caption right below the fence.

B) Static SVG/PNG (for brand-critical or complex diagrams)
	•	Place assets under the nearest doc’s folder or website/src/assets/diagrams/.
	•	Export SVG whenever possible (smaller, crisp).
	•	Ensure color contrast meets WCAG AA (see the a11y checklist).
	•	Provide alt text in Markdown:
![Envelope to Verifier flow (high-level)](./envelope-to-verifier.svg)

⸻

4) Doc tests (what you should run locally)

Even for docs-only PRs, please sanity check:
	1.	Build the website (ensures MD/MDX parse & imports are valid):

# using npm with a subdir
npm --prefix website install
npm --prefix website run build


	2.	Check links (dead link detector):

node website/scripts/check_links.mjs


	3.	Optional: unit tests (site utilities & config):

npm --prefix website run test


	4.	Lint (optional but recommended):
If you have markdownlint/Prettier locally, run them before pushing.

Tip: CI re-runs all of the above and adds Playwright E2E where applicable.

⸻

5) Accessibility checklist (docs)
	•	Headings form a proper outline (h1 → h2 → h3 …).
	•	Alt text for every image/diagram (describe purpose, not pixels).
	•	Color contrast AA+; avoid text embedded in images.
	•	Keyboard: examples and links are navigable; no “click here”—use descriptive labels.

⸻

6) PR review expectations

Scope & quality bar
	•	Prefer small, focused PRs (one topic per PR).
	•	Include before/after screenshots for visual changes, if relevant.
	•	Update cross-references (e.g., docs/TOC.md) when adding/moving pages.

Changelog
	•	For user-facing changes, add a short entry to docs/CHANGELOG.md under [Unreleased].
	•	Docs-only tweaks usually don’t need a new release tag.

Commit messages
	•	Use Conventional Commits where possible:
	•	docs: for doc-only changes
	•	feat: when adding new user-visible surfaces
	•	fix: for corrections
	•	Examples:
	•	docs: add ZKML embedding guide
	•	docs: clarify DA sampling math; fix typos

Branch naming
	•	docs/…, feat/…, fix/…, chore/….

Reviewer checklist
	•	✅ Clear title/description; links to related issues.
	•	✅ Follows style rules; headings/outline make sense.
	•	✅ Code samples are runnable or clearly marked as illustrative.
	•	✅ Diagrams have captions + alt text.
	•	✅ website builds locally; check_links.mjs passes.
	•	✅ Changelog updated if user-facing change.

⸻

7) Where things live
	•	Docs (this folder): high-level philosophy, contribution notes, release notes.
	•	Website content: website/src/docs/ (optional in-repo MDX), blog posts, landing content, components.
	•	Specs: under spec/… (schemas, formats, CDDL/JSON-Schema/OpenRPC).
	•	ZK docs: zk/docs/… (architecture, formats, security, performance, etc.).

⸻

8) Adding or moving pages
	1.	Create the page with proper naming (UPPERCASE.mdx for top-level landings; kebab-case.mdx for guides).
	2.	Link it from the nearest index and update docs/TOC.md.
	3.	If the page should appear on the website:
	•	Place it (or sync it) under website/src/docs/… or add it to the sync script’s allowlist.
	•	Verify npm --prefix website run build.

⸻

9) FAQ

Q: Can I add screenshots?
A: Yes—prefer SVG or high-DPI PNG; crop tightly and add alt text + caption.

Q: Can I reference external blog posts or tweets?
A: Link sparingly; prefer canonical specs and first-party docs. Avoid link rot by quoting a brief context.

Q: How do I propose a large restructure?
A: Open a tracking issue with a one-page proposal (outline + goals) before submitting the PR.

⸻

10) Credits & license

Documentation contributions are accepted under the repository’s license. By submitting a PR, you confirm you have the right to contribute the content.

Thanks again for contributing!
