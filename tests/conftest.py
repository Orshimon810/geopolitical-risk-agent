import sys
import os

# Make the evaluation/ package importable from tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evaluation"))
