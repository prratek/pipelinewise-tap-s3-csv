"""
Tap configuration related stuff
"""
from voluptuous import Schema, Required, Optional

CONFIG_CONTRACT = Schema([{
    Required('table_name'): str,
    Required('search_pattern'): str,
    Optional('key_properties'): [str],
    Optional('search_prefix'): str,
    Optional('date_overrides'): [str],
    Optional('delimiter'): str,
    Optional('quotechar'): str,
    Optional('escapechar'): str,
    Optional('infer_schema', default='true'): str,
    Optional('keys'): [str] # Keys must be in order I guess. For the sake of my sanity, we're gonna say all of these will always be strings. We'll post process if we need to that is the beauty of dbt (:
}])
