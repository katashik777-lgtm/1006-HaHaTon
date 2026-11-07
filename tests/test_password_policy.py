import hashlib
import pytest
from spr.security import hash_password, verify_password
from test_workflow import system

@pytest.mark.parametrize('password', ['парольдлинный123', 'valid-password-Я', 'valid-password-ё', 'valid-password-Ё', 'valid-password-і', 'valid-password-\u0301\u0430'])
def test_cyrillic_password_rejected(password):
    with pytest.raises(ValueError, match='кириллицу'):
        hash_password(password)

@pytest.mark.parametrize('password', ['Valid-password-123!', 'spaces are allowed', '123456789012'])
def test_password_roundtrip(password):
    assert verify_password(password, hash_password(password))

def test_existing_cyrillic_password_can_still_login():
    password = 'старыйпароль123'
    salt = 'legacy-salt'
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    assert verify_password(password, salt + ':' + digest)

def test_registration_rejects_cyrillic_without_creating_user(system):
    client, _, _, _ = system
    result = client.post('/api/register', json={'name': 'rejected-user', 'password': 'Long-password-ё'})
    assert result.status_code == 422
    assert 'кириллицу' in result.json()['detail']
    assert all(user['name'] != 'rejected-user' for user in client.get('/api/admin/users').json())
    assert client.post('/api/register', json={'name': 'rejected-user', 'password': 'Long-password-123'}).status_code == 200
