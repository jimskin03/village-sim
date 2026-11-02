"""Validate the authored Lantern Bay brain draft without importing it into a village."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from village_sim.cognition.autobiography import validate_pack

if __name__ == "__main__":
    print(validate_pack())
