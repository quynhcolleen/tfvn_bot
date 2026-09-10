import copy
import json
import random
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

from cogs.onboarding._role_exam_helpers import (
    MAX_CHOICE_TEXT_LENGTH,
    MAX_ID_LENGTH,
    MAX_INSTRUCTIONS_LENGTH,
    MAX_PROMPT_LENGTH,
    MAX_TITLE_LENGTH,
    UNSAFE_ROLE_PERMISSION_NAMES,
    RoleExamChoice,
    RoleExamConfig,
    RoleExamConfigError,
    RoleExamQuestion,
    is_passing_score,
    load_role_exam_config,
    required_correct_count,
    score_answers,
    shuffled_questions,
    unsafe_role_permission_names,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_CONFIG_PATH = REPOSITORY_ROOT / "data" / "role_exam.json"


def valid_payload() -> dict:
    return {
        "schema_version": 1,
        "title": "Bài kiểm tra nhận role",
        "instructions": "Trả lời đủ 20 câu rồi nộp bài.",
        "role_id": None,
        "required_percent": 80,
        "questions": [
            {
                "id": f"q{number:02d}",
                "prompt": f"Câu hỏi {number}",
                "choices": [
                    {"id": "a", "text": "Đáp án A"},
                    {"id": "b", "text": "Đáp án B"},
                    {"id": "c", "text": "Đáp án C"},
                    {"id": "d", "text": "Đáp án D"},
                ],
                "correct_choice_id": "a",
            }
            for number in range(1, 21)
        ],
    }


def load_payload(payload: object) -> RoleExamConfig:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "role_exam.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return load_role_exam_config(path)


class TestRoleExamConfigLoader(unittest.TestCase):
    def test_loads_repository_exam_config(self) -> None:
        # Administrators may customize the role, threshold, and question text.
        config = load_role_exam_config(REPOSITORY_CONFIG_PATH)

        self.assertIsInstance(config, RoleExamConfig)
        self.assertEqual(config.schema_version, 1)
        self.assertEqual(len(config.questions), 20)

    def test_loads_twenty_question_placeholder_config(self) -> None:
        payload = valid_payload()
        for question in payload["questions"]:
            question["prompt"] = f"[TODO] {question['prompt']}"
        config = load_payload(payload)

        self.assertEqual(config.schema_version, 1)
        self.assertIsNone(config.role_id)
        self.assertEqual(config.required_percent, 80)
        self.assertEqual(len(config.questions), 20)
        self.assertEqual(
            tuple(question.id for question in config.questions),
            tuple(f"q{number:02d}" for number in range(1, 21)),
        )
        self.assertTrue(
            all(question.prompt.startswith("[TODO]") for question in config.questions)
        )
        self.assertTrue(
            all(len(question.choices) == 4 for question in config.questions)
        )

    def test_returns_frozen_dataclasses_with_tuple_collections(self) -> None:
        config = load_payload(valid_payload())

        self.assertIsInstance(config, RoleExamConfig)
        self.assertIsInstance(config.questions, tuple)
        self.assertIsInstance(config.questions[0], RoleExamQuestion)
        self.assertIsInstance(config.questions[0].choices, tuple)
        self.assertIsInstance(config.questions[0].choices[0], RoleExamChoice)
        with self.assertRaises(FrozenInstanceError):
            config.title = "changed"
        with self.assertRaises(FrozenInstanceError):
            config.questions[0].prompt = "changed"
        with self.assertRaises(FrozenInstanceError):
            config.questions[0].choices[0].text = "changed"

    def test_role_id_accepts_null_or_positive_decimal_string(self) -> None:
        for raw_role_id, expected in ((None, None), ("1", 1), ("00042", 42)):
            with self.subTest(role_id=raw_role_id):
                payload = valid_payload()
                payload["role_id"] = raw_role_id
                self.assertEqual(load_payload(payload).role_id, expected)

    def test_role_id_rejects_non_decimal_or_non_positive_values(self) -> None:
        invalid_values = (
            123,
            0,
            True,
            "",
            "0",
            "-1",
            "+1",
            "1.0",
            " 1",
            "1 ",
            "１２３",
            "not-an-id",
        )
        for role_id in invalid_values:
            with self.subTest(role_id=role_id):
                payload = valid_payload()
                payload["role_id"] = role_id
                with self.assertRaises(RoleExamConfigError):
                    load_payload(payload)

    def test_schema_version_must_be_integer_one(self) -> None:
        for schema_version in (0, 2, True, 1.0, "1", None):
            with self.subTest(schema_version=schema_version):
                payload = valid_payload()
                payload["schema_version"] = schema_version
                with self.assertRaises(RoleExamConfigError):
                    load_payload(payload)

    def test_required_percent_accepts_integer_boundaries(self) -> None:
        for required_percent in (1, 80, 100):
            with self.subTest(required_percent=required_percent):
                payload = valid_payload()
                payload["required_percent"] = required_percent
                self.assertEqual(
                    load_payload(payload).required_percent,
                    required_percent,
                )

    def test_required_percent_rejects_out_of_range_and_non_integer_values(self) -> None:
        for required_percent in (0, 101, -1, True, 80.0, "80", None):
            with self.subTest(required_percent=required_percent):
                payload = valid_payload()
                payload["required_percent"] = required_percent
                with self.assertRaises(RoleExamConfigError):
                    load_payload(payload)

    def test_title_and_instructions_are_nonblank_and_bounded(self) -> None:
        invalid_fields = (
            ("title", ""),
            ("title", " \n\t"),
            ("title", 123),
            ("title", "x" * (MAX_TITLE_LENGTH + 1)),
            ("instructions", ""),
            ("instructions", " \n\t"),
            ("instructions", []),
            ("instructions", "x" * (MAX_INSTRUCTIONS_LENGTH + 1)),
        )
        for field, value in invalid_fields:
            with self.subTest(field=field, value_type=type(value).__name__):
                payload = valid_payload()
                payload[field] = value
                with self.assertRaises(RoleExamConfigError):
                    load_payload(payload)

    def test_requires_exactly_twenty_unique_question_ids(self) -> None:
        too_few = valid_payload()
        too_few["questions"].pop()
        too_many = valid_payload()
        too_many["questions"].append(copy.deepcopy(too_many["questions"][-1]))
        duplicate = valid_payload()
        duplicate["questions"][1]["id"] = duplicate["questions"][0]["id"]

        for payload in (too_few, too_many, duplicate):
            with self.subTest(question_count=len(payload["questions"])):
                with self.assertRaises(RoleExamConfigError):
                    load_payload(payload)

    def test_question_ids_and_prompts_are_nonblank_and_bounded(self) -> None:
        invalid_fields = (
            ("id", ""),
            ("id", " " * 3),
            ("id", 1),
            ("id", "x" * (MAX_ID_LENGTH + 1)),
            ("prompt", ""),
            ("prompt", " \n"),
            ("prompt", None),
            ("prompt", "x" * (MAX_PROMPT_LENGTH + 1)),
        )
        for field, value in invalid_fields:
            with self.subTest(field=field, value_type=type(value).__name__):
                payload = valid_payload()
                payload["questions"][0][field] = value
                with self.assertRaises(RoleExamConfigError):
                    load_payload(payload)

    def test_each_question_requires_two_to_five_unique_choices(self) -> None:
        one_choice = valid_payload()
        one_choice["questions"][0]["choices"] = [
            one_choice["questions"][0]["choices"][0]
        ]
        six_choices = valid_payload()
        six_choices["questions"][0]["choices"].extend(
            [
                {"id": "e", "text": "Đáp án E"},
                {"id": "f", "text": "Đáp án F"},
            ]
        )
        duplicate = valid_payload()
        duplicate["questions"][0]["choices"][1]["id"] = "a"

        for payload in (one_choice, six_choices, duplicate):
            with self.assertRaises(RoleExamConfigError):
                load_payload(payload)

    def test_each_question_accepts_two_or_five_unique_choices(self) -> None:
        for choice_count in (2, 5):
            with self.subTest(choice_count=choice_count):
                payload = valid_payload()
                choices = payload["questions"][0]["choices"]
                if choice_count == 2:
                    payload["questions"][0]["choices"] = choices[:2]
                else:
                    choices.append({"id": "e", "text": "Đáp án E"})

                self.assertEqual(
                    len(load_payload(payload).questions[0].choices),
                    choice_count,
                )

    def test_choice_ids_and_text_are_nonblank_and_bounded(self) -> None:
        invalid_fields = (
            ("id", ""),
            ("id", " \t"),
            ("id", 1),
            ("id", "x" * (MAX_ID_LENGTH + 1)),
            ("text", ""),
            ("text", " \n"),
            ("text", None),
            ("text", "x" * (MAX_CHOICE_TEXT_LENGTH + 1)),
        )
        for field, value in invalid_fields:
            with self.subTest(field=field, value_type=type(value).__name__):
                payload = valid_payload()
                payload["questions"][0]["choices"][0][field] = value
                with self.assertRaises(RoleExamConfigError):
                    load_payload(payload)

    def test_correct_choice_id_must_reference_a_choice(self) -> None:
        for correct_choice_id in ("", "missing", None, 1):
            with self.subTest(correct_choice_id=correct_choice_id):
                payload = valid_payload()
                payload["questions"][0]["correct_choice_id"] = correct_choice_id
                with self.assertRaises(RoleExamConfigError):
                    load_payload(payload)

    def test_required_fields_and_json_container_types_are_validated(self) -> None:
        missing_title = valid_payload()
        del missing_title["title"]
        questions_object = valid_payload()
        questions_object["questions"] = {}
        choices_object = valid_payload()
        choices_object["questions"][0]["choices"] = {}

        for payload in (None, [], missing_title, questions_object, choices_object):
            with self.assertRaises(RoleExamConfigError):
                load_payload(payload)

    def test_loader_wraps_missing_malformed_and_invalid_utf8_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            missing_path = directory_path / "missing.json"
            malformed_path = directory_path / "malformed.json"
            malformed_path.write_text("{", encoding="utf-8")
            invalid_utf8_path = directory_path / "invalid-utf8.json"
            invalid_utf8_path.write_bytes(b"\xff")

            for path in (missing_path, malformed_path, invalid_utf8_path):
                with self.subTest(path=path.name):
                    with self.assertRaises(RoleExamConfigError):
                        load_role_exam_config(path)

    def test_loader_rejects_duplicate_json_object_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text(
                '{"schema_version": 1, "schema_version": 1}',
                encoding="utf-8",
            )

            with self.assertRaises(RoleExamConfigError):
                load_role_exam_config(path)


class TestRoleExamShufflingAndScoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_payload(valid_payload())

    def test_seeded_shuffle_is_repeatable_and_does_not_mutate_config(self) -> None:
        original_question_ids = tuple(question.id for question in self.config.questions)
        original_choice_ids = {
            question.id: tuple(choice.id for choice in question.choices)
            for question in self.config.questions
        }

        first = shuffled_questions(self.config, random.Random(8675309))
        second = shuffled_questions(self.config, random.Random(8675309))

        self.assertEqual(first, second)
        self.assertNotEqual(tuple(question.id for question in first), original_question_ids)
        self.assertEqual(
            tuple(question.id for question in self.config.questions),
            original_question_ids,
        )
        self.assertEqual(
            {
                question.id: tuple(choice.id for choice in question.choices)
                for question in self.config.questions
            },
            original_choice_ids,
        )

    def test_shuffle_preserves_stable_ids_text_and_choice_instances(self) -> None:
        shuffled = shuffled_questions(self.config, random.Random(42))
        original_by_id = {question.id: question for question in self.config.questions}

        self.assertEqual(
            {question.id for question in shuffled},
            set(original_by_id),
        )
        for question in shuffled:
            original = original_by_id[question.id]
            self.assertEqual(question.prompt, original.prompt)
            self.assertEqual(question.correct_choice_id, original.correct_choice_id)
            original_choices = {choice.id: choice for choice in original.choices}
            self.assertEqual(
                {choice.id for choice in question.choices},
                set(original_choices),
            )
            for choice in question.choices:
                self.assertIs(choice, original_choices[choice.id])

    def test_scores_answers_by_stable_ids_after_shuffling(self) -> None:
        shuffled = shuffled_questions(self.config, random.Random(99))
        answers = {
            question.id: question.correct_choice_id
            for question in self.config.questions
        }
        answers["q01"] = "not-a-choice"
        del answers["q02"]
        answers["not-a-question"] = "a"

        self.assertEqual(score_answers(self.config.questions, answers), 18)
        self.assertEqual(score_answers(shuffled, answers), 18)

    def test_required_correct_count_uses_ceiling_integer_math(self) -> None:
        cases = (
            (20, 80, 16),
            (3, 50, 2),
            (7, 1, 1),
            (7, 100, 7),
            (100, 99, 99),
        )
        for total, required_percent, expected in cases:
            with self.subTest(total=total, required_percent=required_percent):
                self.assertEqual(
                    required_correct_count(total, required_percent),
                    expected,
                )

    def test_integer_safe_pass_check_handles_threshold_edges(self) -> None:
        self.assertTrue(is_passing_score(16, 20, 80))
        self.assertFalse(is_passing_score(15, 20, 80))
        self.assertTrue(is_passing_score(2, 3, 50))
        self.assertFalse(is_passing_score(1, 3, 50))
        self.assertTrue(is_passing_score(1, 7, 1))

    def test_scoring_threshold_helpers_reject_invalid_inputs(self) -> None:
        invalid_thresholds = (
            (0, 80),
            (-1, 80),
            (True, 80),
            (20.0, 80),
            (20, 0),
            (20, 101),
            (20, True),
            (20, 80.0),
        )
        for total, required_percent in invalid_thresholds:
            with self.subTest(total=total, required_percent=required_percent):
                with self.assertRaises(ValueError):
                    required_correct_count(total, required_percent)

        for correct in (-1, 21, True, 16.0):
            with self.subTest(correct=correct):
                with self.assertRaises(ValueError):
                    is_passing_score(correct, 20, 80)


class TestUnsafeRolePermissions(unittest.TestCase):
    def test_denylist_has_the_expected_stable_order(self) -> None:
        self.assertEqual(
            UNSAFE_ROLE_PERMISSION_NAMES,
            (
                "administrator",
                "manage_guild",
                "manage_roles",
                "manage_channels",
                "kick_members",
                "ban_members",
                "moderate_members",
                "manage_messages",
                "pin_messages",
                "bypass_slowmode",
                "view_audit_log",
                "view_guild_insights",
                "view_creator_monetization_analytics",
                "mention_everyone",
                "manage_webhooks",
                "manage_events",
                "create_events",
                "manage_expressions",
                "create_expressions",
                "manage_threads",
                "manage_nicknames",
                "mute_members",
                "deafen_members",
                "move_members",
                "set_voice_channel_status",
            ),
        )

    def test_detects_truthy_denied_permissions_in_denylist_order(self) -> None:
        permissions = SimpleNamespace(
            move_members=True,
            administrator=True,
            manage_roles=False,
            send_messages=True,
            view_audit_log=1,
        )

        self.assertEqual(
            unsafe_role_permission_names(permissions),
            ("administrator", "view_audit_log", "move_members"),
        )

    def test_missing_or_false_permission_attributes_are_safe(self) -> None:
        self.assertEqual(unsafe_role_permission_names(object()), ())
        self.assertEqual(
            unsafe_role_permission_names(
                SimpleNamespace(
                    administrator=False,
                    manage_roles=False,
                    send_messages=True,
                )
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main()
