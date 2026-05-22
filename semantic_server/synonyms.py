"""Seed synonym groups. First word of each group is canonical.

Stemmed at import time so callers can look up either stemmed or raw.
Keep groups small and domain-relevant; over-broad groups dilute recall.
"""

DEFAULT_SYNONYMS: list[tuple[str, ...]] = [
    # auth
    ("login", "signin", "signon", "logon", "authenticate"),
    ("logout", "signout", "logoff"),
    ("password", "passwd", "pwd"),
    ("token", "jwt", "bearer"),
    # db
    ("database", "db", "datastore"),
    ("postgres", "postgresql", "psql", "pg"),
    ("sqlite", "sqlite3"),
    # net
    ("request", "req", "http"),
    ("response", "resp"),
    ("endpoint", "route", "url"),
    # build
    ("dependency", "dep", "package", "library"),
    ("config", "configuration", "settings"),
    ("environment", "env"),
    # ops
    ("deploy", "deployment", "release", "ship"),
    ("error", "err", "exception", "failure"),
    ("test", "tests"),
    # storage
    ("cache", "memo", "memoize"),
    ("file", "files", "document"),
    # control
    ("create", "add", "insert", "new"),
    ("delete", "remove", "drop"),
    ("update", "modify", "edit", "change"),
]
