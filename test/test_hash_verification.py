import base64
import copy
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import discord
from bson import BSON
from pymongo.errors import DuplicateKeyError, PyMongoError

from cogs._hash_verification import (
    FEMBOY_CARD_KIND,
    QUOTE_KIND,
    VERIFICATION_COLLECTION,
    VerificationConfigurationError,
    VerificationReferenceError,
    VerificationStoreError,
    VerificationTokenError,
    issue_verification,
    load_verification_keyring,
    normalize_verification_reference,
    normalize_verification_token,
    verification_document_is_valid,
    verification_payload_is_valid,
    verification_reference_from_token,
    verification_reference_from_token_id,
    verification_token_id_from_reference,
    verification_token_fingerprint,
    verify_verification_token,
)
from cogs.funny_things.cards.femboy_card import FemboyCardCog
from cogs.utils.hash_verify import HashVerifyCog
from cogs.utils.quote import QuoteCog


ISSUED_AT = datetime(2026, 8, 28, 3, 4, tzinfo=timezone.utc)
OLD_KEY = bytes(range(32))
NEW_KEY = bytes(range(32, 64))
WRONG_KEY = b"w" * 32


def encoded_key(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def keyring(
    *,
    active: str = "2026-08",
    keys: dict[str, bytes] | None = None,
):
    configured = keys or {"2026-08": OLD_KEY}
    return load_verification_keyring(
        json.dumps(
            {kid: encoded_key(value) for kid, value in configured.items()}
        ),
        active,
    )


TEST_KEYRING = keyring()


def valid_payload(
    kind: str,
    **overrides: object,
) -> dict[str, object]:
    if kind == FEMBOY_CARD_KIND:
        payload: dict[str, object] = {
            "guild_id": 10,
            "member_id": 20,
            "member_name": "Kien",
            "role_id": 40,
            "role_name": "Femboy",
            "issued_by_id": 20,
        }
    else:
        payload = {
            "guild_id": 10,
            "channel_id": 21,
            "channel_name": "general",
            "message_id": 22,
            "message_url": "https://discord.com/channels/10/21/22",
            "author_id": 23,
            "author_name": "Quote Author",
            "content": "Nội dung gốc",
            "source_created_at": "2026-08-01T00:00:00+00:00",
            "issued_by_id": 24,
            "issued_by_name": "Quote Creator",
        }
    payload.update(overrides)
    return payload


def issued_record(
    kind: str,
    payload: dict[str, object] | None = None,
    *,
    signing_keyring=TEST_KEYRING,
    token_id: str = "12" * 16,
    snapshot_salt: str = "34" * 16,
) -> tuple[str, object, dict[str, object]]:
    collection = MagicMock()
    with patch(
        "cogs._hash_verification.secrets.token_hex",
        side_effect=[token_id, snapshot_salt],
    ):
        proof = issue_verification(
            collection,
            signing_keyring,
            kind=kind,
            payload=valid_payload(kind, **(payload or {})),
            issued_at=ISSUED_AT,
        )
    document = collection.insert_one.call_args.args[0]
    claims = verify_verification_token(proof, signing_keyring)
    return proof, claims, document


class TestSigningConfiguration(unittest.TestCase):
    def test_loads_a_versioned_base64url_keyring(self):
        loaded = keyring(
            active="new",
            keys={"old": OLD_KEY, "new": NEW_KEY},
        )
        self.assertEqual(loaded.active_kid, "new")
        self.assertEqual(loaded.keys["old"], OLD_KEY)
        self.assertEqual(loaded.active_key, NEW_KEY)

    def test_missing_or_short_keys_fail_closed(self):
        cases = [
            (None, "key"),
            ("{}", "key"),
            (json.dumps({"key": encoded_key(b"x" * 31)}), "key"),
            (json.dumps({"key": encoded_key(OLD_KEY)}), "missing"),
        ]
        for keys_json, active in cases:
            with self.subTest(keys_json=keys_json, active=active):
                with self.assertRaises(VerificationConfigurationError):
                    load_verification_keyring(keys_json, active)

    def test_duplicate_key_ids_are_rejected(self):
        encoded = encoded_key(OLD_KEY)
        duplicate_json = f'{{"same":"{encoded}","same":"{encoded}"}}'
        with self.assertRaises(VerificationConfigurationError):
            load_verification_keyring(duplicate_json, "same")


class TestSignedVerificationProofs(unittest.TestCase):
    def test_proof_contains_readable_signed_manifest_without_quote_text(self):
        proof, claims, _ = issued_record(QUOTE_KIND)
        prefix, kid, payload_segment, signature = proof.split(".")
        padded = payload_segment + "=" * (-len(payload_segment) % 4)
        manifest = json.loads(base64.urlsafe_b64decode(padded))

        self.assertEqual(prefix, "tfv1")
        self.assertEqual(kid, "2026-08")
        self.assertEqual(len(signature), 43)
        self.assertEqual(manifest["kind"], QUOTE_KIND)
        self.assertEqual(manifest["iss"], "tfvn_bot")
        self.assertEqual(manifest["guild_id"], "10")
        self.assertEqual(manifest["channel_id"], "21")
        self.assertEqual(manifest["message_id"], "22")
        self.assertEqual(manifest["author_id"], "23")
        self.assertEqual(manifest["snapshot_sha256"], claims.snapshot_sha256)
        self.assertEqual(manifest["snapshot_salt"], "34" * 16)
        self.assertNotIn("content", manifest)
        self.assertNotIn("author_name", manifest)

    def test_normalizes_only_copy_backticks_and_preserves_case(self):
        proof, _, _ = issued_record(FEMBOY_CARD_KIND)
        self.assertEqual(normalize_verification_token(f"  `{proof}`  "), proof)

        changed_case = proof.replace("tfv1", "TFV1", 1)
        with self.assertRaises(VerificationTokenError):
            normalize_verification_token(changed_case)

    def test_short_reference_round_trips_the_signed_token_id(self):
        proof, claims, _ = issued_record(FEMBOY_CARD_KIND)
        reference = verification_reference_from_token(proof, TEST_KEYRING)

        self.assertTrue(reference.startswith("tfp1_"))
        self.assertEqual(len(reference), 31)
        self.assertEqual(
            verification_token_id_from_reference(reference.upper()),
            claims.token_id,
        )
        self.assertEqual(
            normalize_verification_reference(f" `{reference.upper()}` "),
            reference,
        )

        with self.assertRaises(VerificationReferenceError):
            verification_token_id_from_reference(reference[:-1] + "0")

    def test_issue_persists_a_signature_bound_snapshot(self):
        proof, claims, document = issued_record(FEMBOY_CARD_KIND)
        self.assertTrue(proof.startswith("tfv1.2026-08."))
        self.assertEqual(document["_id"], claims.token_id)
        self.assertEqual(document["token"], proof)
        self.assertEqual(document["created_at"], ISSUED_AT)
        self.assertEqual(document["claims"], dict(claims.raw))
        self.assertEqual(
            document["payload"]["issued_at"],
            "2026-08-28T03:04:00+00:00",
        )
        self.assertTrue(verification_document_is_valid(claims, document))

    def test_record_survives_a_real_bson_round_trip(self):
        guild_id = 123_456_789_012_345_678
        channel_id = 223_456_789_012_345_678
        message_id = 323_456_789_012_345_678
        proof, claims, document = issued_record(
            QUOTE_KIND,
            {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "message_url": (
                    f"https://discord.com/channels/{guild_id}/"
                    f"{channel_id}/{message_id}"
                ),
                "author_id": 423_456_789_012_345_678,
                "issued_by_id": 523_456_789_012_345_678,
            },
        )

        round_tripped = BSON.encode(document).decode()

        self.assertEqual(
            type(round_tripped["payload"]["guild_id"]).__name__,
            "Int64",
        )
        self.assertEqual(
            round_tripped["_id"],
            claims.token_id,
        )
        self.assertTrue(
            verification_document_is_valid(claims, round_tripped)
        )

    def test_existing_full_token_record_remains_valid(self):
        proof, claims, document = issued_record(FEMBOY_CARD_KIND)
        legacy_document = copy.deepcopy(document)
        legacy_document.pop("token")
        legacy_document["_id"] = verification_token_fingerprint(proof)

        self.assertTrue(
            verification_document_is_valid(claims, legacy_document)
        )

    def test_payload_or_signature_tampering_is_rejected(self):
        proof, _, _ = issued_record(FEMBOY_CARD_KIND)
        parts = proof.split(".")
        parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
        tampered_payload = ".".join(parts)

        parts = proof.split(".")
        parts[3] = ("A" if parts[3][0] != "A" else "B") + parts[3][1:]
        tampered_signature = ".".join(parts)

        for value in (tampered_payload, tampered_signature):
            with self.subTest(value=value), self.assertRaises(
                VerificationTokenError
            ):
                verify_verification_token(value, TEST_KEYRING)

    def test_wrong_secret_cannot_forge_tfvn_signature(self):
        wrong_keyring = keyring(keys={"2026-08": WRONG_KEY})
        forged_proof, _, _ = issued_record(
            FEMBOY_CARD_KIND,
            signing_keyring=wrong_keyring,
        )
        with self.assertRaises(VerificationTokenError):
            verify_verification_token(forged_proof, TEST_KEYRING)

    def test_unknown_key_id_is_rejected(self):
        proof, _, _ = issued_record(FEMBOY_CARD_KIND)
        parts = proof.split(".")
        parts[1] = "unknown"
        with self.assertRaises(VerificationTokenError):
            verify_verification_token(".".join(parts), TEST_KEYRING)

    def test_old_signatures_survive_key_rotation(self):
        old_keyring = keyring(active="old", keys={"old": OLD_KEY})
        proof, _, _ = issued_record(
            FEMBOY_CARD_KIND,
            signing_keyring=old_keyring,
        )
        rotated = keyring(
            active="new",
            keys={"old": OLD_KEY, "new": NEW_KEY},
        )

        claims = verify_verification_token(proof, rotated)

        self.assertEqual(claims.kid, "old")

    def test_tampered_or_swapped_snapshot_is_rejected(self):
        _, claims, document = issued_record(FEMBOY_CARD_KIND)
        tampered = copy.deepcopy(document)
        tampered["payload"]["role_name"] = "Administrator"
        self.assertFalse(verification_document_is_valid(claims, tampered))

        type_tampered = copy.deepcopy(document)
        type_tampered["claims"]["v"] = 1.0
        self.assertFalse(
            verification_document_is_valid(claims, type_tampered)
        )

        _, other_claims, other_document = issued_record(
            FEMBOY_CARD_KIND,
            {"member_id": 99, "member_name": "Other", "issued_by_id": 99},
        )
        self.assertFalse(
            verification_document_is_valid(other_claims, document)
        )
        self.assertFalse(
            verification_document_is_valid(claims, other_document)
        )

    def test_duplicate_record_retries_with_a_new_signed_id(self):
        collection = MagicMock()
        collection.insert_one.side_effect = [
            DuplicateKeyError("duplicate"),
            None,
        ]
        random_values = ["00" * 16, "01" * 16, "10" * 16, "11" * 16]
        with patch(
            "cogs._hash_verification.secrets.token_hex",
            side_effect=random_values,
        ):
            proof = issue_verification(
                collection,
                TEST_KEYRING,
                kind=QUOTE_KIND,
                payload=valid_payload(QUOTE_KIND),
                issued_at=ISSUED_AT,
            )
        claims = verify_verification_token(proof, TEST_KEYRING)
        self.assertEqual(collection.insert_one.call_count, 2)
        self.assertEqual(claims.token_id, "10" * 16)
        self.assertEqual(
            collection.insert_one.call_args.args[0]["_id"],
            claims.token_id,
        )

    def test_database_failure_becomes_store_error(self):
        collection = MagicMock()
        collection.insert_one.side_effect = PyMongoError("offline")
        with self.assertRaises(VerificationStoreError):
            issue_verification(
                collection,
                TEST_KEYRING,
                kind=QUOTE_KIND,
                payload=valid_payload(QUOTE_KIND),
                issued_at=ISSUED_AT,
            )

    def test_payload_schema_is_exact(self):
        payload = valid_payload(FEMBOY_CARD_KIND)
        payload["issued_at"] = ISSUED_AT.isoformat()
        payload["untrusted_extra"] = "ignored?"
        self.assertFalse(
            verification_payload_is_valid(FEMBOY_CARD_KIND, payload)
        )
        with self.assertRaises(ValueError):
            issue_verification(
                MagicMock(),
                TEST_KEYRING,
                kind=FEMBOY_CARD_KIND,
                payload={"guild_id": 10},
                issued_at=ISSUED_AT,
            )


class TestHashVerifyCommand(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _context(
        *,
        guild_id: int = 10,
        channel: object | None = None,
    ) -> SimpleNamespace:
        source_channel = channel or SimpleNamespace(id=99)
        return SimpleNamespace(
            author=SimpleNamespace(id=30),
            channel=source_channel,
            guild=SimpleNamespace(id=guild_id),
            prefix="!tf ",
            send=AsyncMock(),
        )

    @staticmethod
    def _cog(collection: MagicMock, *, signing_keyring=TEST_KEYRING):
        return HashVerifyCog(
            SimpleNamespace(
                db={VERIFICATION_COLLECTION: collection},
                verification_keyring=signing_keyring,
            )
        )

    async def test_verifies_femboy_card_in_current_guild(self):
        proof, claims, document = issued_record(FEMBOY_CARD_KIND)
        reference = verification_reference_from_token(proof, TEST_KEYRING)
        collection = MagicMock()
        collection.find_one.return_value = document
        cog = self._cog(collection)
        ctx = self._context()

        await cog.hash_verify.callback(cog, ctx, f"`{reference}`")

        collection.find_one.assert_called_once_with(
            {"_id": claims.token_id}
        )
        embed = ctx.send.await_args.kwargs["embed"]
        self.assertEqual(
            embed.title,
            "✅ TFVN proof hợp lệ — Dữ liệu Femboy Card",
        )
        self.assertIn("Femboy", "\n".join(field.value for field in embed.fields))
        self.assertIn(claims.token_id[:12], embed.footer.text)

    async def test_full_token_still_resolves_an_existing_legacy_record(self):
        proof, claims, document = issued_record(FEMBOY_CARD_KIND)
        legacy_document = copy.deepcopy(document)
        legacy_document.pop("token")
        legacy_id = verification_token_fingerprint(proof)
        legacy_document["_id"] = legacy_id
        collection = MagicMock()
        collection.find_one.side_effect = [None, legacy_document]
        cog = self._cog(collection)
        ctx = self._context()

        await cog.hash_verify.callback(cog, ctx, proof)

        self.assertEqual(
            collection.find_one.call_args_list,
            [
                call({"_id": claims.token_id}),
                call({"_id": legacy_id}),
            ],
        )
        self.assertIn("embed", ctx.send.await_args.kwargs)

    async def test_verifies_quote_and_shows_snapshot_with_source_access(self):
        proof, _, document = issued_record(QUOTE_KIND)
        reference = verification_reference_from_token(proof, TEST_KEYRING)
        permissions = SimpleNamespace(
            view_channel=True,
            read_message_history=True,
        )
        source_channel = SimpleNamespace(
            id=21,
            permissions_for=MagicMock(return_value=permissions),
        )
        ctx = self._context(channel=source_channel)
        collection = MagicMock()
        collection.find_one.return_value = document
        cog = self._cog(collection)

        await cog.hash_verify.callback(cog, ctx, reference)

        embed = ctx.send.await_args.kwargs["embed"]
        rendered = "\n".join(field.value for field in embed.fields)
        self.assertEqual(embed.title, "✅ TFVN proof hợp lệ — Nội dung quote")
        self.assertIn("Nội dung gốc", rendered)
        self.assertIn("Mở tin nhắn", rendered)
        source_channel.permissions_for.assert_called_once_with(ctx.author)

    async def test_hides_quote_details_outside_source_channel(self):
        proof, _, document = issued_record(
            QUOTE_KIND,
            {
                "author_name": "Private Author",
                "content": "Nội dung riêng tư",
                "issued_by_name": "Private Creator",
            },
        )
        reference = verification_reference_from_token(proof, TEST_KEYRING)
        public_channel = SimpleNamespace(
            id=99,
            permissions_for=lambda member: SimpleNamespace(
                view_channel=True,
                read_message_history=True,
            ),
        )
        ctx = self._context(channel=public_channel)
        collection = MagicMock()
        collection.find_one.return_value = document
        cog = self._cog(collection)

        await cog.hash_verify.callback(cog, ctx, reference)

        embed = ctx.send.await_args.kwargs["embed"]
        rendered = "\n".join(field.value for field in embed.fields)
        self.assertIn("không đưa quote ra khỏi kênh nguồn", rendered)
        self.assertNotIn("Nội dung riêng tư", rendered)
        self.assertNotIn("Private Author", rendered)
        self.assertNotIn("Private Creator", rendered)

    async def test_shows_long_source_snapshot_across_bounded_fields(self):
        content = "x" * 3_500
        proof, _, document = issued_record(QUOTE_KIND, {"content": content})
        reference = verification_reference_from_token(proof, TEST_KEYRING)
        source_channel = SimpleNamespace(
            id=21,
            permissions_for=lambda member: SimpleNamespace(
                view_channel=True,
                read_message_history=True,
            ),
        )
        ctx = self._context(channel=source_channel)
        collection = MagicMock()
        collection.find_one.return_value = document
        cog = self._cog(collection)

        await cog.hash_verify.callback(cog, ctx, reference)

        embed = ctx.send.await_args.kwargs["embed"]
        snapshot_fields = [
            field
            for field in embed.fields
            if field.name.startswith("Nội dung chữ TFVN")
        ]
        self.assertEqual(len(snapshot_fields), 4)
        self.assertEqual("".join(field.value for field in snapshot_fields), content)
        self.assertTrue(
            all(len(field.value) <= 1_024 for field in snapshot_fields)
        )
        self.assertLessEqual(len(embed), 6_000)

    async def test_invalid_signature_never_queries_database(self):
        proof, _, _ = issued_record(FEMBOY_CARD_KIND)
        forged = proof[:-1] + ("A" if proof[-1] != "A" else "B")
        collection = MagicMock()
        cog = self._cog(collection)
        ctx = self._context()

        await cog.hash_verify.callback(cog, ctx, forged)

        collection.find_one.assert_not_called()
        self.assertIn("chữ ký TFVN hợp lệ", ctx.send.await_args.args[0])

    async def test_schema_valid_database_row_cannot_create_a_signature(self):
        forged = "tfv1.2026-08." + "A" * 200 + "." + "A" * 43
        collection = MagicMock()
        collection.find_one.return_value = {"kind": FEMBOY_CARD_KIND}
        cog = self._cog(collection)
        ctx = self._context()

        await cog.hash_verify.callback(cog, ctx, forged)

        collection.find_one.assert_not_called()
        self.assertNotIn("embed", ctx.send.await_args.kwargs)

    async def test_forged_hidden_token_is_rejected_for_short_code(self):
        token_id = "ab" * 16
        reference = verification_reference_from_token_id(token_id)
        collection = MagicMock()
        collection.find_one.return_value = {
            "_id": token_id,
            "token": "tfv1.2026-08." + "A" * 200 + "." + "A" * 43,
        }
        cog = self._cog(collection)
        ctx = self._context()

        await cog.hash_verify.callback(cog, ctx, reference)

        collection.find_one.assert_called_once_with({"_id": token_id})
        self.assertIn("Không tìm thấy mã proof hợp lệ", ctx.send.await_args.args[0])
        self.assertNotIn("embed", ctx.send.await_args.kwargs)

    async def test_malformed_short_code_never_queries_database(self):
        collection = MagicMock()
        cog = self._cog(collection)
        ctx = self._context()

        await cog.hash_verify.callback(cog, ctx, "tfp1_too-short")

        collection.find_one.assert_not_called()
        self.assertIn("tfp1_<26 ký tự>", ctx.send.await_args.args[0])

    async def test_unknown_short_code_has_no_unverified_success_state(self):
        reference = verification_reference_from_token_id("ab" * 16)
        collection = MagicMock()
        collection.find_one.return_value = None
        cog = self._cog(collection)
        ctx = self._context()

        await cog.hash_verify.callback(cog, ctx, reference)

        self.assertIn("Không tìm thấy mã proof hợp lệ", ctx.send.await_args.args[0])
        self.assertNotIn("embed", ctx.send.await_args.kwargs)

    async def test_short_code_cannot_be_redirected_to_another_valid_token(self):
        first_proof, first_claims, _ = issued_record(FEMBOY_CARD_KIND)
        _, _, second_document = issued_record(
            FEMBOY_CARD_KIND,
            {"member_id": 99, "member_name": "Other", "issued_by_id": 99},
            token_id="56" * 16,
            snapshot_salt="78" * 16,
        )
        reference = verification_reference_from_token(
            first_proof,
            TEST_KEYRING,
        )
        collection = MagicMock()
        collection.find_one.return_value = second_document
        cog = self._cog(collection)
        ctx = self._context()

        await cog.hash_verify.callback(cog, ctx, reference)

        collection.find_one.assert_called_once_with(
            {"_id": first_claims.token_id}
        )
        self.assertIn("Không tìm thấy mã proof hợp lệ", ctx.send.await_args.args[0])
        self.assertNotIn("embed", ctx.send.await_args.kwargs)

    async def test_cross_guild_replay_is_rejected_before_database(self):
        proof, _, _ = issued_record(FEMBOY_CARD_KIND)
        collection = MagicMock()
        cog = self._cog(collection)
        ctx = self._context(guild_id=999)

        await cog.hash_verify.callback(cog, ctx, proof)

        collection.find_one.assert_not_called()
        self.assertIn("server hiện tại", ctx.send.await_args.args[0])

    async def test_short_code_uses_only_the_signed_guild_after_lookup(self):
        proof, claims, document = issued_record(FEMBOY_CARD_KIND)
        reference = verification_reference_from_token(proof, TEST_KEYRING)
        collection = MagicMock()
        collection.find_one.return_value = document
        cog = self._cog(collection)
        ctx = self._context(guild_id=999)

        await cog.hash_verify.callback(cog, ctx, reference)

        collection.find_one.assert_called_once_with({"_id": claims.token_id})
        self.assertIn("server hiện tại", ctx.send.await_args.args[0])
        self.assertNotIn("embed", ctx.send.await_args.kwargs)

    async def test_valid_signature_reports_unavailable_snapshot_honestly(self):
        proof, _, _ = issued_record(QUOTE_KIND)
        collection = MagicMock()
        collection.find_one.return_value = None
        cog = self._cog(collection)
        ctx = self._context()

        await cog.hash_verify.callback(cog, ctx, proof)

        embed = ctx.send.await_args.kwargs["embed"]
        self.assertIn("Snapshot chưa xác minh", embed.title)
        self.assertIn("Không thể xác nhận nội dung", embed.description)
        self.assertNotEqual(embed.color, discord.Color.green())

    async def test_database_lookup_failure_is_not_signature_failure(self):
        proof, _, _ = issued_record(FEMBOY_CARD_KIND)
        collection = MagicMock()
        collection.find_one.side_effect = PyMongoError("offline")
        cog = self._cog(collection)
        ctx = self._context()

        with patch("cogs.utils.hash_verify.logger.exception") as log_exception:
            await cog.hash_verify.callback(cog, ctx, proof)

        self.assertIn("tải snapshot", ctx.send.await_args.args[0])
        log_exception.assert_called_once_with(
            "Failed to look up content verification proof"
        )

    async def test_tampered_snapshot_does_not_receive_green_verification(self):
        proof, _, document = issued_record(FEMBOY_CARD_KIND)
        document["payload"]["role_name"] = "Changed"
        collection = MagicMock()
        collection.find_one.return_value = document
        cog = self._cog(collection)
        ctx = self._context()

        await cog.hash_verify.callback(cog, ctx, proof)

        self.assertIn("snapshot đã lưu không khớp", ctx.send.await_args.args[0])
        self.assertNotIn("embed", ctx.send.await_args.kwargs)

    async def test_missing_configuration_fails_closed_without_database(self):
        collection = MagicMock()
        cog = HashVerifyCog(
            SimpleNamespace(db={VERIFICATION_COLLECTION: collection})
        )
        ctx = self._context()

        await cog.hash_verify.callback(cog, ctx, "anything")

        collection.find_one.assert_not_called()
        self.assertIn("chưa được cấu hình an toàn", ctx.send.await_args.args[0])


class TestVerificationProducers(unittest.IsolatedAsyncioTestCase):
    async def test_femboy_card_persists_and_displays_signed_proof(self):
        collection = MagicMock()
        role = SimpleNamespace(
            id=40,
            name="Femboy",
            position=5,
            color=discord.Color.from_rgb(255, 105, 180),
        )
        duplicate_name_role = SimpleNamespace(
            id=41,
            name="Femboy",
            position=1,
            color=discord.Color.blue(),
        )
        avatar = SimpleNamespace(url="https://cdn.example/avatar.png")
        member = SimpleNamespace(
            id=20,
            name="kien",
            display_name="Kiên",
            mention="<@20>",
            display_avatar=avatar,
            roles=[role],
        )
        ctx = SimpleNamespace(
            author=member,
            guild=SimpleNamespace(id=10, roles=[duplicate_name_role, role]),
            prefix="!tf ",
            send=AsyncMock(),
        )
        bot = SimpleNamespace(
            FEMBOY_ROLE=["Femboy"],
            db={VERIFICATION_COLLECTION: collection},
            verification_keyring=TEST_KEYRING,
        )
        cog = FemboyCardCog(bot)

        with patch(
            "cogs._hash_verification.secrets.token_hex",
            side_effect=["34" * 16, "56" * 16],
        ):
            await cog.femboy_card.callback(cog, ctx)

        document = collection.insert_one.call_args.args[0]
        rendered = "\n".join(field.value for field in ctx.send.await_args.kwargs["embed"].fields)
        reference = next(
            part.strip("`")
            for part in rendered.split()
            if part.strip("`").startswith("tfp1_")
        )
        proof = document["token"]
        claims = verify_verification_token(proof, TEST_KEYRING)
        self.assertTrue(verification_document_is_valid(claims, document))
        self.assertEqual(
            reference,
            verification_reference_from_token(proof, TEST_KEYRING),
        )
        self.assertEqual(document["kind"], FEMBOY_CARD_KIND)
        self.assertEqual(document["payload"]["member_id"], 20)
        self.assertEqual(document["payload"]["role_id"], 40)
        self.assertNotIn("partner_id", document["payload"])
        self.assertIn(f"!tf hash_verify {reference}", rendered)
        proof_field = next(
            field
            for field in ctx.send.await_args.kwargs["embed"].fields
            if "proof" in field.name.casefold()
        )
        self.assertLessEqual(len(proof_field.value), 1_024)

    async def test_quote_proof_captures_source_snapshot(self):
        collection = MagicMock()
        cog = QuoteCog(
            SimpleNamespace(
                db={VERIFICATION_COLLECTION: collection},
                verification_keyring=TEST_KEYRING,
            )
        )
        author = SimpleNamespace(
            id=23,
            name="author",
            display_name="Quote Author",
        )
        message = SimpleNamespace(
            id=22,
            author=author,
            channel=SimpleNamespace(id=21, name="general"),
            created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            jump_url="https://discord.com/channels/10/21/22",
        )
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=10),
            author=SimpleNamespace(
                id=24,
                name="creator",
                display_name="Quote Creator",
            ),
        )

        with patch(
            "cogs._hash_verification.secrets.token_hex",
            side_effect=["56" * 16, "78" * 16],
        ):
            proof = await cog._issue_quote_proof(
                ctx,
                message,
                "  Nội dung gốc  ",
            )

        document = collection.insert_one.call_args.args[0]
        claims = verify_verification_token(proof, TEST_KEYRING)
        self.assertEqual(document["_id"], claims.token_id)
        self.assertEqual(document["token"], proof)
        self.assertEqual(document["kind"], QUOTE_KIND)
        self.assertEqual(document["payload"]["content"], "Nội dung gốc")
        self.assertEqual(document["payload"]["message_id"], 22)
        self.assertTrue(verification_document_is_valid(claims, document))

    async def test_producer_without_secret_emits_no_unsigned_fallback(self):
        collection = MagicMock()
        cog = QuoteCog(SimpleNamespace(db={VERIFICATION_COLLECTION: collection}))
        with self.assertRaises(VerificationConfigurationError):
            await cog._issue_quote_proof(
                SimpleNamespace(guild=SimpleNamespace(id=10)),
                SimpleNamespace(),
                "text",
            )
        collection.insert_one.assert_not_called()


if __name__ == "__main__":
    unittest.main()
