"""WATTS Command Center.

The shipped dashboard is a static renderer: it runs the digital twin, computes every
panel from that run, and writes one self-contained HTML file with inline SVG. No CDN, no
build step, no JavaScript framework, and no number that was not produced by a run.

The production Command Center described in docs/ARCHITECTURE.md is a Next.js application
fed by WebSockets over the same aggregates; this renderer is its offline equivalent and
the one that ships as reproducible output.
"""
