import json
import re

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.errors import ServiceError


def configuration_error() -> ServiceError:
    return ServiceError(
        "connection_encryption_unavailable",
        "Connection encryption is unavailable; contact the operator",
        503,
    )


class ConnectionCipher:
    def __init__(self, serialized_keys: str, active_id: str):
        try:
            keys = json.loads(serialized_keys)
            if not isinstance(keys, dict) or not keys or active_id not in keys:
                raise ValueError
            if any(
                not isinstance(key_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_id)
                or not isinstance(value, str)
                for key_id, value in keys.items()
            ):
                raise ValueError
            ordered_ids = [active_id, *sorted(set(keys) - {active_id})]
            self._fernets = {key_id: Fernet(keys[key_id].encode()) for key_id in ordered_ids}
            self._ring = MultiFernet(list(self._fernets.values()))
            self.active_id = active_id
        except (TypeError, ValueError, UnicodeError):
            raise configuration_error() from None

    def encrypt(self, secret: str, *, workspace_id: str, connection_id: str) -> str:
        payload = json.dumps(
            {
                "version": 1,
                "workspace_id": workspace_id,
                "connection_id": connection_id,
                "secret": secret,
            },
            separators=(",", ":"),
        ).encode()
        token = self._fernets[self.active_id].encrypt(payload).decode()
        return f"{self.active_id}:{token}"

    def decrypt(self, ciphertext: str, *, workspace_id: str, connection_id: str) -> str:
        try:
            key_id, token = ciphertext.split(":", 1)
            if key_id not in self._fernets:
                raise ValueError
            payload = json.loads(self._ring.decrypt(token.encode()))
            if (
                payload.get("version") != 1
                or payload.get("workspace_id") != workspace_id
                or payload.get("connection_id") != connection_id
                or not isinstance(payload.get("secret"), str)
            ):
                raise ValueError
            return payload["secret"]
        except (InvalidToken, TypeError, ValueError, AttributeError, UnicodeError):
            raise configuration_error() from None

    def rotate(self, ciphertext: str, *, workspace_id: str, connection_id: str) -> str:
        self.decrypt(ciphertext, workspace_id=workspace_id, connection_id=connection_id)
        try:
            token = ciphertext.split(":", 1)[1]
            return f"{self.active_id}:{self._ring.rotate(token.encode()).decode()}"
        except (InvalidToken, ValueError, UnicodeError):
            raise configuration_error() from None
