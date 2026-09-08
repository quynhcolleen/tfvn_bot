import os
import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
from pymongo.errors import PyMongoError

from cogs._hash_verification import VerificationKeyring
from cogs.operation import _doctor as doctor
from cogs.operation import setup_check as setup_module


HEALTHY_ENVIRONMENT = {
    "DISCORD_TOKEN": "test-discord-secret",
    "DB_METHOD": "mongodb",
    "DB_USERNAME": "test-db-user",
    "DB_PASSWORD": "test-db-password",
    "DB_HOST": "database.invalid",
}


@dataclass
class FakeRole:
    id: int
    position: int
    managed: bool = False
    default: bool = False

    def is_default(self) -> bool:
        return self.default

    def __ge__(self, other: "FakeRole") -> bool:
        return self.position >= other.position


def make_guild(guild_id: int = 41) -> SimpleNamespace:
    channels: dict[int, Any] = {}
    roles: dict[int, FakeRole] = {}
    return SimpleNamespace(
        id=guild_id,
        me=SimpleNamespace(
            guild_permissions=discord.Permissions.all(),
            top_role=FakeRole(900, 10),
        ),
        channels_by_id=channels,
        roles_by_id=roles,
        get_channel=Mock(side_effect=channels.get),
        get_channel_or_thread=Mock(side_effect=channels.get),
        get_role=Mock(side_effect=roles.get),
    )


def make_channel(
    guild: SimpleNamespace,
    channel_id: int = 101,
    *,
    kind: type = discord.TextChannel,
    **permissions: bool,
) -> Mock:
    channel = Mock(spec=kind)
    channel.id = channel_id
    channel.guild = guild
    effective = discord.Permissions.all()
    effective.update(**permissions)
    channel.permissions_for.return_value = effective
    guild.channels_by_id[channel_id] = channel
    return channel


def make_bot(
    guild: SimpleNamespace,
    *,
    loaded: tuple[str, ...] = (),
    selected: tuple[str, ...] = (),
    variables: dict[str, Any] | None = None,
) -> SimpleNamespace:
    cogs: dict[str, Any] = {}
    return SimpleNamespace(
        extensions={module: object() for module in loaded},
        selected_extensions=set(selected),
        extension_load_failures={},
        global_vars={} if variables is None else variables,
        guilds=[guild],
        get_channel=Mock(side_effect=guild.channels_by_id.get),
        get_cog=Mock(side_effect=cogs.get),
        cogs_by_name=cogs,
        walk_commands=Mock(return_value=[]),
        intents=discord.Intents.all(),
        db=Mock(),
    )


def findings(checks: list[doctor.SetupCheck]) -> list[doctor.SetupCheck]:
    return [check for check in checks if check.level in {"warning", "error"}]


