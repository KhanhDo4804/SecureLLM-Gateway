"""KMS demo trên Windows. DPAPI buộc khóa của mỗi run với tài khoản Windows hiện hành."""
import json
from pathlib import Path
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import win32crypt


AAD = b"SecureLLM-Gateway/M2/F/mappings-v1"
UI_FORBIDDEN = 1


def load_mappings(vault: Path):
    wrapped = (vault / "key.dpapi").read_bytes()
    _, key = win32crypt.CryptUnprotectData(wrapped, None, None, None, UI_FORBIDDEN)
    blob = (vault / "mappings.aesgcm").read_bytes()
    return json.loads(AESGCM(key).decrypt(blob[:12], blob[12:], AAD))


def save_and_check(vault, mappings):
    vault.mkdir()
    key = AESGCM.generate_key(bit_length=256)
    wrapped = win32crypt.CryptProtectData(key, "M2 release F", None, None, None, UI_FORBIDDEN)
    (vault / "key.dpapi").write_bytes(wrapped)
    nonce = secrets.token_bytes(12)
    data = json.dumps(mappings, ensure_ascii=False).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, data, AAD)
    path = vault / "mappings.aesgcm"
    path.write_bytes(nonce + ciphertext)

    restored = load_mappings(vault)
    if restored != mappings:
        raise ValueError("Vault round-trip changed the mappings.")
    blob = path.read_bytes()
    try:
        AESGCM(AESGCM.generate_key(bit_length=256)).decrypt(blob[:12], blob[12:], AAD)
    except InvalidTag:
        return restored
    raise ValueError("Wrong AES key unexpectedly decrypted the vault.")
