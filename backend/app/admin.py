"""Operator CLI.

    python -m app.admin grant-platform-admin <email> <org-slug>
    python -m app.admin revoke-platform-admin <email> <org-slug>
"""
import sys

from sqlalchemy import select

from . import models as m
from .db import system_session


def set_pa(email: str, slug: str, value: bool):
    with system_session() as db:
        u = db.scalar(select(m.User).join(m.Organisation).where(m.User.email == email, m.Organisation.slug == slug))
        if not u:
            sys.exit(f"No user {email} in organisation {slug}")
        u.is_platform_admin = value
        db.add(m.ActivityLog(organisation_id=u.organisation_id, action="platform_admin." + ("granted" if value else "revoked"),
                             target=f"user:{u.id}"))
    print(("granted" if value else "revoked"), email, slug)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("grant-platform-admin", "revoke-platform-admin"):
        sys.exit(__doc__)
    set_pa(sys.argv[2], sys.argv[3], sys.argv[1].startswith("grant"))
