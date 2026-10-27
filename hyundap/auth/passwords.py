"""auth/passwords.py — scrypt 비밀번호 해시 생성·검증 (SPEC-AUTH-001 M1).

담당: 이성민. 표준 라이브러리(hashlib.scrypt, secrets, hmac)만 사용한다.
저장 형식은 scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>이다.
비밀번호·해시는 로그·예외 메시지에 넣지 않는다.
"""

import base64
import binascii
import hashlib
import hmac
import re
import secrets

SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32

# 기본 인자(D1)로 한 번 생성해 붙여 넣은 상수다. import 시 scrypt를 실행하지 않는다.
# 기본 인자를 바꾸면 반드시 다시 생성해야 하며, 그 전까지 AC-005 테스트가 실패한다.
_DUMMY_HASH = 'scrypt$32768$8$1$mFuP1bkRwJuB/8aUzlEqyA==$zjux4ss++OsYXwDEaJQFz08vvt37m9fvP8qYS6bFKcE='

_FORMAT = re.compile(r'scrypt\$(\d{1,7})\$(\d{1,2})\$(\d{1,2})\$([A-Za-z0-9+/=]+)\$([A-Za-z0-9+/=]+)',
                     re.ASCII)
_ALLOWED_N = (2 ** 14, 2 ** 15, 2 ** 16)


def _maxmem(n, r, p):
    """scrypt 필요 메모리에 32 MiB 여유를 더한 maxmem 값을 반환한다(기본값 거부 방지)."""
    return 128 * r * (n + 2) + 128 * r * p + 32 * 1024 * 1024


def _scrypt(password, salt, n, r, p):
    """모든 scrypt 호출은 이 함수를 거치며 maxmem을 명시한다."""
    return hashlib.scrypt(password, salt=salt, n=n, r=r, p=p, maxmem=_maxmem(n, r, p),
                          dklen=KEY_BYTES)


def _encode(password):
    """비밀번호 문자열을 UTF-8 바이트로 바꾼다. 잘못된 surrogate도 예외 없이 처리한다."""
    return password.encode('utf-8', 'surrogatepass')


def _parse(stored):
    """저장 해시를 (n, r, p, salt, key)로 해석한다. 형식·범위를 벗어나면 None이다.

    scrypt를 호출하기 전에 모든 경계를 검사해 손상된 행이 큰 메모리를 요구하지 못하게 한다.
    """
    if not isinstance(stored, str) or len(stored) > 255:
        return None
    match = _FORMAT.fullmatch(stored)
    if match is None:
        return None
    n, r, p = int(match.group(1)), int(match.group(2)), int(match.group(3))
    # OpenSSL은 n >= 2^(16·r)을 maxmem과 무관하게 거부하므로 미리 제외한다.
    if n not in _ALLOWED_N or not 1 <= r <= 8 or not 1 <= p <= 2 or n >= 2 ** (16 * r):
        return None
    try:
        salt = base64.b64decode(match.group(4), validate=True)
        key = base64.b64decode(match.group(5), validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(salt) < SALT_BYTES or len(key) != KEY_BYTES:
        return None
    return n, r, p, salt, key


_DUMMY_PARAMS = _parse(_DUMMY_HASH)


def _dummy_verify(password):
    """사용 가능한 저장 해시가 없을 때 실제 검증과 같은 비용의 scrypt를 1회 실행한다."""
    n, r, p, salt, key = _DUMMY_PARAMS
    secret = _encode(password) if isinstance(password, str) else b''
    try:
        hmac.compare_digest(_scrypt(secret, salt, n, r, p), key)
    except ValueError:
        pass


def hash_password(password):
    """비밀번호 문자열을 기본 인자의 scrypt 저장 형식 문자열로 반환한다."""
    if not isinstance(password, str):
        raise TypeError('password must be a string')
    salt = secrets.token_bytes(SALT_BYTES)
    key = _scrypt(_encode(password), salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return 'scrypt${}${}${}${}${}'.format(SCRYPT_N, SCRYPT_R, SCRYPT_P,
                                          base64.b64encode(salt).decode('ascii'),
                                          base64.b64encode(key).decode('ascii'))


def verify_password(password, stored):
    """비밀번호가 저장 해시와 일치하면 True, 그 밖의 모든 경우 False다. 예외를 내지 않는다.

    NULL·손상·범위 밖 해시는 더미 검증 1회 후 False다. 범위 안 해시의 scrypt가
    ValueError를 내면 실패한 호출 뒤 더미 검증 1회를 더 하고 False다.
    """
    params = _parse(stored)
    if params is None or not isinstance(password, str):
        _dummy_verify(password)
        return False
    n, r, p, salt, key = params
    try:
        actual = _scrypt(_encode(password), salt, n, r, p)
    except ValueError:
        _dummy_verify(password)
        return False
    return hmac.compare_digest(actual, key)
