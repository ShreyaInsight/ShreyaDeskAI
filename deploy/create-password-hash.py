#!/usr/bin/env python3
import base64
import getpass
import hashlib
import secrets

password = getpass.getpass("Dashboard password: ")
confirmation = getpass.getpass("Repeat dashboard password: ")
if not password or password != confirmation:
    raise SystemExit("Passwords are empty or do not match.")
salt = secrets.token_bytes(16)
digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
encode = lambda value: base64.urlsafe_b64encode(value).decode()
print(f"SHREYADESK_PASSWORD_HASH={encode(salt)}${encode(digest)}")