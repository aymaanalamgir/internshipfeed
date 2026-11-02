"""Run database tests on an existing server, using per-test disposable schemas."""
import os
import sys
import unittest
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
database_url = os.environ.get('TEST_DATABASE_URL') or dotenv_values(root / '.env').get('DATABASE_URL')
if not database_url:
    raise SystemExit('Set TEST_DATABASE_URL or configure the local DATABASE_URL first.')
os.environ['TEST_DATABASE_URL'] = database_url
print('Testing against existing PostgreSQL; application tables are not modified.')
suite = unittest.defaultTestLoader.discover(str(root / 'tests'))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
