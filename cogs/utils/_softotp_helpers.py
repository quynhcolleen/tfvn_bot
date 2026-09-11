"""Time- and key-version-bound Soft OTP codes for Discord identity proofs."""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from pymongo.errors import DuplicateKeyError, PyMongoError

from cogs._hash_verification import VerificationKeyring


SOFTOTP_COLLECTION = "softotp_issuances"
SOFTOTP_TOKEN_PREFIX = "tfotp1"
SOFTOTP_CODE_LENGTH = 8
MAX_CHALLENGE_LENGTH = 64
MAX_TOKEN_LENGTH = 128
MAX_FUTURE_SKEW_SECONDS = 60
MAX_UNIX_TIMESTAMP = 253_402_300_799
SOFTOTP_MISMATCH_MESSAGE = "OTP không khớp challenge này."
SOFTOTP_ACCOUNT_MISMATCH_MESSAGE = "OTP không khớp với tài khoản đã chọn."
SOFTOTP_KEY_MESSAGE = "OTP không còn hiệu lực vì khóa đã đổi. Hãy lấy mã mới."
SOFTOTP_STORE_MESSAGE = "Không thể lưu Soft OTP lúc này. Hãy thử lại sau."
SOFTOTP_LOOKUP_MESSAGE = "Không thể kiểm tra Soft OTP lúc này. Hãy thử lại sau."

_OTP_DOMAIN = b"tfvn-softotp-code-v2\0"
_LOOKUP_DOMAIN = b"tfvn-softotp-lookup-v2\0"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_LOOKUP = frozenset(_CROCKFORD)
_CROCKFORD_ALIASES = str.maketrans({
    "I": "1",
    "L": "1",
    "O": "0",
})
_TOKEN_PATTERN = re.compile(
    rf"^{SOFTOTP_TOKEN_PREFIX}\."
    r"(?P<kid>[A-Za-z0-9_-]{1,32})\."
    r"(?P<iat>[1-9][0-9]{0,11})\."
    rf"(?P<code>[0-9A-Za-z]{{{SOFTOTP_CODE_LENGTH}}})$"
)
_DOCUMENT_KEYS = frozenset(
    {
        "_id",
        "guild_id",
        "user_id",
        "challenge",
        "kid",
        "issued_at",
        "created_at",
        "updated_at",
    }
)
_MAX_SNOWFLAKE = (1 << 64) - 1
_KID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")


class _IssuanceCollection(Protocol):
    def find_one(self, query: dict[str, Any]) -> Any: ...

    def insert_one(self, document: dict[str, Any]) -> Any: ...

    def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> Any: ...

    def delete_one(self, query: dict[str, Any]) -> Any: ...


class SoftOtpError(ValueError):
    """User-facing Soft OTP validation or verification failure."""


class SoftOtpStoreError(SoftOtpError):
    """An issuance record could not be saved or loaded."""


@dataclass(frozen=True)
class SoftOtpToken:
    """Parsed `tfotp1.<kid>.<unix>.<code>` token."""

    kid: str
    issued_at: int
    code: str

    @property
    def text(self) -> str:
        return (
            f"{SOFTOTP_TOKEN_PREFIX}.{self.kid}.{self.issued_at}.{self.code}"
        )


def normalize_copied_text(value: object) -> str:
    """Strip copy formatting without changing the remaining characters."""
    if not isinstance(value, str):
        raise SoftOtpError("Giá trị Soft OTP không hợp lệ.")
    candidate = value.strip()
    if len(candidate) >= 2 and candidate.startswith("`") and candidate.endswith("`"):
        candidate = candidate[1:-1].strip()
    return candidate


def normalize_challenge(value: object) -> str:
    """Normalize a Google Form challenge into the exact HMAC input."""
    candidate = unicodedata.normalize("NFKC", normalize_copied_text(value))
    candidate = candidate.strip().casefold()
    if not candidate:
        raise SoftOtpError("Challenge không được để trống.")
    if any(ch.isspace() for ch in candidate):
        raise SoftOtpError("Challenge phải là một cụm, không có khoảng trắng.")
    if len(candidate) > MAX_CHALLENGE_LENGTH:
        raise SoftOtpError(f"Challenge tối đa {MAX_CHALLENGE_LENGTH} ký tự.")
    if any(ord(ch) < 32 or ch == "`" for ch in candidate):
        raise SoftOtpError(
            "Challenge không được chứa ký tự điều khiển hoặc dấu nháy ngược."
        )
    return candidate


