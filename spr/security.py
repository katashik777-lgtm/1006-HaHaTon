import hashlib, secrets, hmac

def hash_password(value, *, minimum_length=12):
 if len(value)<minimum_length: raise ValueError(f'Пароль должен содержать не менее {minimum_length} символов')
 salt=secrets.token_hex(16)
 return salt+':'+hashlib.scrypt(value.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
def verify_password(value, stored):
 salt,digest=stored.split(':')
 return hmac.compare_digest(digest,hashlib.scrypt(value.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex())
def token_hash(value): return hashlib.sha256(value.encode()).hexdigest()
