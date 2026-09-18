# src/shared/env.py
# ---------------------------------------------------------------
# Single load-point for the project's .env file, and the accessor
# every secret must go through.
#
# The .env path is derived from THIS FILE's location, not from the
# current working directory. That matters: load_dotenv('.env') with
# a relative path resolves against the CWD, so running a script from
# anywhere but the repo root silently loads nothing - and any
# os.getenv fallback then wins without a word. Secrets must never
# have a fallback, so required() raises instead.
#
# .env is git-ignored. .env.example is the committed template that
# declares the key names with no values.
# ---------------------------------------------------------------

import os
from pathlib import Path

from dotenv import load_dotenv

# src/shared/env.py -> parents[2] is the repo root.
# NOTE: deliberately not reusing PROJECT_ROOT from src.shared.paths -
# that one uses parents[3] and resolves above the repo.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / '.env'

load_dotenv(ENV_FILE)


class MissingSecret(RuntimeError):
    """Raised when a required environment value is absent or empty."""


def required(key: str) -> str:
    """
    Reads a required value from the environment. Never falls back to a
    default - a missing secret must fail loudly, at the point of use,
    with a message that says how to fix it.

    :param key: Environment variable name, e.g. 'PG_PASSWORD'
    :type key: str
    :return: The value
    :rtype: str
    :raises MissingSecret: If the key is unset or empty
    """
    val = os.getenv(key)
    if not val:
        # The pointer must name a file that actually travels with the
        # project: ROTATION.md is git-ignored, so on a machine set up
        # from the repo zip this message used to send the operator to a
        # file that does not exist - as the very first error they see.
        guia = ('See ROTATION.md.' if (PROJECT_ROOT / 'ROTATION.md').exists()
                else 'See the comments in .env.example and INSTALACION.md.')
        raise MissingSecret(
            f'{key} is not set. Copy .env.example to .env at the project root '
            f'({PROJECT_ROOT}) and fill in {key}. {guia}'
        )
    return val


def optional(key: str, default: str | None = None) -> str | None:
    """
    Reads a non-secret value that has a safe default (a port, a flag).
    Never use this for a credential - use required().

    An EMPTY value counts as unset on purpose: .env.example ships every
    key blank (KEY=), and os.getenv's own default would lose to that
    empty string - which once turned every data path CWD-relative.

    :param key: Environment variable name
    :type key: str
    :param default: Value to use when the key is unset or empty
    :type default: str | None
    :return: The value or the default
    :rtype: str | None
    """
    return os.getenv(key) or default
