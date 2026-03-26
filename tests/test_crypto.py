"""
test_crypto.py - Unit tests for gps_bridge.crypto

Tests cover:
  - Keypair generation
  - Key serialisation round-trips
  - AES key derivation (ECDH + HKDF)
  - encrypt_payload / decrypt_payload round-trip
  - Error handling (missing fields, tampered ciphertext, bad tag)
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.exceptions import InvalidTag

from gps_bridge.crypto import (
    decrypt_payload,
    derive_aes_key,
    encrypt_payload,
    generate_keypair,
    private_key_from_bytes,
    private_key_to_bytes,
    public_key_from_bytes,
    public_key_to_bytes,
    public_key_to_b64,
    private_key_to_b64,
)


# ---------------------------------------------------------------------------
# Keypair generation
# ---------------------------------------------------------------------------


class TestKeyGeneration:
    def test_generate_keypair_returns_two_distinct_objects(self):
        priv, pub = generate_keypair()
        assert priv is not None
        assert pub is not None

    def test_private_key_raw_bytes_are_32_bytes(self):
        priv, _ = generate_keypair()
        raw = private_key_to_bytes(priv)
        assert len(raw) == 32

    def test_public_key_raw_bytes_are_32_bytes(self):
        _, pub = generate_keypair()
        raw = public_key_to_bytes(pub)
        assert len(raw) == 32

    def test_two_keypairs_have_different_public_keys(self):
        _, pub1 = generate_keypair()
        _, pub2 = generate_keypair()
        assert public_key_to_bytes(pub1) != public_key_to_bytes(pub2)


# ---------------------------------------------------------------------------
# Serialisation round-trips
# ---------------------------------------------------------------------------


class TestKeySerialization:
    def test_private_key_bytes_round_trip(self):
        priv, _ = generate_keypair()
        raw = private_key_to_bytes(priv)
        recovered = private_key_from_bytes(raw)
        assert private_key_to_bytes(recovered) == raw

    def test_public_key_bytes_round_trip(self):
        _, pub = generate_keypair()
        raw = public_key_to_bytes(pub)
        recovered = public_key_from_bytes(raw)
        assert public_key_to_bytes(recovered) == raw

    def test_private_key_b64_is_valid_base64(self):
        priv, _ = generate_keypair()
        b64 = private_key_to_b64(priv)
        decoded = base64.b64decode(b64)
        assert len(decoded) == 32

    def test_public_key_b64_is_valid_base64(self):
        _, pub = generate_keypair()
        b64 = public_key_to_b64(pub)
        decoded = base64.b64decode(b64)
        assert len(decoded) == 32


# ---------------------------------------------------------------------------
# ECDH key derivation
# ---------------------------------------------------------------------------


class TestDeriveAesKey:
    def test_ecdh_is_symmetric(self):
        """ECDH shared secret must be the same from both sides."""
        priv_a, pub_a = generate_keypair()
        priv_b, pub_b = generate_keypair()

        key_ab = derive_aes_key(priv_a, pub_b)
        key_ba = derive_aes_key(priv_b, pub_a)
        assert key_ab == key_ba

    def test_derived_key_is_32_bytes(self):
        priv_a, _ = generate_keypair()
        _, pub_b = generate_keypair()
        key = derive_aes_key(priv_a, pub_b)
        assert len(key) == 32

    def test_different_peers_produce_different_keys(self):
        priv_a, _ = generate_keypair()
        _, pub_b = generate_keypair()
        _, pub_c = generate_keypair()

        key_ab = derive_aes_key(priv_a, pub_b)
        key_ac = derive_aes_key(priv_a, pub_c)
        assert key_ab != key_ac


# ---------------------------------------------------------------------------
# Encrypt / Decrypt round-trip
# ---------------------------------------------------------------------------


class TestEncryptDecrypt:
    def _make_payload(self) -> bytes:
        return json.dumps(
            {"lat": 51.5074, "lng": -0.1278, "timestamp": "2026-03-26T12:00:00Z"}
        ).encode()

    def test_basic_round_trip(self):
        priv, pub = generate_keypair()
        plaintext = self._make_payload()
        encrypted = encrypt_payload(plaintext, pub)
        recovered = decrypt_payload(encrypted, priv)
        assert recovered == plaintext

    def test_encrypted_dict_has_required_keys(self):
        _, pub = generate_keypair()
        encrypted = encrypt_payload(b"hello", pub)
        assert set(encrypted.keys()) == {"ephemeral_pub", "nonce", "ciphertext", "tag"}

    def test_ephemeral_pub_is_32_bytes_decoded(self):
        _, pub = generate_keypair()
        encrypted = encrypt_payload(b"test", pub)
        raw = base64.b64decode(encrypted["ephemeral_pub"])
        assert len(raw) == 32

    def test_nonce_is_12_bytes_decoded(self):
        _, pub = generate_keypair()
        encrypted = encrypt_payload(b"test", pub)
        raw = base64.b64decode(encrypted["nonce"])
        assert len(raw) == 12

    def test_tag_is_16_bytes_decoded(self):
        _, pub = generate_keypair()
        encrypted = encrypt_payload(b"test", pub)
        raw = base64.b64decode(encrypted["tag"])
        assert len(raw) == 16

    def test_each_encryption_uses_different_ephemeral_key(self):
        _, pub = generate_keypair()
        enc1 = encrypt_payload(b"same plaintext", pub)
        enc2 = encrypt_payload(b"same plaintext", pub)
        assert enc1["ephemeral_pub"] != enc2["ephemeral_pub"]

    def test_each_encryption_uses_different_nonce(self):
        _, pub = generate_keypair()
        enc1 = encrypt_payload(b"same plaintext", pub)
        enc2 = encrypt_payload(b"same plaintext", pub)
        assert enc1["nonce"] != enc2["nonce"]

    def test_wrong_private_key_raises_invalid_tag(self):
        _, pub = generate_keypair()
        wrong_priv, _ = generate_keypair()
        encrypted = encrypt_payload(b"secret", pub)
        with pytest.raises(InvalidTag):
            decrypt_payload(encrypted, wrong_priv)

    def test_tampered_ciphertext_raises_invalid_tag(self):
        priv, pub = generate_keypair()
        plaintext = self._make_payload()
        encrypted = encrypt_payload(plaintext, pub)

        # Flip a byte in the ciphertext
        ct_bytes = bytearray(base64.b64decode(encrypted["ciphertext"]))
        ct_bytes[0] ^= 0xFF
        tampered = dict(encrypted)
        tampered["ciphertext"] = base64.b64encode(bytes(ct_bytes)).decode()

        with pytest.raises(InvalidTag):
            decrypt_payload(tampered, priv)

    def test_tampered_tag_raises_invalid_tag(self):
        priv, pub = generate_keypair()
        encrypted = encrypt_payload(b"data", pub)

        tag_bytes = bytearray(base64.b64decode(encrypted["tag"]))
        tag_bytes[0] ^= 0x01
        tampered = dict(encrypted)
        tampered["tag"] = base64.b64encode(bytes(tag_bytes)).decode()

        with pytest.raises(InvalidTag):
            decrypt_payload(tampered, priv)

    def test_empty_plaintext_round_trip(self):
        priv, pub = generate_keypair()
        encrypted = encrypt_payload(b"", pub)
        recovered = decrypt_payload(encrypted, priv)
        assert recovered == b""

    def test_large_plaintext_round_trip(self):
        priv, pub = generate_keypair()
        plaintext = b"x" * 65536
        encrypted = encrypt_payload(plaintext, pub)
        recovered = decrypt_payload(encrypted, priv)
        assert recovered == plaintext


# ---------------------------------------------------------------------------
# decrypt_payload error handling
# ---------------------------------------------------------------------------


class TestDecryptPayloadErrors:
    def _valid_encrypted(self) -> tuple:
        priv, pub = generate_keypair()
        return priv, encrypt_payload(b"payload", pub)

    def test_missing_ephemeral_pub_raises_value_error(self):
        priv, enc = self._valid_encrypted()
        del enc["ephemeral_pub"]
        with pytest.raises(ValueError, match="missing fields"):
            decrypt_payload(enc, priv)

    def test_missing_nonce_raises_value_error(self):
        priv, enc = self._valid_encrypted()
        del enc["nonce"]
        with pytest.raises(ValueError, match="missing fields"):
            decrypt_payload(enc, priv)

    def test_missing_ciphertext_raises_value_error(self):
        priv, enc = self._valid_encrypted()
        del enc["ciphertext"]
        with pytest.raises(ValueError, match="missing fields"):
            decrypt_payload(enc, priv)

    def test_missing_tag_raises_value_error(self):
        priv, enc = self._valid_encrypted()
        del enc["tag"]
        with pytest.raises(ValueError, match="missing fields"):
            decrypt_payload(enc, priv)

    def test_invalid_base64_in_ephemeral_pub_raises_value_error(self):
        priv, enc = self._valid_encrypted()
        enc["ephemeral_pub"] = "!!!not-base64!!!"
        with pytest.raises(ValueError):
            decrypt_payload(enc, priv)

    def test_invalid_ephemeral_pub_length_raises_value_error(self):
        priv, enc = self._valid_encrypted()
        # A valid base64 string but wrong key length (31 bytes instead of 32)
        enc["ephemeral_pub"] = base64.b64encode(b"\x00" * 31).decode()
        with pytest.raises(ValueError, match="Invalid ephemeral public key"):
            decrypt_payload(enc, priv)
