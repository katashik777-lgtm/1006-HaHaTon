import hashlib, secrets, hmac

def hash_password(value):
 if len(value)<12: raise ValueError('Пароль должен содержать не менее 12 символов')
 salt=secrets.token_hex(16)
 return salt+':'+hashlib.scrypt(value.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
def verify_password(value, stored):
 salt,digest=stored.split(':')
 return hmac.compare_digest(digest,hashlib.scrypt(value.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex())
def token_hash(value): return hashlib.sha256(value.encode()).hexdigest()
