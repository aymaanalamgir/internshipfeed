"""Run the browser regression suite on disposable local PostgreSQL without using deployment credentials."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pgserver

ROOT = Path(__file__).resolve().parent.parent
checks = ['check_pagination_ui.py', 'check_apply_ui.py', 'check_recommendations_ui.py',
          'check_cache_ui.py', 'check_sessions_ui.py']
with tempfile.TemporaryDirectory(prefix='internshipfeed-browser-') as directory:
    server = pgserver.get_server(Path(directory) / 'postgres', cleanup_mode='stop')
    try:
        environment = dict(os.environ, TEST_DATABASE_URL=server.get_uri(), DATABASE_URL=server.get_uri())
        for check in checks:
            subprocess.run([sys.executable, str(ROOT / 'scripts' / check)], cwd=ROOT, env=environment, check=True)
    finally:
        server.cleanup()
