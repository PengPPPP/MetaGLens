"""MetaGLens: reproducible shotgun-metagenomics pipeline orchestrator.

Turns the MetaGLens skill bundle into a self-contained command-line tool. It
collects a project configuration, discovers paired samples, renders the bundled
shell templates into runnable stage scripts, and drives them to completion with
resumable state tracking.
"""

__version__ = "1.0.0"
