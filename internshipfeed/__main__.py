"""Run the shared ingestion CLI when the package is invoked with python -m."""
from .cli import main

raise SystemExit(main())
