"""Run the suite against a disposable, actual PostgreSQL cluster."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import pgserver

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

with tempfile.TemporaryDirectory(prefix="internshipfeed-tests-") as directory:
    server = pgserver.get_server(Path(directory) / "postgres", cleanup_mode="stop")
    try:
        os.environ["TEST_DATABASE_URL"] = server.get_uri()
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent.parent / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        server.cleanup()
sys.exit(0 if result.wasSuccessful() else 1)