class DoctorFixtures:
    def setUp(self) -> None:
        super().setUp()
        environment = patch.dict(os.environ, HEALTHY_ENVIRONMENT, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.guild = make_guild()
        self.bot = make_bot(self.guild)


class TestDoctorEnvironment(DoctorFixtures, unittest.TestCase):
    def test_defaulted_environment_variables_do_not_warn_when_absent(self) -> None:
        checks = doctor._check_environment(self.bot, set())

        self.assertEqual(findings(checks), [])
        for name in ("ENVIRONMENT", "COMMAND_PREFIX", "DB_NAME", "DISABLED_COGS"):
            self.assertNotIn(f"Environment: {name}", [check.name for check in checks])

    def test_missing_and_blank_required_variables_warn_without_values(self) -> None:
        del os.environ["DB_HOST"]
        os.environ["DISCORD_TOKEN"] = "   "

        checks = doctor._check_environment(self.bot, set())

        self.assertEqual(
            {check.name for check in findings(checks)},
            {"Environment: DB_HOST", "Environment: DISCORD_TOKEN"},
        )
        for value in ("test-db-user", "test-db-password"):
            self.assertNotIn(value, repr(checks))

    def test_invalid_runtime_options_warn_without_echoing_input(self) -> None:
        for name, value in (
            ("DB_METHOD", "private-invalid-method"),
            ("ENVIRONMENT", "private-invalid-runtime"),
            ("COMMAND_PREFIX", "   "),
            ("DB_NAME", ""),
        ):
            with self.subTest(name=name), patch.dict(os.environ, {name: value}):
                checks = doctor._check_environment(self.bot, set())
                self.assertTrue(any(
                    check.name == f"Environment: {name}" for check in findings(checks)
                ))
                if value.strip():
                    self.assertNotIn(value, repr(checks))

    def test_feature_credentials_are_checked_only_when_feature_is_active(self) -> None:
        inactive = doctor._check_environment(self.bot, set())
        active = doctor._check_environment(self.bot, {"cogs.nsfw.r34"})

        self.assertFalse(any("RULE34" in check.name for check in inactive))
        self.assertTrue(any(check.name == "Environment: RULE34_API_KEY" for check in findings(active)))
        self.assertTrue(any(check.name == "Environment: RULE34_SECOND_USER_ID" for check in findings(active)))
        self.assertFalse(any(check.name == "Environment: RULE34_API_URL" for check in findings(active)))

    def test_feature_url_and_user_id_are_validated_without_values(self) -> None:
        os.environ.update({
            "RULE34_API_KEY": "private-api-key",
            "RULE34_USER_ID": "not-a-user-id",
            "SECOND_RULE34_API_KEY": "private-second-key",
            "RULE34_SECOND_USER_ID": "12",
            "RULE34_API_URL": "private-invalid-url",
        })

        checks = doctor._check_environment(self.bot, {"cogs.nsfw.r34"})

        names = {check.name for check in findings(checks)}
        self.assertIn("Environment: RULE34_API_URL", names)
        self.assertIn("Environment: RULE34_USER_ID", names)
        for value in ("private-api-key", "private-second-key", "private-invalid-url", "not-a-user-id"):
            self.assertNotIn(value, repr(checks))

    def test_signing_checks_runtime_keyring_and_does_not_expose_invalid_json(self) -> None:
        self.bot.CONTENT_VERIFICATION_KEYS_JSON = "private-malformed-keyring"
        self.bot.CONTENT_VERIFICATION_ACTIVE_KEY_ID = "private-key-id"
        inactive = doctor._check_environment(self.bot, set())
        invalid = doctor._check_environment(self.bot, {"cogs.utils.quote"})
        self.bot.verification_keyring = VerificationKeyring("test", {"test": b"x" * 32})
        valid = doctor._check_environment(self.bot, {"cogs.utils.quote"})

        self.assertFalse(any(check.name == "Content verification" for check in inactive))
        self.assertTrue(any(check.name == "Content verification" for check in findings(invalid)))
        self.assertFalse(any(check.name == "Content verification" for check in findings(valid)))
        self.assertNotIn("private-malformed-keyring", repr(invalid))
        self.assertNotIn("private-key-id", repr(invalid))

    def test_cached_invite_environment_mismatch_is_reported_without_urls(self) -> None:
        previous_url = "https://discord.invalid/private-previous-invite"
        current_url = "https://discord.invalid/private-current-invite"
        self.bot.cogs_by_name["GeneralCog"] = SimpleNamespace(invite_link=previous_url)
        os.environ["INVITE_LINK"] = current_url

        changed = doctor._check_environment(self.bot, {"cogs.general"})
        self.bot.cogs_by_name["GeneralCog"].invite_link = current_url
        same = doctor._check_environment(self.bot, {"cogs.general"})

        self.assertTrue(any(check.name == "Runtime config: INVITE_LINK" for check in findings(changed)))
        self.assertFalse(any(check.name == "Runtime config: INVITE_LINK" for check in same))
        self.assertNotIn(previous_url, repr(changed))
        self.assertNotIn(current_url, repr(changed))


class TestDoctorRuntime(DoctorFixtures, unittest.TestCase):
    def test_selected_failed_extensions_are_checked_but_unselected_are_omitted(self) -> None:
        selected = "cogs.utils.highlight"
        self.bot.selected_extensions = {selected}
        self.bot.extension_load_failures = {selected: "ValueError"}

        modules = doctor._active_modules(self.bot)
        runtime = doctor._check_runtime(self.bot, modules)
        configuration = doctor._check_global_settings(self.bot, self.guild, modules)

        self.assertEqual(modules, {selected})
        self.assertTrue(any(check.name == f"Extension: {selected}" for check in findings(runtime)))
        self.assertTrue(any(check.name == "HIGHLIGHT_CHANNEL" for check in findings(configuration)))
        self.assertFalse(any(check.name == "JOIN_CHANNEL" for check in configuration))

    def test_loaded_extensions_remain_active_after_disabled_flag_changes(self) -> None:
        module = "cogs.utils.highlight"
        self.bot.extensions[module] = object()
        os.environ["DISABLED_COGS"] = "cogs.utils.*"

        modules = doctor._active_modules(self.bot)
        checks = doctor._check_runtime(self.bot, modules)

        self.assertIn(module, modules)
        self.assertFalse(any(check.name.startswith("Extension:") for check in findings(checks)))

    def test_disabled_extension_failures_outside_selection_are_ignored(self) -> None:
        module = "cogs.utils.highlight"
        self.bot.extension_load_failures[module] = "ValueError"
        os.environ["DISABLED_COGS"] = module

        modules = doctor._active_modules(self.bot)
        checks = doctor._check_global_settings(self.bot, self.guild, modules)

        self.assertEqual(modules, set())
        self.assertFalse(any(check.name == "HIGHLIGHT_CHANNEL" for check in checks))

    def test_optional_intents_are_scoped_to_features(self) -> None:
        self.bot.intents.members = False
        self.bot.intents.guild_reactions = False
        self.bot.intents.message_content = False

        inactive = doctor._check_runtime(self.bot, set())
        active = doctor._check_runtime(self.bot, {"cogs.announcement.welcome", "cogs.utils.highlight"})

        inactive_names = {check.name for check in findings(inactive)}
        active_names = {check.name for check in findings(active)}
        self.assertIn("Intent: message_content", inactive_names)
        self.assertNotIn("Intent: members", inactive_names)
        self.assertNotIn("Intent: guild_reactions", inactive_names)
        self.assertIn("Intent: members", active_names)
        self.assertIn("Intent: guild_reactions", active_names)

    def test_dashboard_requires_members_intent_for_current_admin_permissions(self) -> None:
        self.bot.intents.members = False
        checks = doctor._check_runtime(self.bot, {"cogs.operation.operation_dashboard"})

        self.assertTrue(any(check.name == "Intent: members" for check in findings(checks)))

    def test_extension_failure_type_is_shown_but_exception_text_is_rejected(self) -> None:
        module = "cogs.utils.highlight"
        self.bot.extension_load_failures[module] = "ValueError"
        safe = doctor._check_runtime(self.bot, {module})
        self.bot.extension_load_failures[module] = "ValueError: private-credential"
        unsafe = doctor._check_runtime(self.bot, {module})

        failure = next(check for check in safe if check.name == f"Extension: {module}")
        self.assertIn("ValueError", failure.detail)
        self.assertNotIn("private-credential", repr(unsafe))

    def test_cached_configuration_change_requires_reload_and_normalizes_ids(self) -> None:
        self.bot.cogs_by_name["WelcomeCog"] = SimpleNamespace(join_channel=101)
        self.bot.global_vars["JOIN_CHANNEL"] = "102"

        changed = doctor._check_global_settings(self.bot, self.guild, set())
        self.bot.global_vars["JOIN_CHANNEL"] = "101"
        same = doctor._check_global_settings(self.bot, self.guild, set())

        self.assertTrue(any(check.name == "Runtime config: JOIN_CHANNEL" for check in findings(changed)))
        self.assertFalse(any(check.name == "Runtime config: JOIN_CHANNEL" for check in same))

    def test_missing_global_settings_only_warn_for_dependent_features(self) -> None:
        del self.bot.global_vars

        dashboard = doctor._check_global_settings(
            self.bot, self.guild, {"cogs.operation.operation_dashboard"},
        )
        dependent = doctor._check_global_settings(
            self.bot, self.guild, {"cogs.announcement.welcome"},
        )

        self.assertEqual(dashboard, [])
        self.assertEqual(len(dependent), 1)
        self.assertEqual(dependent[0].name, "Global variables")

    def test_optional_booster_anchor_does_not_warn_when_unset(self) -> None:
        checks = doctor._check_global_settings(
            self.bot, self.guild, {"cogs.booster.create_custom_role"},
        )

        self.assertFalse(any(check.name == "BOOSTER_CUSTOM_ROLE_ANCHOR_ID" for check in checks))

    def test_cached_foreign_targets_do_not_create_current_guild_mismatches(self) -> None:
        foreign = make_guild(99)
        first = make_channel(foreign, 201)
        second = make_channel(foreign, 202)
        self.bot.get_channel.side_effect = {201: first, 202: second}.get
        self.bot.cogs_by_name["WelcomeCog"] = SimpleNamespace(join_channel=201)
        self.bot.global_vars["JOIN_CHANNEL"] = 202

        checks = doctor._check_global_settings(self.bot, self.guild, set())

        self.assertFalse(any(check.name == "Runtime config: JOIN_CHANNEL" for check in checks))

    def test_removing_foreign_cached_setting_does_not_warn_but_local_removal_does(self) -> None:
        foreign = make_guild(99)
        channel = make_channel(foreign, 201)
        self.bot.get_channel.side_effect = {201: channel}.get
        self.bot.cogs_by_name["WelcomeCog"] = SimpleNamespace(join_channel=201)

        foreign_removed = doctor._check_global_settings(self.bot, self.guild, set())
        self.bot.cogs_by_name["WelcomeCog"].join_channel = 101
        local_removed = doctor._check_global_settings(self.bot, self.guild, set())

        self.assertFalse(any(check.name == "Runtime config: JOIN_CHANNEL" for check in foreign_removed))
        self.assertTrue(any(check.name == "Runtime config: JOIN_CHANNEL" for check in findings(local_removed)))

    def test_partially_invalid_beta_roles_warn_while_high_access_role_is_valid(self) -> None:
        self.guild.roles_by_id[201] = FakeRole(201, 20)
        self.bot.global_vars["BETA_ROLE_IDS"] = [201, "private-malformed-role"]
        self.bot.walk_commands.return_value = [SimpleNamespace(__beta_function__=True)]

        checks = doctor._check_global_settings(self.bot, self.guild, set())

        self.assertTrue(any(check.name == "BETA_ROLE_IDS" for check in findings(checks)))
        self.assertTrue(any(check.name == "BETA_ROLE_IDS" and check.level == "ok" for check in checks))
        self.assertFalse(any(check.level == "error" for check in checks))
        self.assertNotIn("private-malformed-role", repr(checks))


class TestDoctorPermissions(DoctorFixtures, unittest.TestCase):
    def test_channel_denial_is_reported_despite_guild_grant(self) -> None:
        channel = make_channel(self.guild, send_messages=False)

        checks = doctor._check_effective_permissions(
            self.guild, channel, "Target", doctor.MESSAGE_PERMISSIONS,
        )

        self.assertTrue(self.guild.me.guild_permissions.send_messages)
        self.assertEqual(checks[0].level, "error")
        self.assertIn("send_messages", checks[0].detail)

    def test_channel_grant_passes_independently_of_guild_mismatch(self) -> None:
        self.guild.me.guild_permissions = discord.Permissions.none()
        channel = make_channel(self.guild)

        guild_checks = doctor._check_guild_permissions(self.guild, {"cogs.mod.ban"})
        channel_checks = doctor._check_effective_permissions(
            self.guild, channel, "Target", doctor.MESSAGE_PERMISSIONS,
        )

        self.assertTrue(any(check.name == "Guild permission: ban_members" for check in findings(guild_checks)))
        self.assertEqual(findings(channel_checks), [])

    def test_thread_posting_uses_thread_send_permission(self) -> None:
        channel = make_channel(
            self.guild, kind=discord.Thread,
            send_messages=False, send_messages_in_threads=True,
        )
        allowed = doctor._check_effective_permissions(
            self.guild, channel, "Thread", doctor.MESSAGE_PERMISSIONS,
        )
        channel.permissions_for.return_value.send_messages_in_threads = False
        denied = doctor._check_effective_permissions(
            self.guild, channel, "Thread", doctor.MESSAGE_PERMISSIONS,
        )

        self.assertEqual(findings(allowed), [])
        self.assertIn("send_messages_in_threads", denied[0].detail)

    def test_reference_channel_checks_existence_without_posting_permission(self) -> None:
        channel = make_channel(self.guild, send_messages=False, view_channel=False)
        requirement = doctor.ChannelRequirement("RULE_CHANNEL", (), (), "reference")

        checks = doctor._check_channel_value(self.bot, self.guild, requirement, channel.id)

        self.assertEqual(findings(checks), [])
        channel.permissions_for.assert_not_called()

    def test_wrong_target_type_and_malformed_channel_ids_are_reported(self) -> None:
        category = make_channel(self.guild, kind=discord.CategoryChannel)
        requirement = doctor.ChannelRequirement("JOIN_CHANNEL", ())
        wrong_type = doctor._check_channel_value(self.bot, self.guild, requirement, category.id)
        malformed = doctor._check_channel_value(self.bot, self.guild, requirement, True)

        self.assertEqual(wrong_type[0].level, "error")
        self.assertEqual(malformed[0].level, "error")

    def test_known_foreign_targets_are_omitted_and_unknown_ids_are_cache_warnings(self) -> None:
        foreign = make_guild(99)
        channel = make_channel(foreign, 200)
        foreign.roles_by_id[300] = FakeRole(300, 100)
        self.bot.guilds.append(foreign)
        self.bot.get_channel.side_effect = lambda identifier: channel if identifier == 200 else None
        requirement = doctor.ChannelRequirement("JOIN_CHANNEL", ())

        self.assertEqual(doctor._check_channel_value(self.bot, self.guild, requirement, 200), [])
        self.assertEqual(doctor._check_role_value(self.bot, self.guild, "Role", 300, assignable=True), [])
        unknown = doctor._check_channel_value(self.bot, self.guild, requirement, 404)
        self.assertEqual(unknown[0].level, "warning")
        self.assertIn("cache", unknown[0].detail)

    def test_access_roles_above_bot_pass_but_assignment_roles_fail(self) -> None:
        self.guild.roles_by_id[201] = FakeRole(201, 20)

        access = doctor._check_role_value(self.bot, self.guild, "Access", 201, assignable=False)
        assignment = doctor._check_role_value(self.bot, self.guild, "Grant", 201, assignable=True)

        self.assertEqual(findings(access), [])
        self.assertEqual(assignment[0].level, "error")

    def test_managed_and_default_assignment_roles_fail_below_bot(self) -> None:
        for role in (FakeRole(201, 1, managed=True), FakeRole(201, 0, default=True)):
            with self.subTest(role=role):
                self.guild.roles_by_id[201] = role
                checks = doctor._check_role_value(self.bot, self.guild, "Grant", 201, assignable=True)
                self.assertEqual(checks[0].level, "error")

    def test_missing_bot_member_is_reported_without_permission_crash(self) -> None:
        self.guild.me = None
        channel = make_channel(self.guild)

        checks = doctor._check_guild_permissions(self.guild, {"cogs.mod.ban"})
        effective = doctor._check_effective_permissions(self.guild, channel, "Target", doctor.MESSAGE_PERMISSIONS)

        self.assertEqual(checks[0].name, "Bot member")
        self.assertEqual(checks[0].level, "warning")
        self.assertEqual(effective, [])
        channel.permissions_for.assert_not_called()

    def test_feature_guild_permissions_are_not_required_for_unselected_features(self) -> None:
        self.guild.me.guild_permissions = discord.Permissions.none()
        inactive = doctor._check_guild_permissions(self.guild, set())
        active = doctor._check_guild_permissions(self.guild, {"cogs.mod.ban"})

        self.assertFalse(any(check.name == "Guild permission: ban_members" for check in inactive))
        self.assertTrue(any(check.name == "Guild permission: ban_members" for check in findings(active)))


class TestDoctorCollection(DoctorFixtures, unittest.IsolatedAsyncioTestCase):
    async def test_database_work_runs_off_event_loop(self) -> None:
        event_loop_thread = threading.get_ident()
        worker_threads = []

        def read_database(*args: Any, **kwargs: Any) -> None:
            worker_threads.append(threading.get_ident())

        with patch.object(doctor, "_read_database", side_effect=read_database) as read:
            checks = await doctor.collect_doctor_checks(self.bot, self.guild, None)

        read.assert_called_once_with(self.bot, self.guild.id, check_cases=False)
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], event_loop_thread)
        self.assertTrue(any(check.name == "MongoDB" and check.level == "ok" for check in checks))
        self.bot.db.command.assert_not_called()

    async def test_database_failure_preserves_other_findings_without_error_details(self) -> None:
        del os.environ["DB_HOST"]
        self.bot.selected_extensions = {"cogs.utils.highlight"}
        for error in (TimeoutError("private-timeout"), PyMongoError("private-db-error")):
            with self.subTest(error=type(error).__name__), patch.object(doctor, "_read_database", side_effect=error):
                checks = await doctor.collect_doctor_checks(self.bot, self.guild, None)
                names = {check.name for check in findings(checks)}
                self.assertIn("MongoDB", names)
                self.assertIn("Environment: DB_HOST", names)
                self.assertIn("HIGHLIGHT_CHANNEL", names)
                self.assertNotIn(str(error), repr(checks))

    async def test_configured_moderation_log_checks_permissions_when_feature_enabled(self) -> None:
        self.bot.extensions["cogs.mod.cases"] = object()
        channel = make_channel(self.guild, embed_links=False)
        with patch.object(doctor, "_read_database", return_value={"log_channel_id": channel.id}) as read:
            checks = await doctor.collect_doctor_checks(self.bot, self.guild, None)

        read.assert_called_once_with(self.bot, self.guild.id, check_cases=True)
        log_findings = [check for check in findings(checks) if check.name.startswith("Moderation log")]
        self.assertEqual(len(log_findings), 1)
        self.assertIn("case log_channel #channel", log_findings[0].fix)

    async def test_moderation_log_rejects_non_text_channels_with_correct_remediation(self) -> None:
        self.bot.extensions["cogs.mod.cases"] = object()
        for kind in (discord.Thread, discord.VoiceChannel, discord.CategoryChannel):
            with self.subTest(kind=kind.__name__):
                channel = make_channel(self.guild, kind=kind)
                with patch.object(doctor, "_read_database", return_value={"log_channel_id": channel.id}):
                    checks = await doctor.collect_doctor_checks(self.bot, self.guild, None)
                log_findings = [check for check in findings(checks) if check.name.startswith("Moderation log")]
                self.assertEqual(len(log_findings), 1)
                self.assertEqual(log_findings[0].level, "error")
                self.assertIn("case log_channel #channel", log_findings[0].fix)
                channel.permissions_for.assert_not_called()

    async def test_verify_channel_failure_explains_environment_configuration(self) -> None:
        self.bot.extensions["cogs.general"] = object()
        os.environ["VERIFY_CHANNEL"] = "private-invalid-channel-id"
        with patch.object(doctor, "_read_database", return_value=None):
            checks = await doctor.collect_doctor_checks(self.bot, self.guild, None)

        channel_findings = [check for check in findings(checks) if check.name == "VERIFY_CHANNEL"]
        self.assertEqual(len(channel_findings), 1)
        self.assertIn("VERIFY_CHANNEL", channel_findings[0].fix)
        self.assertNotIn("setting set_variable", channel_findings[0].fix)
        self.assertNotIn("private-invalid-channel-id", repr(checks))

    async def test_dashboard_channel_permissions_are_checked(self) -> None:
        channel = make_channel(self.guild, embed_links=False)
        with patch.object(doctor, "_read_database", return_value=None):
            checks = await doctor.collect_doctor_checks(self.bot, self.guild, channel)

        self.assertTrue(any(check.name == "Kênh mở dashboard" for check in findings(checks)))

    def test_database_read_has_driver_timeout_and_guild_scoped_projection(self) -> None:
        context = Mock()
        context.__enter__ = Mock()
        context.__exit__ = Mock(return_value=False)
        collection = Mock()
        self.bot.db = Mock()
        self.bot.db.__getitem__ = Mock(return_value=collection)
        with patch.object(doctor.pymongo, "timeout", return_value=context) as timeout:
            doctor._read_database(self.bot, self.guild.id, check_cases=True)

        timeout.assert_called_once_with(doctor.DATABASE_TIMEOUT_SECONDS)
        self.bot.db.command.assert_called_once_with("ping")
        self.bot.db.__getitem__.assert_called_once_with("moderation_config")
        collection.find_one.assert_called_once_with(
            {"guild_id": self.guild.id}, {"_id": 0, "log_channel_id": 1},
        )
        self.assertEqual([entry[0] for entry in collection.mock_calls], ["find_one"])