def _require_snowflake(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Discord ID must be a positive integer.")
    if not 0 < value <= _MAX_SNOWFLAKE:
        raise ValueError("Discord ID must be a positive integer.")
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _unix_seconds(value: datetime) -> int:
    return int(_as_utc(value).timestamp())


def _encode_crockford_40(data: bytes) -> str:
    if len(data) != 5:
        raise ValueError("Soft OTP digest must be 5 bytes.")
    number = int.from_bytes(data, "big")
    chars = ["0"] * SOFTOTP_CODE_LENGTH
    for index in range(SOFTOTP_CODE_LENGTH - 1, -1, -1):
        chars[index] = _CROCKFORD[number & 31]
        number >>= 5
    if number:
        raise ValueError("Soft OTP digest overflow.")
    return "".join(chars)


def normalize_otp_code(value: object) -> str:
    """Canonicalize an 8-character Crockford Soft OTP code."""
    if not isinstance(value, str):
        raise SoftOtpError("Mã OTP không hợp lệ.")
    code = value.strip().translate(_CROCKFORD_ALIASES).upper()
    if len(code) != SOFTOTP_CODE_LENGTH or any(ch not in _CROCKFORD_LOOKUP for ch in code):
        raise SoftOtpError("Mã OTP không hợp lệ.")
    return code


def parse_softotp_token(value: object) -> SoftOtpToken:
    """Parse `tfotp1.<kid>.<unix>.<code>`. Discord IDs are not accepted."""
    candidate = normalize_copied_text(value)
    if not candidate or len(candidate) > MAX_TOKEN_LENGTH:
        raise SoftOtpError("Mã OTP không hợp lệ.")
    match = _TOKEN_PATTERN.fullmatch(candidate)
    if match is None:
        raise SoftOtpError(
            "Mã OTP phải có dạng "
            f"`{SOFTOTP_TOKEN_PREFIX}.<khóa>.<unix>.<8 ký tự>`."
        )
    issued_at = int(match.group("iat"))
    if issued_at > MAX_UNIX_TIMESTAMP:
        raise SoftOtpError("Mã OTP không hợp lệ.")
    return SoftOtpToken(
        kid=match.group("kid"),
        issued_at=issued_at,
        code=normalize_otp_code(match.group("code")),
    )


def format_softotp_token(kid: str, issued_at: int, code: str) -> str:
    """Build the user-facing token from key version, time, and code."""
    if _KID_PATTERN.fullmatch(kid) is None:
        raise SoftOtpError("Mã OTP không hợp lệ.")
    if not 0 < issued_at <= MAX_UNIX_TIMESTAMP:
        raise SoftOtpError("Mã OTP không hợp lệ.")
    return SoftOtpToken(kid, issued_at, normalize_otp_code(code)).text


def compute_otp_code(
    key: bytes,
    guild_id: int,
    user_id: int,
    challenge: str,
    *,
    kid: str,
    issued_at: int,
) -> str:
    """Return the 8-character code for the active key, version, and issue time."""
    payload = (
        f"{kid}:{issued_at}:"
        f"{_require_snowflake(guild_id)}:{_require_snowflake(user_id)}:"
        f"{challenge}"
    ).encode("utf-8")
    digest = hmac.new(key, _OTP_DOMAIN + payload, hashlib.sha256).digest()
    return _encode_crockford_40(digest[:5])


def issue_softotp_token(
    keyring: VerificationKeyring,
    *,
    guild_id: int,
    user_id: int,
    challenge: object,
    issued_at: datetime,
) -> str:
    """Issue an opaque token bound to the current key version and time."""
    if not isinstance(keyring, VerificationKeyring):
        raise SoftOtpError("Tính năng Soft OTP chưa được cấu hình an toàn.")
    normalized = normalize_challenge(challenge)
    issued_unix = _unix_seconds(issued_at)
    code = compute_otp_code(
        keyring.active_key,
        guild_id,
        user_id,
        normalized,
        kid=keyring.active_kid,
        issued_at=issued_unix,
    )
    return format_softotp_token(keyring.active_kid, issued_unix, code)


def issuance_lookup_id(
    guild_id: int,
    challenge: str,
    token: SoftOtpToken,
) -> str:
    """Private registry key for one guild, challenge, and full token."""
    payload = (
        f"{_require_snowflake(guild_id)}\0{challenge}\0{token.kid}\0"
        f"{token.issued_at}\0{token.code}"
    ).encode("utf-8")
    return hashlib.sha256(_LOOKUP_DOMAIN + payload).hexdigest()


def require_active_token(
    keyring: VerificationKeyring,
    token: SoftOtpToken,
    *,
    now: datetime | None = None,
) -> None:
    """Reject OTPs from a previous key version or a future timestamp."""
    if token.kid != keyring.active_kid:
        raise SoftOtpError(SOFTOTP_KEY_MESSAGE)
    current = _unix_seconds(now or datetime.now(timezone.utc))
    if token.issued_at > current + MAX_FUTURE_SKEW_SECONDS:
        raise SoftOtpError("Mã OTP không hợp lệ.")


def otp_code_matches(
    keyring: VerificationKeyring,
    *,
    guild_id: int,
    user_id: int,
    challenge: str,
    token: SoftOtpToken,
) -> bool:
    """Return whether the active key authenticates this versioned token."""
    if not isinstance(keyring, VerificationKeyring):
        raise SoftOtpError("Tính năng Soft OTP chưa được cấu hình an toàn.")
    if token.kid != keyring.active_kid:
        return False
    expected = compute_otp_code(
        keyring.active_key,
        guild_id,
        user_id,
        challenge,
        kid=token.kid,
        issued_at=token.issued_at,
    )
    return hmac.compare_digest(expected, token.code)


def confirm_softotp_token(
    keyring: VerificationKeyring,
    *,
    guild_id: int,
    user_id: int,
    challenge: object,
    token: object,
    now: datetime | None = None,
) -> int:
    """Prove the OTP belongs to the claimed member under the current key."""
    if not isinstance(keyring, VerificationKeyring):
        raise SoftOtpError("Tính năng Soft OTP chưa được cấu hình an toàn.")
    normalized = normalize_challenge(challenge)
    parsed = parse_softotp_token(token)
    require_active_token(keyring, parsed, now=now)
    claimed_id = _require_snowflake(user_id)
    if not otp_code_matches(
        keyring,
        guild_id=guild_id,
        user_id=claimed_id,
        challenge=normalized,
        token=parsed,
    ):
        raise SoftOtpError(SOFTOTP_ACCOUNT_MISMATCH_MESSAGE)
    return claimed_id


def _issuance_document_is_valid(
    document: Mapping[str, Any] | None,
    *,
    guild_id: int,
    challenge: str,
    token: SoftOtpToken,
) -> bool:
    if not isinstance(document, Mapping) or set(document) != _DOCUMENT_KEYS:
        return False
    user_id = document.get("user_id")
    kid = document.get("kid")
    issued_at = document.get("issued_at")
    created_at = document.get("created_at")
    updated_at = document.get("updated_at")
    if (
        document.get("guild_id") != guild_id
        or document.get("challenge") != challenge
        or not isinstance(user_id, int)
        or isinstance(user_id, bool)
        or not 0 < user_id <= _MAX_SNOWFLAKE
        or kid != token.kid
        or issued_at != token.issued_at
        or not isinstance(kid, str)
        or _KID_PATTERN.fullmatch(kid) is None
        or not isinstance(issued_at, int)
        or not 0 < issued_at <= MAX_UNIX_TIMESTAMP
        or not isinstance(created_at, datetime)
        or not isinstance(updated_at, datetime)
        or _unix_seconds(created_at) != token.issued_at
        or document.get("_id") != issuance_lookup_id(guild_id, challenge, token)
    ):
        return False
    return True


def persist_softotp_issuance(
    collection: _IssuanceCollection,
    keyring: VerificationKeyring,
    *,
    guild_id: int,
    user_id: int,
    challenge: object,
    issued_at: datetime,
) -> str:
    """Save the opaque token's identity binding and return the token."""
    if not isinstance(keyring, VerificationKeyring):
        raise SoftOtpError("Tính năng Soft OTP chưa được cấu hình an toàn.")
    normalized = normalize_challenge(challenge)
    created_at = _as_utc(issued_at)
    token_text = issue_softotp_token(
        keyring,
        guild_id=guild_id,
        user_id=user_id,
        challenge=normalized,
        issued_at=created_at,
    )
    parsed = parse_softotp_token(token_text)
    lookup = issuance_lookup_id(guild_id, normalized, parsed)
    document = {
        "_id": lookup,
        "guild_id": _require_snowflake(guild_id),
        "user_id": _require_snowflake(user_id),
        "challenge": normalized,
        "kid": parsed.kid,
        "issued_at": parsed.issued_at,
        "created_at": created_at,
        "updated_at": created_at,
    }
    slot = {
        "guild_id": document["guild_id"],
        "user_id": document["user_id"],
        "challenge": normalized,
    }
    try:
        existing = collection.find_one(slot)
    except PyMongoError as exc:
        raise SoftOtpStoreError(SOFTOTP_STORE_MESSAGE) from exc

    if isinstance(existing, Mapping) and existing.get("_id") == lookup:
        try:
            collection.update_one(
                {"_id": lookup},
                {
                    "$set": {
                        "updated_at": created_at,
                        "kid": parsed.kid,
                        "issued_at": parsed.issued_at,
                    }
                },
            )
        except PyMongoError as exc:
            raise SoftOtpStoreError(SOFTOTP_STORE_MESSAGE) from exc
        return token_text

    if isinstance(existing, Mapping):
        previous_id = existing.get("_id")
        if not isinstance(previous_id, str):
            raise SoftOtpStoreError(SOFTOTP_STORE_MESSAGE)
        try:
            collection.delete_one({"_id": previous_id})
        except PyMongoError as exc:
            raise SoftOtpStoreError(SOFTOTP_STORE_MESSAGE) from exc

    try:
        collection.insert_one(document)
    except DuplicateKeyError:
        try:
            current = collection.find_one({"_id": lookup})
        except PyMongoError as exc:
            raise SoftOtpStoreError(SOFTOTP_STORE_MESSAGE) from exc
        if (
            isinstance(current, Mapping)
            and current.get("user_id") == document["user_id"]
            and current.get("guild_id") == document["guild_id"]
            and current.get("challenge") == normalized
        ):
            return token_text
        raise SoftOtpStoreError(SOFTOTP_STORE_MESSAGE)
    except PyMongoError as exc:
        raise SoftOtpStoreError(SOFTOTP_STORE_MESSAGE) from exc
    return token_text


def resolve_softotp_issuance(
    collection: _IssuanceCollection,
    keyring: VerificationKeyring,
    *,
    guild_id: int,
    challenge: object,
    token: object,
    now: datetime | None = None,
) -> int:
    """Return the bound user ID from a stored issuance after active-key HMAC."""
    if not isinstance(keyring, VerificationKeyring):
        raise SoftOtpError("Tính năng Soft OTP chưa được cấu hình an toàn.")
    normalized = normalize_challenge(challenge)
    parsed = parse_softotp_token(token)
    require_active_token(keyring, parsed, now=now)
    lookup = issuance_lookup_id(guild_id, normalized, parsed)
    try:
        document = collection.find_one({"_id": lookup})
    except PyMongoError as exc:
        raise SoftOtpStoreError(SOFTOTP_LOOKUP_MESSAGE) from exc
    if not _issuance_document_is_valid(
        document if isinstance(document, Mapping) else None,
        guild_id=guild_id,
        challenge=normalized,
        token=parsed,
    ):
        raise SoftOtpError(SOFTOTP_MISMATCH_MESSAGE)
    assert isinstance(document, Mapping)
    user_id = int(document["user_id"])
    if not otp_code_matches(
        keyring,
        guild_id=guild_id,
        user_id=user_id,
        challenge=normalized,
        token=parsed,
    ):
        raise SoftOtpError(SOFTOTP_MISMATCH_MESSAGE)
    return user_id
