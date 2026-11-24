# src/shared/config_loader.py
# ---------------------------------------------------------------
# YAML config loaders for the config/ directory.
# Only files with live consumers have loaders:
#   schedules.yaml -> scripts/scheduler.py
#   pipelines.yaml -> src/pipelines/dim/bloomberg/security/run.py
# Series/universe definitions live in series_registry (seeded from
# src/seeds/), not in YAML.

from pathlib import Path
import yaml

from src.shared.paths import CONFIG_DIR

def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f'Missing config file: {path}')
    with open(path, 'r') as f:
        return yaml.safe_load(f)

def load_schedules() -> dict:
    """
    Scheduler job fire times (cron fields per job id)
    """
    return _load_yaml(CONFIG_DIR / 'schedules.yaml')

def jobs_for_machine(jobs_cfg: dict, machine: str) -> list[str]:
    """
    Job ids whose `machine` field names this machine_id (a string, or a
    list for a job that runs on several). Case-insensitive. A job with
    no `machine` is a config error: it would silently run nowhere.
    """
    me = machine.casefold()
    out = []
    for job_id, cfg in jobs_cfg.items():
        owners = cfg.get('machine')
        if not owners:
            raise KeyError(f"Job '{job_id}' has no `machine` in config/schedules.yaml")
        if isinstance(owners, str):
            owners = [owners]
        if me in (str(o).casefold() for o in owners):
            out.append(job_id)
    return out

def load_pipelines() -> dict:
    """
    Specific fields/values for pipelines
    """
    return _load_yaml(CONFIG_DIR / 'pipelines.yaml')
