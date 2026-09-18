"""Report generation: one run, one document, no hand-written numbers."""
from .context import RunContext, VERSION, make_run_context
from .pipeline import PipelineConfig, PipelineResult, run_pipeline
from .render import render_markdown

__all__ = ["RunContext", "VERSION", "make_run_context", "PipelineConfig", "PipelineResult",
           "run_pipeline", "render_markdown"]
