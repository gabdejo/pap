# src/configs/machine_config.py
# ---------------------------------------------------------------
# Loads machine-specific configuration from
# market_data_config.yaml.
#
# Falls  back to configs/machine_config.yaml if local file is
# not found, but logs a warning since the template has no values.
#
# All public functions are safe to call at import time.
# Config is loaded once and cached via lru_cache.
# ---------------------------------------------------------------

from src.shared.env import optional
from src.shared.logging import setup_logging
from src.shared.paths import CONFIG_DIR

import os
import logging
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_LOCAL_CONFIG = (
    Path(os.path.expandvars('%USERPROFILE%'))
    / 'Documents'
    / 'Tools'
    / 'config'
    / 'market_data_config.yaml'
)
_BASE_CONFIG = CONFIG_DIR / 'machine_config.yaml'

# ---- The .env as machine configuration --------------------------
# market_data_config.yaml lives OUTSIDE the repo, so installing this
# project on another computer meant creating one more file (and three
# nested folders) beyond the .env that is required anyway. These
# variables declare the same thing in the .env, which every install
# already has to write.
#
# Precedence: a value present in the .env wins over the yaml. Empty
# counts as unset (env.optional), and .env.example ships them all
# blank, so a machine that configures itself through the yaml - every
# machine that exists today - behaves exactly as before.
_ENV_A_CLAVE = {
    'MACHINE_ID': 'machine_id',
    'BLOOMBERG_ENABLED': 'bloomberg_enabled',
    'FMS_ENABLED': 'fms_enabled',
    'SCRAPER_ENABLED': 'scraper_enabled',
    'AUTOMATED_SCRAPER_ENABLED': 'automated_scraper_enabled',
    'CHROMEDRIVER_PATH': 'chromedriver_path',
    'TIMEZONE': 'timezone',
}
_BANDERAS = frozenset({'bloomberg_enabled', 'fms_enabled',
                       'scraper_enabled', 'automated_scraper_enabled'})


def _bandera(texto: str) -> bool:
    """Reads a flag written by a person. A bare bool() would call the
    string 'false' True, which is exactly what someone would type."""
    return str(texto).strip().lower() in ('1', 'true', 'si', 'sí', 'yes', 'on')


def _desde_env() -> dict:
    """Machine settings declared in the .env, if any."""
    fuera = {}
    for var, clave in _ENV_A_CLAVE.items():
        val = optional(var)
        if val is None:
            continue
        fuera[clave] = _bandera(val) if clave in _BANDERAS else val
    return fuera


@lru_cache(maxsize=1)
def get_machine_config() -> dict:
    """
    Loads and caches machine config from the local yaml, with whatever
    the .env declares layered on top.
    Call this to access raw config dict if needed.
    Prefer the typed accessor functions below.
    
    :return: Machine config as dictionary
    :rtype: dict
    """
    desde_env = _desde_env()

    if _LOCAL_CONFIG.exists():
        path = _LOCAL_CONFIG
    else:
        path = _BASE_CONFIG
        # Silent when the .env carries the configuration: on a machine
        # set up that way the yaml is deliberately absent, and warning
        # about it on every start reads as a missing step.
        if not desde_env:
            logger.warning(
                f'{_LOCAL_CONFIG} not found. '
                'Copy config/machine_config.yaml there (or declare MACHINE_ID / '
                'SCRAPER_ENABLED / ... in .env) and fill in values for this '
                'machine before running any pipelines'
            )

    with open(path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    if not isinstance(cfg, dict):
        raise ValueError(f'Invalid machine config at {path} - expected a YAML mapping.')

    cfg.update(desde_env)
    return cfg

# ---- Typed accessors ------------------------------------------

def machine_id() -> str:
    val = get_machine_config().get('machine_id', '')
    if not val:
        logger.warning(
            'machine_id not set in market_data_config.yaml.'
        )
    return val or 'unknown'

def bloomberg_enabled() -> bool:
    return bool(get_machine_config().get('bloomberg_enabled', False))

def scraper_enabled() -> bool:
    return bool(get_machine_config().get('scraper_enabled', False))

def automated_scraper_enabled() -> bool:
    return bool(get_machine_config().get('automated_scraper_enabled', False))

def fms_enabled() -> bool:
    return bool(get_machine_config().get('fms_enabled', False))

def sbs_ingestion_enabled() -> bool:
    """
    True on the machine that ingests SBS files from the network
    share into Postgres (the i7 server). Exactly one machine
    should set this, or the daily ingest runs twice.
    """
    return bool(get_machine_config().get('sbs_ingestion_enabled', False))

def chromedriver_path() -> str | None:
    """
    Returns the absolute path to chromedriver.exe for this machine.
    Expands environmnet variables (e.g. %USERPROFILE%).
    Returns None if not set or scraper is not enabled.
    """
    val = get_machine_config().get('chromedriver_path', '')
    if not val:
        return None
    expanded = os.path.expandvars(val)
    path = Path(expanded)
    if not path.exists():
        logger.warning(
            f'chromedriver_path is set to {expanded} '
            'but the file does not exist. '
            'Download chromedrive.exe and place it at the path.'
        )
    return expanded

def timezone() -> str:
    return get_machine_config().get('timezone', 'America/Lima')

# ---- Capability guards ------------------------------------------

def assert_bloomberg() -> None:
    """
    Raises RuntimeError if Bloomberg is not enabled on this machine.
    Call at the top of any Bloomberg pipeline entry point.
    """
    if not bloomberg_enabled():
        raise RuntimeError(
            f'Bloomberg is not enabled on this machine ({machine_id()}). '
            'Set bloomberg_enabled: true in market_data_config.yaml'
            'and ensure the Bloomberg BLP is installed to access the API.'
        )
    
def assert_fms() -> None:
    """
    Raises RuntimeError if FMS is not enabled on this machine.
    Call at the schedulers that contain FMS pipelines
    """
    if not fms_enabled():
        raise RuntimeError(
            f"FMS is not enabled on this machine ({machine_id()})"
            "Set fms_enabled: true in market_data_config.yaml."
        )
    
def assert_scraper() -> None:
    """
    Raises RuntimeError if the scraper is not enabled on this machine.
    Call at the top of any acquisition script.
    """
    if not scraper_enabled():
        raise RuntimeError(
            f'Scraper is not enabled on this machine ({machine_id()}). '
            'Set SCRAPER_ENABLED=true in .env, or scraper_enabled: true in '
            'market_data_config.yaml.'
        )
    
    driver = chromedriver_path()
    if not driver:
        raise RuntimeError(
            'chromedriver_path is not set in market_data_config.yaml. '
            'Download chromedriver.exe and set the path.'
        )
    
def assert_automated_scraper() -> None:
    """
    Raises RuntimeError if the automatic scraper is not enabled on this machine.
    Call at the top of any automatic acquisition script.
    """
    if not automated_scraper_enabled():
        raise RuntimeError(
            f'Automated scaper is not enabled on this machine ({machine_id()}). '
            'Set AUTOMATED_SCRAPER_ENABLED=true in .env, or '
            'automated_scraper_enabled: true in market_data_config.yaml.'
        )
