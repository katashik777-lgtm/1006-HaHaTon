import hashlib, secrets, hmac, unicodedata

def hash_password(value, *, minimum_length=12):
 if any('CYRILLIC' in unicodedata.name(char, '') for char in value): raise ValueError('Пароль не должен содержать кириллицу. Переключите раскладку на английскую')
 if len(value)<minimum_length: raise ValueError(f'РџР°СЂРѕР»СЊ РґРѕР»Р¶РµРЅ СЃРѕРґРµСЂР¶Р°С‚СЊ РЅРµ РјРµРЅРµРµ {minimum_length} СЃРёРјРІРѕР»РѕРІ')
 salt=secrets.token_hex(16)
 return salt+':'+hashlib.scrypt(value.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
def verify_password(value, stored):
 salt,digest=stored.split(':')
 return hmac.compare_digest(digest,hashlib.scrypt(value.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex())
def token_hash(value): return hashlib.sha256(value.encode()).hexdigest()