class TestSetupDoctorIntegration(DoctorFixtures, unittest.IsolatedAsyncioTestCase):
    async def test_setup_uses_shared_collector_and_keeps_three_safe_summary_fields(self) -> None:
        channel = make_channel(self.guild)
        ctx = SimpleNamespace(guild=self.guild, channel=channel, send=AsyncMock())
        cog = setup_module.SetupCheckCog(self.bot)
        checks = [
            doctor.SetupCheck("ok", "Passed check", "Healthy"),
            doctor.SetupCheck("warning", "Warning check", "Needs review", "Review config"),
            doctor.SetupCheck("error", "Failed check", "Needs repair", "Repair config"),
        ]
        with patch.object(setup_module, "collect_doctor_checks", new=AsyncMock(return_value=checks)) as collect:
            await cog.run_setup_check(ctx)

        collect.assert_awaited_once_with(self.bot, self.guild, channel)
        ctx.send.assert_awaited_once()
        kwargs = ctx.send.await_args.kwargs
        embed = kwargs["embed"]
        self.assertEqual([field.name for field in embed.fields], ["Lỗi cần sửa", "Cảnh báo", "Đã đạt"])
        self.assertIn("Failed check", embed.fields[0].value)
        self.assertIn("Warning check", embed.fields[1].value)
        self.assertIn("Passed check", embed.fields[2].value)
        self.assertEqual(embed.description, "✅ 1 · ⚠️ 1 · ❌ 1")
        self.assertEqual(kwargs["allowed_mentions"].to_dict()["parse"], [])
        self.assertFalse(kwargs["allowed_mentions"].replied_user)


if __name__ == "__main__":
    unittest.main()
