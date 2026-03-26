"""
crypto.py - X25519 key generation and AES-GCM encrypt/decrypt for gps-bridge.

Encryption scheme:
  1. Receiver generates a static X25519 keypair (stored in config).
  2. Sender generates an ephemeral X25519 keypair per message.
  3. ECDH: shared_secret = receiver_static_private * sender_ephemeral_public
  4. HKDF-SHA256 derives a 32-byte AES-256-GCM key from the shared secret.
  5. AES-256-GCM encrypts the plaintext; tag is 16 bytes appended or separate.

Wire format (all binary values are base64-encoded in JSON):
  {
      "ephemeral_pub": "<base64>",   # sender's ephemeral X25519 public key (32 bytes)
      "nonce":         "<base64>",   # AES-GCM nonce (12 bytes)
      "ciphertext":    "<base64>",   # encrypted payload
      "tag":           "<base64>"    # AES-GCM authentication tag (16 bytes)
  }
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HKDF_INFO = b"gps-bridge-v1"
AES_KEY_LENGTH = 32   # AES-256
GCM_NONCE_LENGTH = 12
GCM_TAG_LENGTH = 16


# ---------------------------------------------------------------------------
# Key generation helpers
# ---------------------------------------------------------------------------


def generate_keypair() -> tuple[X25519PrivateKey, X25519PublicKey]:
    """Generate a fresh X25519 keypair."""
    private_key = X25519PrivateKey.generate()
    public_key = private_key.public_key()
    return private_key, public_key


def private_key_to_bytes(private_key: X25519PrivateKey) -> bytes:
    """Serialize a private key to raw 32-byte representation."""
    return private_key.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )


def public_key_to_bytes(public_key: X25519PublicKey) -> bytes:
    """Serialize a public key to raw 32-byte representation."""
    return public_key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)


def private_key_from_bytes(raw: bytes) -> X25519PrivateKey:
    """Load an X25519 private key from raw bytes."""
    return X25519PrivateKey.from_private_bytes(raw)


def public_key_from_bytes(raw: bytes) -> X25519PublicKey:
    """Load an X25519 public key from raw bytes."""
    return X25519PublicKey.from_public_bytes(raw)


def private_key_to_b64(private_key: X25519PrivateKey) -> str:
    """Return the private key as a URL-safe base64 string."""
    return base64.b64encode(private_key_to_bytes(private_key)).decode()


def public_key_to_b64(public_key: X25519PublicKey) -> str:
    """Return the public key as a URL-safe base64 string."""
    return base64.b64encode(public_key_to_bytes(public_key)).decode()


# ---------------------------------------------------------------------------
# ECDH + HKDF key derivation
# ---------------------------------------------------------------------------


def derive_aes_key(
    private_key: X25519PrivateKey,
    peer_public_key: X25519PublicKey,
) -> bytes:
    """
    Perform X25519 ECDH and derive a 32-byte AES-256-GCM key via HKDF-SHA256.

    Args:
        private_key:     Our X25519 private key.
        peer_public_key: The peer's X25519 public key.

    Returns:
        32-byte AES key.
    """
    shared_secret = private_key.exchange(peer_public_key)
    hkdf = HKDF(
        algorithm=SHA256(),
        length=AES_KEY_LENGTH,
        salt=None,
        info=HKDF_INFO,
    )
    return hkdf.derive(shared_secret)


# ---------------------------------------------------------------------------
# Encryption (used by the phone / test sender)
# ---------------------------------------------------------------------------


def encrypt_payload(
    plaintext: bytes,
    receiver_public_key: X25519PublicKey,
) -> dict[str, str]:
    """
    Encrypt *plaintext* for the receiver who holds *receiver_public_key*.

    An ephemeral X25519 keypair is generated for each call so that forward
    secrecy is maintained at the message level.

    Returns a dict ready to be serialised as JSON:
        {
            "ephemeral_pub": "<base64>",
            "nonce":         "<base64>",
            "ciphertext":    "<base64>",
            "tag":           "<base64>"
        }
    """
    ephemeral_private, ephemeral_public = generate_keypair()
    aes_key = derive_aes_key(ephemeral_private, receiver_public_key)

    nonce = os.urandom(GCM_NONCE_LENGTH)
    aesgcm = AESGCM(aes_key)

    # AESGCM.encrypt() returns ciphertext + tag concatenated (tag is last 16 bytes).
    ct_with_tag = aesgcm.encrypt(nonce, plaintext, associated_data=None)
    ciphertext = ct_with_tag[:-GCM_TAG_LENGTH]
    tag = ct_with_tag[-GCM_TAG_LENGTH:]

    return {
        "ephemeral_pub": base64.b64encode(public_key_to_bytes(ephemeral_public)).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
        "tag": base64.b64encode(tag).decode(),
    }


# ---------------------------------------------------------------------------
# Decryption (used by the server)
# ---------------------------------------------------------------------------


def decrypt_payload(
    encrypted: dict[str, str],
    receiver_private_key: X25519PrivateKey,
) -> bytes:
    """
    Decrypt an encrypted payload dict produced by :func:`encrypt_payload`.

    Args:
        encrypted:             Dict with keys ephemeral_pub, nonce, ciphertext, tag.
        receiver_private_key:  Our X25519 private key.

    Returns:
        Decrypted plaintext bytes.

    Raises:
        ValueError:   If required keys are missing or base64 is malformed.
        cryptography.exceptions.InvalidTag: If authentication fails (tampered data).
    """
    required_keys = {"ephemeral_pub", "nonce", "ciphertext", "tag"}
    missing = required_keys - encrypted.keys()
    if missing:
        raise ValueError(f"Encrypted payload missing fields: {missing}")

    try:
        ephemeral_pub_bytes = base64.b64decode(encrypted["ephemeral_pub"])
        nonce = base64.b64decode(encrypted["nonce"])
        ciphertext = base64.b64decode(encrypted["ciphertext"])
        tag = base64.b64decode(encrypted["tag"])
    except Exception as exc:
        raise ValueError(f"Failed to base64-decode payload fields: {exc}") from exc

    try:
        ephemeral_public_key = public_key_from_bytes(ephemeral_pub_bytes)
    except Exception as exc:
        raise ValueError(f"Invalid ephemeral public key: {exc}") from exc

    aes_key = derive_aes_key(receiver_private_key, ephemeral_public_key)

    aesgcm = AESGCM(aes_key)
    # Re-assemble ciphertext+tag as expected by AESGCM.decrypt()
    ct_with_tag = ciphertext + tag
    return aesgcm.decrypt(nonce, ct_with_tag, associated_data=None)
