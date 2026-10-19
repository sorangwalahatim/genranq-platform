from __future__ import annotations

from decimal import Decimal

from sqlalchemy import inspect


def to_dict(obj, exclude: tuple = (), extra: dict | None = None) -> dict:
    out = {}
    for c in inspect(obj).mapper.column_attrs:
        if c.key in exclude:
            continue
        v = getattr(obj, c.key)
        if isinstance(v, Decimal):
            v = float(v)
        out[c.key] = v
    if extra:
        out.update(extra)
    return out


def rows(objs, exclude: tuple = ()) -> list[dict]:
    return [to_dict(o, exclude) for o in objs]
