#!/usr/bin/env python3
"""Render docs/research/WHITEPAPER.md -> a styled PDF via weasyprint.

LaTeX display-math blocks (\\[ ... \\]) are converted to readable Unicode
(weasyprint has no math typesetting). Usage:

    python contrib/docs-templates/render_whitepaper.py \
        docs/research/WHITEPAPER.md /var/www/animica.org/whitepaper.pdf
"""
from __future__ import annotations

import re
import sys

import markdown
from weasyprint import HTML

# LaTeX token -> Unicode. Applied to the body of each \[ ... \] block.
_TOKENS = {
    r"\;": " ", r"\,": " ", r"\quad": "    ", r"\:": " ", r"\!": "",
    r"\sum": "Σ", r"\psi": "ψ", r"\Theta": "Θ", r"\Gamma": "Γ",
    r"\ge": "≥", r"\le": "≤", r"\in": "∈", r"\ln": "ln", r"\Pr": "Pr",
    r"\cdot": "·", r"\times": "×", r"\approx": "≈", r"\to": "→",
}


def _clean_math(body: str) -> str:
    s = body
    s = s.replace(r"\begin{cases}", "").replace(r"\end{cases}", "")
    s = s.replace(r"\\", "\n").replace("&", " ")
    s = re.sub(r"\\text\{([^}]*)\}", r"\1", s)
    for k, v in _TOKENS.items():
        s = s.replace(k, v)
    s = re.sub(r"\^\{([^}]*)\}", r"^(\1)", s)   # x^{...} -> x^(...)
    s = re.sub(r"_\{([^}]*)\}", r"[\1]", s)      # x_{...} -> x[...]
    s = s.replace("-", "−")                       # minus sign
    lines = [ln.strip() for ln in s.splitlines()]
    s = "\n".join(ln for ln in lines if ln)
    return s.strip()


def _sub_display_math(md_text: str) -> str:
    def repl(m: re.Match) -> str:
        return "\n\n<div class=\"math\">" + _clean_math(m.group(1)) + "</div>\n\n"
    return re.sub(r"\\\[(.*?)\\\]", repl, md_text, flags=re.DOTALL)


_CSS = """
@page {
  size: A4; margin: 22mm 20mm 24mm 20mm;
  @bottom-center { content: "Animica Whitepaper — v0.9 Draft"; font-size: 8pt; color: #888; }
  @bottom-right  { content: counter(page) " / " counter(pages); font-size: 8pt; color: #888; }
}
html { font-family: "DejaVu Sans", "Helvetica", sans-serif; font-size: 10.5pt; color: #1a1a1a; line-height: 1.5; }
h1 { font-size: 22pt; color: #0b0b0c; line-height: 1.2; margin: 0 0 4pt; }
h1 + p { color: #555; }
h2 { font-size: 14pt; color: #111; margin-top: 18pt; border-bottom: 1px solid #e3e3e6; padding-bottom: 3pt; }
h3 { font-size: 11.5pt; color: #222; margin-top: 12pt; }
p, li { text-align: justify; }
a { color: #6d28d9; text-decoration: none; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 9pt; background: #f4f4f6; padding: 0.5pt 3pt; border-radius: 3px; }
pre { background: #f4f4f6; padding: 8pt; border-radius: 5px; overflow-x: auto; }
pre code { background: none; padding: 0; }
blockquote { margin: 10pt 0; padding: 8pt 14pt; background: #faf7ff; border-left: 3px solid #6d28d9; color: #2a2a2a; }
hr { border: none; border-top: 1px solid #e3e3e6; margin: 14pt 0; }
.math { font-family: "DejaVu Sans Mono", monospace; font-size: 10pt; text-align: center;
        white-space: pre-wrap; background: #f7f7fb; border: 1px solid #ececf2; border-radius: 5px;
        padding: 10pt; margin: 10pt 0; color: #111; }
ul, ol { margin: 6pt 0 6pt 0; }
.cover-meta { color: #555; font-size: 10pt; }
"""


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else "docs/research/WHITEPAPER.md"
    out = sys.argv[2] if len(sys.argv) > 2 else "whitepaper.pdf"
    with open(src, "r", encoding="utf-8") as f:
        md_text = f.read()
    md_text = _sub_display_math(md_text)
    body = markdown.markdown(
        md_text,
        extensions=["extra", "sane_lists", "smarty", "toc"],
        output_format="html5",
    )
    html = f"<!doctype html><html><head><meta charset='utf-8'>" \
           f"<style>{_CSS}</style></head><body>{body}</body></html>"
    HTML(string=html, base_url=".").write_pdf(out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
