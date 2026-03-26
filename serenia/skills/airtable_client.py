"""Shared Airtable client for Serenia skills."""

import os

from pyairtable import Api


_api: Api | None = None
_base_id: str | None = None


def get_airtable():
    """Return the Airtable API client and base ID."""
    global _api, _base_id

    if _api is None:
        token = os.environ.get("AIRTABLE_PAT", "")
        _base_id = os.environ.get("AIRTABLE_BASE_ID", "")

        if not token or not _base_id:
            print("[airtable] WARNING: AIRTABLE_PAT or AIRTABLE_BASE_ID not set")
            return None, None

        _api = Api(token)
        print("[airtable] Airtable client initialized")

    return _api, _base_id


def get_table(table_name: str):
    """Get an Airtable table by name."""
    api, base_id = get_airtable()
    if api is None:
        return None
    return api.table(base_id, table_name)
