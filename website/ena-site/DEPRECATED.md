# ena.animica.org — RETIRED

The standalone ENA static site (this directory: `index.html` + `ena.js`) has
been **retired** and consolidated into **pool.animica.org**.

Everything the ENA landing site offered now lives on pool.animica.org:

- the **pool dashboard** (live stats, jobs, training pools) — the pool homepage
- **/about-ena** — the "what is ENA / open AI training & inference" overview
- **/training-pools** — collaborative training pools

## What changed

- `ena.animica.org` is now a permanent **301 redirect** to
  `https://pool.animica.org` (see `deploy/ena.animica.org.conf`).
- The static files in this directory are kept **archived for reference** only;
  they are no longer served by nginx.
- Code and docs that previously pointed at `https://ena.animica.org` now point
  at `https://pool.animica.org`.

## What did NOT change

The **ENA coordinator backend still runs** — only the static site was retired.
The coordinator is still started with `animica ena serve` (deployed as
`animica-ena.service`, listening on `127.0.0.1:8791`) and is reached through
pool.animica.org rather than ena.animica.org.
