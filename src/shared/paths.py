# src/shared/paths.py
# ---------------------------------------------------------------
# Project path constants: resolves the data, config, seed and schema
# directories from the environment (defaults under the project root).

from pathlib import Path

# env.py is the single load-point for .env (repo-root anchored, unlike a
# bare load_dotenv() that resolves against the CWD) and its optional()
# treats an empty KEY= from .env.example as unset.
from src.shared.env import optional

# ETL_DIR is the repo root. This file is at <repo>/src/shared/paths.py
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ETL_DIR = Path(__file__).resolve().parents[2]

# Main env dirs. DATA_DIR defaults to /data sibling of /<repo>,
# overridden via .env when data lives elsewhere.
DATA_DIR = Path(optional('DATA_DIR', str(PROJECT_ROOT / 'data')))

#ETL paths
CONFIG_DIR = ETL_DIR / 'config'
SEED_DIR = ETL_DIR / 'src' / 'seeds'
SCHEMA_DIR = ETL_DIR / 'src' / 'db' / 'schema'

#DATA paths
RAW_DIR = DATA_DIR / 'raw'
STAGING_DIR = DATA_DIR / 'staging'
#SEEDS_DIR = DATA_DIR / 'seeds'
LOGS_DIR = DATA_DIR / 'logs'
