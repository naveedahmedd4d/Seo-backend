import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import settings


def get_encryption_key() -> bytes:
    key = hashlib.sha256(settings.secret_key.encode()).digest()
    return base64.urlsafe_b64encode(key)


def encrypt_secret(value: str) -> str:
    f = Fernet(get_encryption_key())
    return f.encrypt(value.encode()).decode()


def decrypt_secret(encrypted_value: str) -> str:
    f = Fernet(get_encryption_key())
    return f.decrypt(encrypted_value.encode()).decode()
