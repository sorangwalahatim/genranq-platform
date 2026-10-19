"""Credential vault backend (F-018 / module 48).

Envelope encryption: each secret gets a random 256-bit data key (DEK); the secret is
sealed with AES-256-GCM under the DEK, and the DEK is wrapped with the KEK
(VAULT_MASTER_KEY, held by a KMS in production). Ciphertext lives in the separate
``vault`` schema which the RLS app role cannot read at all; application tables keep
only ``vault_ref`` pointers. Associated data binds every ciphertext to its tenant.
"""
from __future__ import annotations

import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select

from .. import models as m
from ..config import get_settings
from ..db import system_session


def _kek() -> AESGCM:
    return AESGCM(get_settings().vault_kek)


def put(org_id, plaintext: str) -> str:
    ref = "vault:" + secrets.token_hex(16)
    dek = AESGCM.generate_key(bit_length=256)
    n1, n2 = os.urandom(12), os.urandom(12)
    aad = str(org_id).encode()
    wrapped = n1 + _kek().encrypt(n1, dek, aad)
    ct = AESGCM(dek).encrypt(n2, plaintext.encode(), aad)
    with system_session() as db:
        db.add(m.VaultSecret(ref=ref, organisation_id=org_id, wrapped_dek=wrapped, nonce=n2, ciphertext=ct))
    return ref


def get(org_id, ref: str) -> str:
    with system_session() as db:
        row = db.scalar(select(m.VaultSecret).where(m.VaultSecret.ref == ref,
                                                    m.VaultSecret.organisation_id == org_id))
        if not row:
            raise KeyError("secret not found")
        aad = str(org_id).encode()
        dek = _kek().decrypt(row.wrapped_dek[:12], row.wrapped_dek[12:], aad)
        return AESGCM(dek).decrypt(row.nonce, row.ciphertext, aad).decode()


def remove(org_id, ref: str) -> None:
    with system_session() as db:
        db.execute(delete(m.VaultSecret).where(m.VaultSecret.ref == ref, m.VaultSecret.organisation_id == org_id))


def mask(secret: str) -> str:
    if len(secret) <= 4:
        return "•" * len(secret)
    return "•" * 8 + secret[-4:]
