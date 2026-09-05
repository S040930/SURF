"""Unified experiment core shared by every registered research template.

The core is dataset-driven: a scoring contract fixes the channels and the legal
score grid for one dataset revision, a dataset adapter audits and materializes
inputs, and a research template owns the sampling plan and the analysis. The
frozen r23 protocol stack is deliberately not imported here.
"""

__all__: list[str] = []
