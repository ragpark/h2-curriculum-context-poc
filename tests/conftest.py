"""Database-free tests. Importing app.db does not open a connection (the pool is lazy), so the pure modules can be
imported directly; clock tests stub the meta table."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
