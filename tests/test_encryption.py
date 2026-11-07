import json

import pytest
from cryptography.fernet import Fernet

from app.core.encryption import ConnectionCipher
from app.core.errors import ServiceError


def ring(active="one", **keys):
    return ConnectionCipher(json.dumps(keys), active)


def test_encryption_is_bound_to_record_and_rotation_keeps_plaintext():
    first = Fernet.generate_key().decode()
    second = Fernet.generate_key().decode()
    cipher = ring(one=first)
    token = cipher.encrypt("secret-fixture", workspace_id="w1", connection_id="i1")
    assert "secret-fixture" not in token
    assert cipher.decrypt(token, workspace_id="w1", connection_id="i1") == "secret-fixture"
    for workspace_id, connection_id in (("w2", "i1"), ("w1", "i2")):
        with pytest.raises(ServiceError):
            cipher.decrypt(token, workspace_id=workspace_id, connection_id=connection_id)
    rotated_cipher = ring(active="two", one=first, two=second)
    rotated = rotated_cipher.rotate(token, workspace_id="w1", connection_id="i1")
    assert rotated.startswith("two:")
    only_new = ring(active="two", two=second)
    assert only_new.decrypt(rotated, workspace_id="w1", connection_id="i1") == "secret-fixture"
    with pytest.raises(ServiceError) as error:
        only_new.decrypt(token, workspace_id="w1", connection_id="i1")
    assert error.value.code == "connection_encryption_unavailable"
    assert "secret-fixture" not in str(error.value)


@pytest.mark.parametrize("serialized,active", [("", ""), ("{}", "a"), ('{"a":"bad"}', "a")])
def test_invalid_keyring_fails_closed(serialized, active):
    with pytest.raises(ServiceError) as error:
        ConnectionCipher(serialized, active)
    assert error.value.code == "connection_encryption_unavailable"
