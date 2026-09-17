import unittest
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from pymongo.errors import DuplicateKeyError

from cogs.cultivation import _cultivation_helpers as rules
from cogs.cultivation._cultivation_ui import (
    PANEL_INVENTORY,
    PANEL_MARKET,
    PANEL_PATH,
    PANEL_PVE,
    PANEL_REALM,
)
from cogs.cultivation.cultivation import (
    DASHBOARD_TIMEOUT_SECONDS,
    CultivationCog,
    CultivationUnavailable,
    CultivationView,
    MutationBusy,
    StateChange,
)


NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)


def fresh_state() -> dict:
    return rules.default_state(NOW)


def major_breakthrough_state(*, failures: int = 0) -> dict:
    state = fresh_state()
    state.update(
        {
            "stage_index": 9,
            "qi": 2_000,
            "spirit_stones": 2_000,
            "tower_floor": 10,
            "breakthrough_failures": failures,
        }
    )
    return state


class FakeCollection:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = deepcopy(documents or [])
        self.index_calls: list[tuple[tuple, dict]] = []
        self.index_error: Exception | None = None
        self.cas_failures = 0

    def create_index(self, *args, **kwargs) -> str:
        self.index_calls.append((args, kwargs))
        if self.index_error is not None:
            raise self.index_error
        return str(kwargs.get("name", "index"))

    def aggregate(self, pipeline: list[dict]) -> list[dict]:
        counts: dict[int, int] = {}
        for document in self.documents:
            user_id = document.get("user_id")
            if user_id is not None:
                counts[int(user_id)] = counts.get(int(user_id), 0) + 1
        return [
            {"_id": user_id, "count": count}
            for user_id, count in counts.items()
            if count > 1
        ][:20]

    def find_one(self, query: dict) -> dict | None:
        document = next(
            (item for item in self.documents if self._matches(item, query)),
            None,
        )
        return deepcopy(document) if document is not None else None

    def find(self, query: dict) -> "FakeCursor":
        return FakeCursor(
            [
                deepcopy(document)
                for document in self.documents
                if self._matches(document, query)
            ]
        )

    def update_one(
        self,
        query: dict,
        update: dict,
        *,
        upsert: bool = False,
    ) -> SimpleNamespace:
        document = next(
            (item for item in self.documents if self._matches(item, query)),
            None,
        )
        inserted = False
        if document is None and upsert:
            document = {
                key: deepcopy(value)
                for key, value in query.items()
                if "." not in key and not isinstance(value, dict)
            }
            self.documents.append(document)
            inserted = True
        if document is None:
            return SimpleNamespace(modified_count=0, upserted_id=None)

        before = deepcopy(document)
        if inserted:
            document.update(deepcopy(update.get("$setOnInsert", {})))
        document.update(deepcopy(update.get("$set", {})))
        for key, amount in update.get("$inc", {}).items():
            document[key] = int(document.get(key, 0)) + int(amount)
        return SimpleNamespace(
            modified_count=int(document != before),
            upserted_id=1 if inserted else None,
        )

    def find_one_and_update(
        self,
        query: dict,
        update: dict,
        **kwargs,
    ) -> dict | None:
        if self.cas_failures:
            self.cas_failures -= 1
            return None
        document = next(
            (item for item in self.documents if self._matches(item, query)),
            None,
        )
        if document is None:
            return None
        document.update(deepcopy(update.get("$set", {})))
        for key, amount in update.get("$inc", {}).items():
            document[key] = int(document.get(key, 0)) + int(amount)
        return deepcopy(document)

    def insert_one(self, document: dict) -> SimpleNamespace:
        request_id = document.get("request_id")
        if request_id is not None and any(
            item.get("request_id") == request_id for item in self.documents
        ):
            raise DuplicateKeyError("duplicate request")
        self.documents.append(deepcopy(document))
        return SimpleNamespace(inserted_id=len(self.documents))

    @staticmethod
    def _matches(document: dict, query: dict) -> bool:
        for key, expected in query.items():
            if key == "cultivation.version":
                cultivation = document.get("cultivation")
                actual = cultivation.get("version") if isinstance(cultivation, dict) else None
                if isinstance(expected, dict) and "$exists" in expected:
                    if (actual is not None) != bool(expected["$exists"]):
                        return False
                elif actual != expected:
                    return False
                continue
            if key == "cultivation" and isinstance(expected, dict) and "$exists" in expected:
                if ("cultivation" in document) != bool(expected["$exists"]):
                    return False
                continue
            actual: object = document
            for part in key.split("."):
                if not isinstance(actual, dict) or part not in actual:
                    actual = None
                    break
                actual = actual[part]
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"]:
                    return False
                continue
            if isinstance(expected, dict) and "$gte" in expected:
                if int(actual or 0) < int(expected["$gte"]):
                    return False
                continue
            if actual != expected:
                return False
        return True


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    @staticmethod
    def _value(document: dict, dotted_key: str) -> object:
        value: object = document
        for part in dotted_key.split("."):
            if not isinstance(value, dict):
                return 0
            value = value.get(part, 0)
        return value

    def sort(self, specification: list[tuple[str, int]]) -> "FakeCursor":
        for key, direction in reversed(specification):
            self.documents.sort(
                key=lambda document: self._value(document, key),
                reverse=direction < 0,
            )
        return self

    def limit(self, amount: int) -> "FakeCursor":
        self.documents = self.documents[:amount]
        return self

    def __iter__(self):
        return iter(self.documents)


class FakeDatabase:
    def __init__(self, accounts: list[dict] | None = None) -> None:
        self.collections = {
            "user_accounts": FakeCollection(accounts),
            "cultivation_events": FakeCollection(),
            "transaction_logs": FakeCollection(),
        }

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


def make_cog(accounts: list[dict] | None = None) -> tuple[CultivationCog, FakeDatabase]:
    database = FakeDatabase(accounts)
    cog = CultivationCog(SimpleNamespace(db=database))
    return cog, database


def fake_interaction(user_id: int = 42, display_name: str = "Đạo Hữu"):
    return SimpleNamespace(
        id=123,
        user=SimpleNamespace(id=user_id, display_name=display_name),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
        ),
    )


def child_labels(view: CultivationView) -> list[str]:
    labels: list[str] = []
    for child in view.children:
        label = getattr(child, "label", None)
        if label:
            labels.append(str(label))
            continue
        placeholder = getattr(child, "placeholder", None)
        if placeholder:
            labels.append(str(placeholder))
    return labels


class TestCultivationCatalog(unittest.TestCase):
    def test_initial_release_has_complete_progression_catalog(self) -> None:
        self.assertEqual(len(rules.STAGES), 18)
        self.assertEqual(rules.STAGES[0].key, "pham_nhan")
        self.assertEqual(rules.STAGES[-1].key, "kim_dan_vien_man")
        self.assertEqual(len({stage.key for stage in rules.STAGES}), 18)

        self.assertEqual(set(rules.PATH_NAMES), {"kiem", "the", "dan"})
        self.assertEqual(
            set(rules.GEAR_SLOT_NAMES),
            {"weapon", "robe", "artifact", "ring"},
        )
        for path in rules.PATH_NAMES:
            talents = [talent for talent in rules.TALENTS.values() if talent.path == path]
            self.assertEqual(len(talents), 3)
            self.assertTrue(all(talent.max_rank == 5 for talent in talents))

    def test_tower_and_expedition_catalogs_match_release_scope(self) -> None:
        self.assertEqual(tuple(floor.floor for floor in rules.TOWER_FLOORS), tuple(range(1, 31)))
        self.assertEqual(
            {floor.floor for floor in rules.TOWER_FLOORS if floor.boss},
            {5, 10, 15, 20, 25, 30},
        )
        self.assertEqual(rules.EXPEDITION_HOURS, (2, 4, 8))
        self.assertEqual(set(rules.EXPEDITION_ZONES), {"linhduoc", "cokhoang", "yeuthuson"})

    def test_market_has_permanent_stock_and_four_daily_offers(self) -> None:
        offers = rules.daily_market(date(2026, 8, 10))
        repeated = rules.daily_market(date(2026, 8, 10))
        next_day = rules.daily_market(date(2026, 8, 11))

        self.assertEqual(offers, repeated)
        self.assertEqual(len({item.key for item in offers}), len(offers))
        self.assertEqual(sum(item.permanent_market for item in offers), 4)
        self.assertEqual(sum(not item.permanent_market for item in offers), 4)
        self.assertNotEqual(
            tuple(item.key for item in offers if not item.permanent_market),
            tuple(item.key for item in next_day if not item.permanent_market),
        )


class TestCultivationFormatting(unittest.TestCase):
    def test_item_and_recipe_summaries_are_deterministic(self) -> None:
        item = rules.ITEMS["tu_linh_chau"]
        self.assertIn("Tu Vi", rules.item_stat_summary(item))
        self.assertEqual(rules.clip_text("abcdef", 4), "abc…")
        recipe = rules.RECIPES["huyen_thiet_kiem"]
        self.assertIn("500 LT", rules.recipe_cost_text(recipe))
        discounted = fresh_state()
        discounted.update({"path": "dan", "talents": {"luyen_dan": 5}})
        self.assertIn("400 LT", rules.recipe_cost_text(recipe, discounted))

    def test_realm_and_tower_maps_mark_current_progress(self) -> None:
        state = fresh_state()
        state["stage_index"] = 2
        realm = rules.realm_map_text(state)
        self.assertIn("**▶Tầng 2**", realm)
        self.assertIn("✓Tầng 1", realm)
        self.assertIn("Luyện Khí:", realm)

        tower = rules.tower_map_text(10)
        self.assertIn(" 1–5:", tower)
        self.assertIn("👑", tower)
        self.assertIn("⬜", tower)
        self.assertEqual(rules.format_duration_seconds(90 * 60), "1h 30m")


class TestCultivationState(unittest.TestCase):
    def test_default_and_normalized_state_are_versioned_and_bounded(self) -> None:
        state = rules.default_state(NOW)
        self.assertEqual(state["schema_version"], rules.SCHEMA_VERSION)
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["stage_index"], 0)
        self.assertEqual(state["cave_level"], 1)
        self.assertTrue(state["profile_public"])
        self.assertEqual(state["processed_requests"], [])

        corrupted = {
            "stage_index": 99,
            "qi": -5,
            "spirit_stones": "bad",
            "cave_level": 99,
            "focus": "invalid",
            "path": "invalid",
            "talent_points": -1,
            "boss_pity": 99,
            "tower_floor": 99,
            "owned_items": ["thanh_truc_kiem", "missing", "thanh_truc_kiem"],
            "equipped": {"robe": "thanh_truc_kiem", "weapon": "missing"},
            "processed_requests": list(range(50)),
        }
        normalized = rules.normalize_state(corrupted, NOW)

        self.assertEqual(normalized["stage_index"], len(rules.STAGES) - 1)
        self.assertEqual(normalized["qi"], 0)
        self.assertEqual(normalized["spirit_stones"], 0)
        self.assertEqual(normalized["cave_level"], rules.MAX_CAVE_LEVEL)
        self.assertEqual(normalized["focus"], rules.FOCUS_BALANCED)
        self.assertIsNone(normalized["path"])
        self.assertEqual(normalized["boss_pity"], 9)
        self.assertEqual(normalized["tower_floor"], 30)
        self.assertEqual(normalized["owned_items"], ["thanh_truc_kiem"])
        self.assertTrue(all(value is None for value in normalized["equipped"].values()))
        self.assertEqual(len(normalized["processed_requests"]), rules.MAX_PROCESSED_REQUESTS)

    def test_normalization_rejects_invalid_session_and_future_safe_defaults(self) -> None:
        state = rules.normalize_state(
            {
                "session": {
                    "kind": "expedition",
                    "started_at": NOW,
                    "settled_at": NOW,
                    "ends_at": NOW + timedelta(hours=3),
                    "zone": "missing",
                    "hours": 3,
                }
            },
            NOW,
        )
        self.assertIsNone(state["session"])


class TestAfkCultivation(unittest.TestCase):
    def test_focus_rates_use_integer_arithmetic(self) -> None:
        expected = {
            rules.FOCUS_BALANCED: (5, 3),
            rules.FOCUS_QI: (6, 1),
            rules.FOCUS_STONES: (3, 4),
        }
        for focus, gains in expected.items():
            with self.subTest(focus=focus):
                state = fresh_state()
                state["focus"] = focus
                qi, stones, _, _ = rules.calculate_afk_rewards(state, rules.SECONDS_PER_HOUR)
                self.assertEqual((qi, stones), gains)

    def test_fractional_remainders_make_split_claims_equal_one_claim(self) -> None:
        state = fresh_state()
        state["focus"] = rules.FOCUS_QI
        one_shot = rules.calculate_afk_rewards(state, 1_200)

        first = rules.calculate_afk_rewards(state, 600)
        state["qi_remainder"] = first[2]
        state["stone_remainder"] = first[3]
        second = rules.calculate_afk_rewards(state, 600)

        self.assertEqual(first[0] + second[0], one_shot[0])
        self.assertEqual(first[1] + second[1], one_shot[1])
        self.assertEqual(second[2:], one_shot[2:])

    def test_storage_cap_grows_from_24_to_at_most_48_hours(self) -> None:
        state = fresh_state()
        self.assertEqual(rules.storage_cap_hours(state), 24)

        state["cave_level"] = 7
        self.assertEqual(rules.storage_cap_hours(state), 48)

        state["path"] = "the"
        state["talents"] = {"ben_bi": 5}
        state["owned_items"] = ["nhan_tinh_ha"]
        state["equipped"]["ring"] = "nhan_tinh_ha"
        self.assertEqual(rules.storage_cap_hours(state), 48)

    def test_each_cave_level_preserves_its_five_percent_fraction(self) -> None:
        state = fresh_state()
        baseline = rules.calculate_afk_rewards(state, 20 * rules.SECONDS_PER_HOUR)
        state["cave_level"] = 2
        upgraded = rules.calculate_afk_rewards(state, 20 * rules.SECONDS_PER_HOUR)

        self.assertEqual(baseline[:2], (100, 60))
        self.assertEqual(upgraded[:2], (105, 63))

    def test_afk_reward_is_capped_and_reports_discarded_time(self) -> None:
        state = rules.start_meditation(fresh_state(), NOW)
        settlement = rules.settle_state(state, NOW + timedelta(hours=30))

        self.assertEqual(settlement.settled_seconds, 24 * rules.SECONDS_PER_HOUR)
        self.assertEqual(settlement.capped_seconds, 6 * rules.SECONDS_PER_HOUR)
        self.assertEqual(settlement.qi_gained, 24 * 5)
        self.assertEqual(settlement.stones_gained, 24 * 3)

    def test_micro_claim_does_not_advance_session(self) -> None:
        state = rules.start_meditation(fresh_state(), NOW)
        too_early = rules.settle_state(state, NOW + timedelta(seconds=599))

        self.assertEqual(too_early.settled_seconds, 0)
        self.assertEqual(too_early.state["session"]["settled_at"], NOW.replace(tzinfo=None))

        eligible = rules.settle_state(state, NOW + timedelta(seconds=600))
        self.assertEqual(eligible.settled_seconds, 600)
        self.assertEqual(
            eligible.state["session"]["settled_at"],
            (NOW + timedelta(seconds=600)).replace(tzinfo=None),
        )

    def test_future_timestamp_never_creates_rewards(self) -> None:
        future = NOW + timedelta(hours=1)
        state = rules.start_meditation(fresh_state(), future)
        settlement = rules.settle_state(state, NOW)

        self.assertEqual(settlement.qi_gained, 0)
        self.assertEqual(settlement.stones_gained, 0)
        self.assertEqual(settlement.state["qi"], 0)

    def test_sub_ten_minute_focus_change_preserves_old_focus_progress(self) -> None:
        state = rules.start_meditation(fresh_state(), NOW, rules.FOCUS_BALANCED)
        elapsed = 9 * 60
        expected = rules.calculate_afk_rewards(state, elapsed)

        changed = rules.change_focus(
            state,
            rules.FOCUS_QI,
            NOW + timedelta(seconds=elapsed),
        )

        self.assertEqual(changed.settled_seconds, elapsed)
        self.assertEqual(changed.state["qi_remainder"], expected[2])
        self.assertEqual(changed.state["stone_remainder"], expected[3])
        self.assertEqual(changed.state["focus"], rules.FOCUS_QI)
        self.assertEqual(
            changed.state["session"]["settled_at"],
            (NOW + timedelta(seconds=elapsed)).replace(tzinfo=None),
        )

    def test_persisted_timestamp_survives_restart_and_double_claim_is_empty(self) -> None:
        persisted = deepcopy(rules.start_meditation(fresh_state(), NOW))
        restored = deepcopy(persisted)
        first = rules.settle_state(restored, NOW + timedelta(hours=2))
        repeated = rules.settle_state(first.state, NOW + timedelta(hours=2))

        self.assertEqual((first.qi_gained, first.stones_gained), (10, 6))
        self.assertEqual((repeated.qi_gained, repeated.stones_gained), (0, 0))
        self.assertEqual(repeated.state["qi"], first.state["qi"])
        self.assertEqual(repeated.state["spirit_stones"], first.state["spirit_stones"])


class TestBreakthroughs(unittest.TestCase):
    def test_minor_breakthrough_is_guaranteed_and_keeps_overflow(self) -> None:
        state = fresh_state()
        state["qi"] = 150
        result = rules.attempt_breakthrough(state, NOW, roll=99)

        self.assertTrue(result.success)
        self.assertEqual(result.chance, 100)
        self.assertEqual(result.state["stage_index"], 1)
        self.assertEqual(result.state["qi"], 50)
        self.assertEqual(result.state["talent_points"], 1)

    def test_major_failure_keeps_qi_stage_and_equipment(self) -> None:
        state = major_breakthrough_state()
        state["owned_items"] = ["thanh_truc_kiem"]
        state["equipped"]["weapon"] = "thanh_truc_kiem"
        before_qi = state["qi"]
        result = rules.attempt_breakthrough(state, NOW, roll=99)

        self.assertFalse(result.success)
        self.assertEqual(result.chance, 70)
        self.assertEqual(result.fee_paid, 250)
        self.assertEqual(result.state["qi"], before_qi)
        self.assertEqual(result.state["stage_index"], 9)
        self.assertEqual(result.state["owned_items"], ["thanh_truc_kiem"])
        self.assertEqual(result.state["equipped"]["weapon"], "thanh_truc_kiem")
        self.assertEqual(result.state["breakthrough_failures"], 1)
        self.assertEqual(result.state["breakthrough_retry_at"], NOW.replace(tzinfo=None) + timedelta(hours=1))

        with self.assertRaises(rules.RuleError):
            rules.attempt_breakthrough(result.state, NOW + timedelta(minutes=59), roll=0)

    def test_soft_pity_guarantees_fourth_major_attempt(self) -> None:
        state = major_breakthrough_state(failures=3)
        state["qi"] = 1_500
        result = rules.attempt_breakthrough(state, NOW, roll=99)

        self.assertTrue(result.success)
        self.assertEqual(result.chance, 100)
        self.assertEqual(result.state["stage_index"], 10)
        self.assertEqual(result.state["qi"], 60)
        self.assertEqual(result.state["spirit_stones"], 1_000)
        self.assertEqual(result.state["breakthrough_failures"], 0)
        self.assertEqual(result.state["talent_points"], 2)

    def test_major_breakthrough_requires_corresponding_tower_floor(self) -> None:
        state = major_breakthrough_state()
        state["tower_floor"] = 9
        with self.assertRaises(rules.RuleError):
            rules.attempt_breakthrough(state, NOW, roll=0)


class TestPathsAndTalents(unittest.TestCase):
    def test_path_selection_requires_luyen_khi_one(self) -> None:
        with self.assertRaises(rules.RuleError):
            rules.select_path(fresh_state(), "kiem")

        state = fresh_state()
        state["stage_index"] = 1
        selected = rules.select_path(state, "kiem")
        self.assertEqual(selected["path"], "kiem")

    def test_talent_spending_is_path_scoped_and_capped_at_five(self) -> None:
        state = fresh_state()
        state.update({"stage_index": 1, "path": "kiem", "talent_points": 6})
        spent = rules.spend_talent(state, "pha_canh", 5)

        self.assertEqual(spent["talents"]["pha_canh"], 5)
        self.assertEqual(spent["talent_points"], 1)
        with self.assertRaises(rules.RuleError):
            rules.spend_talent(spent, "pha_canh", 1)
        with self.assertRaises(rules.RuleError):
            rules.spend_talent(state, "luyen_dan", 1)

    def test_reset_refunds_points_charges_cost_and_has_seven_day_cooldown(self) -> None:
        state = fresh_state()
        state.update(
            {
                "stage_index": 10,
                "path": "the",
                "talent_points": 1,
                "talents": {"the_phach": 2, "ben_bi": 3},
                "spirit_stones": 5_000,
            }
        )
        reset = rules.reset_path(state, NOW)

        self.assertIsNone(reset["path"])
        self.assertEqual(reset["talents"], {})
        self.assertEqual(reset["talent_points"], 6)
        self.assertEqual(reset["spirit_stones"], 3_000)
        self.assertEqual(reset["path_reset_at"], NOW.replace(tzinfo=None) + timedelta(days=7))
        with self.assertRaises(rules.RuleError):
            rules.reset_path(reset, NOW + timedelta(days=6))

    def test_talent_effect_limits_are_applied(self) -> None:
        sword = fresh_state()
        sword.update({"stage_index": 9, "path": "kiem", "talents": {"pha_canh": 5}})
        self.assertEqual(rules.breakthrough_chance(sword), 80)

        body = fresh_state()
        body.update({"stage_index": 9, "path": "the", "talents": {"ho_mach": 5}})
        self.assertEqual(rules.failure_fee(body), 100)

        alchemist = fresh_state()
        alchemist.update({"path": "dan", "talents": {"luyen_dan": 5}})
        self.assertEqual(rules.talent_effects(alchemist)["craft_discount_pct"], 20)


class TestEquipmentAndCrafting(unittest.TestCase):
    def test_buy_equip_and_salvage_use_fixed_item_slot(self) -> None:
        item = rules.ITEMS["thanh_truc_kiem"]
        state = fresh_state()
        state["spirit_stones"] = item.price

        bought = rules.buy_item(state, item.key, date(2026, 8, 10))
        self.assertEqual(bought["spirit_stones"], 0)
        self.assertIn(item.key, bought["owned_items"])

        equipped = rules.equip_item(bought, item.key)
        self.assertEqual(equipped["equipped"][item.slot], item.key)

        salvaged = rules.salvage_item(equipped, item.key)
        self.assertNotIn(item.key, salvaged["owned_items"])
        self.assertIsNone(salvaged["equipped"][item.slot])
        self.assertEqual(salvaged["materials"]["manh_phap_bao"], item.salvage_fragments)

    def test_purchase_cannot_duplicate_or_overspend(self) -> None:
        item = rules.ITEMS["thanh_truc_kiem"]
        state = fresh_state()
        with self.assertRaises(rules.RuleError):
            rules.buy_item(state, item.key, date(2026, 8, 10))

        state["spirit_stones"] = item.price * 2
        bought = rules.buy_item(state, item.key, date(2026, 8, 10))
        with self.assertRaises(rules.RuleError):
            rules.buy_item(bought, item.key, date(2026, 8, 10))

    def test_crafting_is_guaranteed_and_consumes_fixed_costs(self) -> None:
        recipe = rules.RECIPES["huyen_thiet_kiem"]
        state = fresh_state()
        state["spirit_stones"] = recipe.stone_cost
        for material, amount in recipe.materials.items():
            state["materials"][material] = amount

        crafted = rules.craft_item(state, recipe.key)
        self.assertIn(recipe.result_item, crafted["owned_items"])
        self.assertEqual(crafted["spirit_stones"], 0)
        for material in recipe.materials:
            self.assertEqual(crafted["materials"][material], 0)

        with self.assertRaises(rules.RuleError):
            rules.craft_item(crafted, recipe.key)

    def test_alchemist_crafting_discount_uses_ceiling_integer_costs(self) -> None:
        recipe = rules.RECIPES["huyen_thiet_kiem"]
        state = fresh_state()
        state.update({"path": "dan", "talents": {"luyen_dan": 5}, "spirit_stones": 10_000})
        for material in state["materials"]:
            state["materials"][material] = 100

        crafted = rules.craft_item(state, recipe.key)
        self.assertEqual(crafted["spirit_stones"], 10_000 - 400)
        self.assertEqual(crafted["materials"]["huyen_thiet"], 100 - 10)
        self.assertEqual(crafted["materials"]["manh_phap_bao"], 100 - 2)


class TestTowerAndExpeditions(unittest.TestCase):
    def test_tower_is_sequential_deterministic_and_one_time(self) -> None:
        state = fresh_state()
        state["stage_index"] = len(rules.STAGES) - 1
        cleared = rules.clear_tower_floor(state)

        self.assertEqual(cleared["tower_floor"], 1)
        self.assertEqual(cleared["spirit_stones"], rules.TOWER_FLOORS[0].stone_reward)
        with self.assertRaises(rules.RuleError):
            rules.clear_tower_floor(cleared, 1)

    def test_boss_floor_grants_its_fixed_material_reward(self) -> None:
        state = fresh_state()
        state.update({"stage_index": len(rules.STAGES) - 1, "tower_floor": 4})
        state["owned_items"] = ["xich_tieu_kiem"]
        state["equipped"]["weapon"] = "xich_tieu_kiem"
        cleared = rules.clear_tower_floor(state)
        floor = rules.TOWER_FLOORS[4]

        self.assertTrue(floor.boss)
        self.assertEqual(cleared["tower_floor"], 5)
        self.assertEqual(cleared["materials"][floor.material], floor.material_amount)

    def test_expedition_supports_two_four_and_eight_hours(self) -> None:
        state = fresh_state()
        state["stage_index"] = 4
        state = rules.start_meditation(state, NOW, rules.FOCUS_QI)
        for hours in rules.EXPEDITION_HOURS:
            with self.subTest(hours=hours):
                started = rules.start_expedition(state, "linhduoc", hours, NOW)
                self.assertEqual(started.state["session"]["hours"], hours)
                self.assertEqual(
                    started.state["session"]["ends_at"] - NOW.replace(tzinfo=None),
                    timedelta(hours=hours),
                )

        with self.assertRaises(rules.RuleError):
            rules.start_expedition(state, "linhduoc", 3, NOW)

    def test_expedition_cannot_overlap_and_resumes_previous_focus(self) -> None:
        state = fresh_state()
        state["stage_index"] = 4
        state = rules.start_meditation(state, NOW, rules.FOCUS_STONES)
        started = rules.start_expedition(state, "cokhoang", 2, NOW)

        with self.assertRaises(rules.RuleError):
            rules.start_expedition(started.state, "linhduoc", 2, NOW)

        unfinished = rules.settle_state(started.state, NOW + timedelta(hours=1))
        self.assertFalse(unfinished.expedition_completed)
        completed = rules.settle_state(started.state, NOW + timedelta(hours=2))
        self.assertTrue(completed.expedition_completed)
        self.assertEqual(completed.state["session"]["kind"], "meditation")
        self.assertEqual(completed.state["session"]["focus"], rules.FOCUS_STONES)
        self.assertGreater(completed.stones_gained, 0)

    def test_sub_ten_minute_progress_is_preserved_when_expedition_starts(self) -> None:
        state = fresh_state()
        state["stage_index"] = 4
        state = rules.start_meditation(state, NOW, rules.FOCUS_BALANCED)
        elapsed = 9 * 60
        expected = rules.calculate_afk_rewards(state, elapsed)

        started = rules.start_expedition(
            state,
            "linhduoc",
            2,
            NOW + timedelta(seconds=elapsed),
        )

        self.assertEqual(started.settled_seconds, elapsed)
        self.assertEqual(started.state["qi_remainder"], expected[2])
        self.assertEqual(started.state["stone_remainder"], expected[3])
        self.assertEqual(started.state["session"]["kind"], "expedition")

    def test_cancel_expedition_has_no_expedition_reward(self) -> None:
        state = fresh_state()
        state["stage_index"] = 4
        state = rules.start_meditation(state, NOW, rules.FOCUS_QI)
        started = rules.start_expedition(state, "cokhoang", 8, NOW)
        cancelled = rules.cancel_expedition(started.state, NOW + timedelta(hours=1))

        self.assertEqual(cancelled["session"]["kind"], "meditation")
        self.assertEqual(cancelled["focus"], rules.FOCUS_QI)
        self.assertEqual(cancelled["materials"]["huyen_thiet"], 0)

    def test_completed_expedition_cannot_be_cancelled_without_claiming(self) -> None:
        state = fresh_state()
        state["stage_index"] = 1
        state = rules.start_meditation(state, NOW)
        expedition = rules.start_expedition(state, "linhduoc", 2, NOW)

        with self.assertRaisesRegex(rules.RuleError, "claim"):
            rules.cancel_expedition(
                expedition.state,
                NOW + timedelta(hours=2),
            )

    def test_tenth_eligible_gear_run_guarantees_drop_and_duplicate_salvages(self) -> None:
        state = fresh_state()
        state.update({"stage_index": 4, "boss_pity": 9})
        state = rules.start_meditation(state, NOW)
        first = rules.start_expedition(state, "yeuthuson", 2, NOW)
        claimed = rules.settle_state(first.state, NOW + timedelta(hours=2), rng=lambda stop: 0)

        self.assertTrue(claimed.expedition_completed)
        self.assertIsNotNone(claimed.dropped_item)
        dropped = rules.ITEMS[claimed.dropped_item]
        self.assertTrue(dropped.boss_drop)
        self.assertLessEqual(dropped.min_stage, 4)
        self.assertEqual(claimed.state["boss_pity"], 0)

        next_start = rules.start_expedition(claimed.state, "yeuthuson", 2, NOW + timedelta(hours=2))
        next_start.state["boss_pity"] = 9
        duplicate = rules.settle_state(
            next_start.state,
            NOW + timedelta(hours=4),
            rng=lambda stop: 0,
        )
        self.assertIsNone(duplicate.dropped_item)
        self.assertGreater(duplicate.duplicate_fragments, 0)
        self.assertEqual(duplicate.state["boss_pity"], 0)


class TestCultivationExchange(unittest.TestCase):
    def test_week_key_resets_at_monday_midnight_ict(self) -> None:
        before = datetime(2026, 8, 9, 16, 59, tzinfo=timezone.utc)
        boundary = datetime(2026, 8, 9, 17, 0, tzinfo=timezone.utc)

        self.assertEqual(rules.week_key_ict(before), "2026-08-03")
        self.assertEqual(rules.week_key_ict(boundary), "2026-08-10")

    def test_buy_stones_uses_rate_and_weekly_cap(self) -> None:
        state = fresh_state()
        state["tc_spent_week"] = 49
        updated, tc_delta = rules.exchange_buy_stones(state, 1, NOW)

        self.assertEqual(tc_delta, -1)
        self.assertEqual(updated["spirit_stones"], 10)
        self.assertEqual(updated["tc_spent_week"], 50)
        with self.assertRaises(rules.RuleError):
            rules.exchange_buy_stones(updated, 1, NOW)

    def test_sell_stones_uses_rate_and_weekly_cap(self) -> None:
        state = fresh_state()
        state["spirit_stones"] = 400
        updated, tc_delta = rules.exchange_sell_stones(state, 400, NOW)

        self.assertEqual(tc_delta, 20)
        self.assertEqual(updated["spirit_stones"], 0)
        self.assertEqual(updated["tc_earned_week"], 20)
        with self.assertRaises(rules.RuleError):
            rules.exchange_sell_stones(updated, 20, NOW)

    def test_old_week_counters_reset_and_exchange_has_no_arbitrage(self) -> None:
        state = fresh_state()
        state.update(
            {
                "exchange_week": "2026-08-03",
                "tc_spent_week": 50,
                "tc_earned_week": 20,
            }
        )
        bought, tc_delta = rules.exchange_buy_stones(state, 1, NOW)

        self.assertEqual(tc_delta, -1)
        self.assertEqual(bought["tc_spent_week"], 1)
        self.assertEqual(bought["tc_earned_week"], 0)
        self.assertEqual(bought["spirit_stones"], 10)
        with self.assertRaises(rules.RuleError):
            rules.exchange_sell_stones(bought, 10, NOW)


class TestCultivationPersistence(unittest.TestCase):
    def test_profile_creation_is_repeatable_but_only_creates_once(self) -> None:
        cog, database = make_cog()

        created_state, created = cog.create_profile(42, NOW, "profile:one")
        repeated_state, repeated = cog.create_profile(42, NOW, "profile:two")

        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(created_state["session"]["kind"], "meditation")
        self.assertEqual(repeated_state["stage_index"], created_state["stage_index"])
        self.assertEqual(len(database["user_accounts"].documents), 1)
        self.assertEqual(len(database["cultivation_events"].documents), 1)
        self.assertEqual(
            database["cultivation_events"].documents[0]["event_type"],
            "profile_start",
        )

    def test_null_profile_is_repaired_without_replacing_the_account(self) -> None:
        cog, database = make_cog(
            [{"user_id": 42, "balance": 77, "cultivation": None}]
        )

        state, created = cog.create_profile(42, NOW, "repair-null")

        self.assertTrue(created)
        self.assertEqual(state["session"]["kind"], "meditation")
        self.assertEqual(database["user_accounts"].documents[0]["balance"], 77)

    def test_request_id_prevents_double_credit_and_duplicate_audit(self) -> None:
        state = fresh_state()
        account = {"user_id": 42, "balance": 5, "cultivation": state}
        cog, database = make_cog([account])

        def grant(state: dict, balance: int) -> StateChange:
            state["spirit_stones"] += 100
            return StateChange(state, "test_grant", "granted")

        first = cog._mutate(42, "same-request", NOW, grant)
        repeated = cog._mutate(42, "same-request", NOW, grant)

        self.assertFalse(first.duplicate)
        self.assertTrue(repeated.duplicate)
        self.assertEqual(repeated.state["spirit_stones"], 100)
        self.assertEqual(repeated.state["version"], 2)
        self.assertEqual(len(database["cultivation_events"].documents), 1)

    def test_audit_receipt_keeps_aged_request_idempotent(self) -> None:
        state = fresh_state()
        state["processed_requests"] = [
            f"later-{index}" for index in range(rules.MAX_PROCESSED_REQUESTS)
        ]
        cog, database = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        database["cultivation_events"].documents.append(
            {"request_id": "aged-request", "user_id": 42}
        )

        def grant(current: dict, balance: int) -> StateChange:
            current["spirit_stones"] += 999
            return StateChange(current, "test_grant", "granted")

        result = cog._mutate(42, "aged-request", NOW, grant)

        self.assertTrue(result.duplicate)
        self.assertEqual(result.state["spirit_stones"], 0)

    def test_repeated_gameplay_actions_are_idempotent(self) -> None:
        claim_state = rules.start_meditation(fresh_state(), NOW)
        purchase_state = fresh_state()
        purchase_state["spirit_stones"] = 1_000
        expedition_state = fresh_state()
        expedition_state["stage_index"] = 4
        expedition_state = rules.start_meditation(expedition_state, NOW)
        breakthrough_state = fresh_state()
        breakthrough_state["qi"] = 150
        scenarios = (
            (
                "claim",
                claim_state,
                lambda cog, request_id: cog.claim(
                    42,
                    request_id,
                    NOW + timedelta(hours=2),
                ),
                lambda state: self.assertEqual(
                    (state["qi"], state["spirit_stones"]),
                    (10, 6),
                ),
            ),
            (
                "purchase",
                purchase_state,
                lambda cog, request_id: cog.buy(
                    42,
                    "thanh_truc_kiem",
                    request_id,
                    NOW,
                ),
                lambda state: self.assertEqual(
                    state["owned_items"],
                    ["thanh_truc_kiem"],
                ),
            ),
            (
                "expedition",
                expedition_state,
                lambda cog, request_id: cog.start_expedition(
                    42,
                    "linhduoc",
                    2,
                    request_id,
                    NOW,
                ),
                lambda state: self.assertEqual(
                    state["session"]["kind"],
                    "expedition",
                ),
            ),
            (
                "breakthrough",
                breakthrough_state,
                lambda cog, request_id: cog.breakthrough(
                    42,
                    request_id,
                    NOW,
                ),
                lambda state: self.assertEqual(state["stage_index"], 1),
            ),
        )

        for name, state, action, assertion in scenarios:
            with self.subTest(action=name):
                cog, database = make_cog(
                    [{"user_id": 42, "balance": 0, "cultivation": state}]
                )
                request_id = f"repeat-{name}"

                first = action(cog, request_id)
                repeated = action(cog, request_id)

                self.assertFalse(first.duplicate)
                self.assertTrue(repeated.duplicate)
                assertion(repeated.state)
                self.assertEqual(len(database["cultivation_events"].documents), 1)

    def test_exact_ten_minute_claim_persists_fractional_progress(self) -> None:
        state = rules.start_meditation(fresh_state(), NOW)
        cog, database = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )

        result = cog.claim(
            42,
            "ten-minute-claim",
            NOW + timedelta(minutes=10),
        )

        self.assertEqual(result.state["qi"], 0)
        self.assertEqual(result.state["spirit_stones"], 0)
        self.assertGreater(result.state["qi_remainder"], 0)
        self.assertGreater(result.state["stone_remainder"], 0)
        self.assertEqual(
            result.state["session"]["settled_at"],
            (NOW + timedelta(minutes=10)).replace(tzinfo=None),
        )
        self.assertEqual(len(database["cultivation_events"].documents), 1)

    def test_compare_and_swap_retries_without_applying_twice(self) -> None:
        state = fresh_state()
        cog, database = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        database["user_accounts"].cas_failures = 2
        calls = 0

        def grant(state: dict, balance: int) -> StateChange:
            nonlocal calls
            calls += 1
            state["spirit_stones"] += 7
            return StateChange(state, "test_cas", "changed")

        result = cog._mutate(42, "cas-event", NOW, grant)

        self.assertEqual(calls, 3)
        self.assertEqual(result.state["spirit_stones"], 7)
        self.assertEqual(result.state["version"], 2)
        self.assertEqual(len(database["cultivation_events"].documents), 1)

    def test_three_compare_and_swap_conflicts_raise_busy_without_write(self) -> None:
        state = fresh_state()
        cog, database = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        database["user_accounts"].cas_failures = 3

        def grant(state: dict, balance: int) -> StateChange:
            state["spirit_stones"] += 7
            return StateChange(state, "test_cas", "changed")

        with self.assertRaises(MutationBusy):
            cog._mutate(42, "cas-event", NOW, grant)

        persisted = database["user_accounts"].documents[0]["cultivation"]
        self.assertEqual(persisted["spirit_stones"], 0)
        self.assertEqual(persisted["version"], 1)
        self.assertEqual(database["cultivation_events"].documents, [])

    def test_exchange_updates_tc_and_stones_atomically_and_logs_both(self) -> None:
        state = fresh_state()
        cog, database = make_cog(
            [{"user_id": 42, "balance": 5, "cultivation": state}]
        )

        result = cog.exchange_buy(42, 2, "exchange-buy", NOW)
        duplicate = cog.exchange_buy(42, 2, "exchange-buy", NOW)

        self.assertEqual(result.balance, 3)
        self.assertEqual(result.state["spirit_stones"], 20)
        self.assertTrue(duplicate.duplicate)
        account = database["user_accounts"].documents[0]
        self.assertEqual(account["balance"], 3)
        self.assertEqual(account["cultivation"]["spirit_stones"], 20)
        self.assertEqual(len(database["cultivation_events"].documents), 1)
        self.assertEqual(len(database["transaction_logs"].documents), 1)
        transaction = database["transaction_logs"].documents[0]
        self.assertEqual(transaction["transaction_type"], "debit")
        self.assertEqual(transaction["amount"], 2)
        self.assertEqual(transaction["balance_after"], 3)

    def test_exchange_cannot_overspend_trap_coin(self) -> None:
        state = fresh_state()
        cog, database = make_cog(
            [{"user_id": 42, "balance": 1, "cultivation": state}]
        )

        with self.assertRaises(rules.RuleError):
            cog.exchange_buy(42, 2, "too-expensive", NOW)

        account = database["user_accounts"].documents[0]
        self.assertEqual(account["balance"], 1)
        self.assertEqual(account["cultivation"]["spirit_stones"], 0)
        self.assertEqual(database["transaction_logs"].documents, [])

    def test_duplicate_account_index_failure_disables_feature(self) -> None:
        database = FakeDatabase(
            [
                {"user_id": 42, "balance": 1},
                {"user_id": 42, "balance": 2},
            ]
        )
        database["user_accounts"].index_error = DuplicateKeyError(
            "duplicate user_id"
        )
        with self.assertLogs(
            "cogs.cultivation.cultivation",
            level="ERROR",
        ) as captured:
            cog = CultivationCog(SimpleNamespace(db=database))

        self.assertFalse(cog.enabled)
        self.assertTrue(any("duplicate ids=[42]" in line for line in captured.output))
        with self.assertRaises(CultivationUnavailable):
            cog.create_profile(42, NOW, "blocked")


class TestCultivationDiscordSurface(unittest.IsolatedAsyncioTestCase):
    def test_complete_prefix_command_surface_is_registered(self) -> None:
        expected = {
            "tutien",
            "tutien batdau",
            "tutien thucong",
            "tutien huong",
            "tutien dotpha",
            "tutien phai",
            "tutien phai reset",
            "tutien thienphu",
            "tutien thienphu tang",
            "tutien dongphu",
            "tutien dongphu nangcap",
            "tutien choden",
            "tutien mua",
            "tutien kho",
            "tutien trangbi",
            "tutien phanra",
            "tutien luyen",
            "tutien thiluyen",
            "tutien bicanh",
            "tutien bicanh start",
            "tutien bicanh claim",
            "tutien bicanh cancel",
            "tutien doido",
            "tutien doido mua",
            "tutien doido ban",
            "tutien profile",
            "tutien top",
            "tutien riengtu",
        }

        self.assertEqual(
            {command.qualified_name for command in CultivationCog.__cog_commands__},
            expected,
        )
        root = next(
            command
            for command in CultivationCog.__cog_commands__
            if command.qualified_name == "tutien"
        )
        self.assertEqual(root.aliases, ["cultivate"])

    async def test_dashboard_is_owner_only(self) -> None:
        handler = AsyncMock()
        view = CultivationView(
            SimpleNamespace(handle_dashboard_action=handler),
            author_id=42,
        )
        owner = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        stranger = SimpleNamespace(
            user=SimpleNamespace(id=99),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        self.assertTrue(await view.interaction_check(owner))
        self.assertFalse(await view.interaction_check(stranger))
        stranger.response.send_message.assert_awaited_once_with(
            "Chỉ đạo hữu đã mở Tiên Lộ mới dùng được bảng này.",
            ephemeral=True,
        )

    async def test_dashboard_controls_dispatch_and_timeout_cleanly(self) -> None:
        handler = AsyncMock()
        view = CultivationView(
            SimpleNamespace(handle_dashboard_action=handler),
            author_id=42,
        )
        interaction = SimpleNamespace(user=SimpleNamespace(id=42))

        await view.claim_button.callback(interaction)
        handler.assert_awaited_once_with(interaction, view, "claim", None)
        self.assertEqual(view.timeout, DASHBOARD_TIMEOUT_SECONDS)
        self.assertEqual(len(view.children), 5)

        message = SimpleNamespace(edit=AsyncMock())
        view.message = message
        await view.on_timeout()
        self.assertTrue(all(child.disabled for child in view.children))
        message.edit.assert_awaited_once_with(view=view)

    async def test_shared_reply_mentions_only_invoker(self) -> None:
        cog, _ = make_cog()
        message = SimpleNamespace(id=123)
        ctx = SimpleNamespace(reply=AsyncMock(return_value=message))

        returned = await cog._reply(ctx, "hello")

        self.assertIs(returned, message)
        ctx.reply.assert_awaited_once()
        kwargs = ctx.reply.await_args.kwargs
        self.assertTrue(kwargs["mention_author"])
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertFalse(kwargs["allowed_mentions"].roles)

    async def test_dashboard_open_replies_with_bound_view(self) -> None:
        state = rules.start_meditation(fresh_state(), NOW)
        cog, _ = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        message = SimpleNamespace(id=123)
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=42, display_name="Đạo Hữu"),
            reply=AsyncMock(return_value=message),
        )

        await cog._open_dashboard(ctx)

        kwargs = ctx.reply.await_args.kwargs
        self.assertIsInstance(kwargs["embed"], discord.Embed)
        self.assertIn("Cảnh giới", kwargs["embed"].title)
        self.assertIsInstance(kwargs["view"], CultivationView)
        self.assertEqual(kwargs["view"].panel, PANEL_REALM)
        self.assertIs(kwargs["view"].message, message)

    async def test_invalid_dashboard_action_is_ephemeral(self) -> None:
        cog, _ = make_cog()
        view = CultivationView(cog, author_id=42)
        interaction = SimpleNamespace(
            id=123,
            user=SimpleNamespace(id=42, display_name="Đạo Hữu"),
            response=SimpleNamespace(
                send_message=AsyncMock(),
                edit_message=AsyncMock(),
            ),
        )

        await cog.handle_dashboard_action(interaction, view, "removed", None)

        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        interaction.response.edit_message.assert_not_awaited()

    async def test_private_profile_is_hidden_from_other_members(self) -> None:
        state = fresh_state()
        state["profile_public"] = False
        cog, _ = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=7, display_name="Viewer"),
            reply=AsyncMock(),
        )
        target = SimpleNamespace(id=42, display_name="Private")

        await cog.cultivation_profile.callback(cog, ctx, target)

        ctx.reply.assert_awaited_once()
        self.assertIn("riêng tư", ctx.reply.await_args.args[0])
        self.assertIsNone(ctx.reply.await_args.kwargs["embed"])

    async def test_guild_top_excludes_private_and_bot_profiles(self) -> None:
        public = fresh_state()
        public.update({"stage_index": 10, "lifetime_qi": 1_000})
        private = fresh_state()
        private.update(
            {
                "stage_index": 17,
                "lifetime_qi": 999_999,
                "profile_public": False,
            }
        )
        bot_state = fresh_state()
        bot_state.update({"stage_index": 17, "lifetime_qi": 999_999})
        cog, _ = make_cog(
            [
                {"user_id": 1, "balance": 0, "cultivation": public},
                {"user_id": 2, "balance": 0, "cultivation": private},
                {"user_id": 3, "balance": 0, "cultivation": bot_state},
            ]
        )
        members = [
            SimpleNamespace(id=1, mention="<@1>", bot=False),
            SimpleNamespace(id=2, mention="<@2>", bot=False),
            SimpleNamespace(id=3, mention="<@3>", bot=True),
        ]
        ctx = SimpleNamespace(
            author=members[0],
            guild=SimpleNamespace(members=members),
            reply=AsyncMock(),
        )

        await cog.cultivation_top.callback(cog, ctx)

        embed = ctx.reply.await_args.kwargs["embed"]
        self.assertIn("<@1>", embed.description)
        self.assertNotIn("<@2>", embed.description)
        self.assertNotIn("<@3>", embed.description)

    async def test_cave_status_reports_bonus_rates_storage_and_cost(self) -> None:
        state = fresh_state()
        state.update({"cave_level": 2, "spirit_stones": 1_000})
        cog, _ = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=42),
            reply=AsyncMock(),
        )

        await cog.cultivation_cave.callback(cog, ctx)

        content = ctx.reply.await_args.args[0]
        self.assertIn("+5% sản lượng", content)
        self.assertIn("Tu Vi/h", content)
        self.assertIn("28h", content)
        self.assertIn("1,500 Linh Thạch", content)

    async def test_expedition_claim_rejects_an_ordinary_meditation(self) -> None:
        state = rules.start_meditation(fresh_state(), NOW)
        cog, database = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=42),
            message=SimpleNamespace(id=123),
            reply=AsyncMock(),
        )

        await cog.cultivation_expedition_claim.callback(cog, ctx)

        self.assertIn("không có Bí Cảnh", ctx.reply.await_args.args[0])
        persisted = database["user_accounts"].documents[0]["cultivation"]
        self.assertEqual(persisted["version"], 1)

    async def test_crafting_without_id_lists_recipe_costs(self) -> None:
        state = fresh_state()
        cog, _ = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=42),
            reply=AsyncMock(),
        )

        await cog.cultivation_craft.callback(cog, ctx, None)

        embed = ctx.reply.await_args.kwargs["embed"]
        self.assertEqual(len(embed.fields), len(rules.RECIPES))
        self.assertIn("ID:", embed.fields[0].value)

    async def test_top_reports_unavailable_cog_cleanly(self) -> None:
        cog, _ = make_cog()
        cog.enabled = False
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=42),
            guild=SimpleNamespace(members=[]),
            reply=AsyncMock(),
        )

        await cog.cultivation_top.callback(cog, ctx)

        self.assertIn("tạm tắt", ctx.reply.await_args.args[0])

    async def test_view_commands_open_matching_dashboard_panels(self) -> None:
        state = rules.start_meditation(fresh_state(), NOW)
        cog, _ = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=42, display_name="Đạo Hữu"),
            reply=AsyncMock(return_value=SimpleNamespace(id=1)),
        )
        expected = (
            (cog.cultivation_path, PANEL_PATH, "Phái"),
            (cog.cultivation_talents, PANEL_PATH, "Phái"),
            (cog.cultivation_market, PANEL_MARKET, "Chợ"),
            (cog.cultivation_inventory, PANEL_INVENTORY, "Kho"),
            (cog.cultivation_expedition, PANEL_PVE, "Tháp"),
        )
        for command, panel, title_part in expected:
            ctx.reply.reset_mock()
            with self.subTest(panel=panel):
                if command is cog.cultivation_path:
                    await command.callback(cog, ctx, None)
                else:
                    await command.callback(cog, ctx)
                view = ctx.reply.await_args.kwargs["view"]
                embed = ctx.reply.await_args.kwargs["embed"]
                self.assertEqual(view.panel, panel)
                self.assertIn(title_part, embed.title)

    async def test_dashboard_navigation_does_not_mutate_state(self) -> None:
        state = rules.start_meditation(fresh_state(), NOW)
        cog, database = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        view = CultivationView(cog, 42, state=state)
        interaction = fake_interaction()

        with patch("cogs.cultivation.cultivation._utcnow", return_value=NOW):
            await cog.handle_dashboard_action(
                interaction, view, "navigate", PANEL_MARKET
            )

        self.assertEqual(view.panel, PANEL_MARKET)
        self.assertIn("Mua", child_labels(view))
        interaction.response.edit_message.assert_awaited_once()
        persisted = database["user_accounts"].documents[0]["cultivation"]
        self.assertEqual(persisted["version"], 1)
        self.assertEqual(database["cultivation_events"].documents, [])

    async def test_dashboard_select_enables_market_purchase(self) -> None:
        state = rules.start_meditation(fresh_state(), NOW)
        state["spirit_stones"] = 1_000
        item = rules.daily_market(NOW)[0]
        cog, database = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        view = CultivationView(cog, 42, panel=PANEL_MARKET, state=state)
        interaction = fake_interaction()

        with patch("cogs.cultivation.cultivation._utcnow", return_value=NOW):
            await cog.handle_dashboard_action(
                interaction, view, "select", f"market:{item.key}"
            )
            buy = next(
                child
                for child in view.children
                if getattr(child, "label", None) == "Mua"
            )
            self.assertFalse(buy.disabled)
            await buy.callback(interaction)

        owned = database["user_accounts"].documents[0]["cultivation"]["owned_items"]
        self.assertIn(item.key, owned)

    async def test_path_and_talent_can_be_chosen_from_dashboard(self) -> None:
        state = rules.start_meditation(fresh_state(), NOW)
        state["stage_index"] = 1
        state["talent_points"] = 2
        cog, database = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        view = CultivationView(cog, 42, panel=PANEL_PATH, state=state)
        interaction = fake_interaction()

        with patch("cogs.cultivation.cultivation._utcnow", return_value=NOW):
            await cog.handle_dashboard_action(interaction, view, "path", "kiem")
            await cog.handle_dashboard_action(
                interaction, view, "select", "talent:kiem_y"
            )
            allocate = next(
                child
                for child in view.children
                if getattr(child, "label", None) == "Cộng 1 điểm"
            )
            await allocate.callback(interaction)

        persisted = database["user_accounts"].documents[0]["cultivation"]
        self.assertEqual(persisted["path"], "kiem")
        self.assertEqual(persisted["talents"]["kiem_y"], 1)
        self.assertEqual(persisted["talent_points"], 1)

    async def test_salvage_and_reset_require_confirmation(self) -> None:
        state = rules.start_meditation(fresh_state(), NOW)
        state.update(
            {
                "stage_index": 1,
                "path": "kiem",
                "spirit_stones": 5_000,
                "owned_items": ["thanh_truc_kiem"],
            }
        )
        cog, database = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        view = CultivationView(cog, 42, panel=PANEL_INVENTORY, state=state)
        view.selected_item = "thanh_truc_kiem"
        view.rebuild(state)
        interaction = fake_interaction()

        with patch("cogs.cultivation.cultivation._utcnow", return_value=NOW):
            await view._run(interaction, "salvage", "thanh_truc_kiem")
            owned = database["user_accounts"].documents[0]["cultivation"]["owned_items"]
            self.assertEqual(owned, ["thanh_truc_kiem"])
            self.assertEqual(view.confirming, ("salvage", "thanh_truc_kiem"))
            self.assertIn("Xác nhận phân rã", child_labels(view))

            await view._run(interaction, "salvage", "thanh_truc_kiem")

        owned = database["user_accounts"].documents[0]["cultivation"]["owned_items"]
        self.assertEqual(owned, [])

        path_view = CultivationView(
            cog,
            42,
            panel=PANEL_PATH,
            state=database["user_accounts"].documents[0]["cultivation"],
        )
        with patch("cogs.cultivation.cultivation._utcnow", return_value=NOW):
            await path_view._run(interaction, "reset_path")
            self.assertEqual(
                database["user_accounts"].documents[0]["cultivation"]["path"],
                "kiem",
            )
            await path_view._run(interaction, "reset_path")
        self.assertIsNone(
            database["user_accounts"].documents[0]["cultivation"]["path"]
        )

    async def test_pve_panel_can_clear_tower_and_start_expedition(self) -> None:
        state = rules.start_meditation(fresh_state(), NOW)
        state["stage_index"] = 4
        cog, database = make_cog(
            [{"user_id": 42, "balance": 0, "cultivation": state}]
        )
        view = CultivationView(cog, 42, panel=PANEL_PVE, state=state)
        interaction = fake_interaction()

        with patch("cogs.cultivation.cultivation._utcnow", return_value=NOW):
            await cog.handle_dashboard_action(interaction, view, "trial", None)
            self.assertEqual(
                database["user_accounts"].documents[0]["cultivation"]["tower_floor"],
                1,
            )
            await cog.handle_dashboard_action(
                interaction, view, "select", "zone:linhduoc"
            )
            await cog.handle_dashboard_action(
                interaction, view, "select", "hours:4"
            )
            await cog.handle_dashboard_action(
                interaction, view, "expedition_start", "linhduoc:4"
            )

        session = database["user_accounts"].documents[0]["cultivation"]["session"]
        self.assertEqual(session["kind"], "expedition")
        self.assertEqual(session["zone"], "linhduoc")
        self.assertEqual(session["hours"], 4)


class TestCultivationEmbeds(unittest.TestCase):
    @staticmethod
    def assert_embed_within_limits(
        testcase: unittest.TestCase,
        embed: discord.Embed,
    ) -> None:
        testcase.assertLessEqual(len(embed), 6_000)
        testcase.assertLessEqual(len(embed.title or ""), 256)
        testcase.assertLessEqual(len(embed.description or ""), 4_096)
        testcase.assertLessEqual(len(embed.fields), 25)
        for field in embed.fields:
            testcase.assertLessEqual(len(field.name), 256)
            testcase.assertLessEqual(len(field.value), 1_024)
        testcase.assertLessEqual(len(embed.footer.text or ""), 2_048)

    def test_every_cultivation_embed_stays_within_discord_limits(self) -> None:
        cog, _ = make_cog()
        member = SimpleNamespace(display_name="Đạo Hữu")
        state = fresh_state()
        state.update(
            {
                "stage_index": len(rules.STAGES) - 1,
                "qi": 10**12,
                "spirit_stones": 10**12,
                "path": "kiem",
                "talent_points": 99,
                "talents": {key: 5 for key, value in rules.TALENTS.items() if value.path == "kiem"},
                "owned_items": list(rules.ITEMS),
                "materials": {key: 10**12 for key in rules.MATERIAL_NAMES},
                "tower_floor": 30,
            }
        )
        for slot in rules.GEAR_SLOT_NAMES:
            state["equipped"][slot] = next(
                item.key for item in rules.ITEMS.values() if item.slot == slot
            )
        state = rules.start_meditation(state, NOW)
        embeds = (
            cog.profile_embed(member, state, NOW + timedelta(hours=48), owner_view=True),
            cog.realm_embed(member, state, NOW + timedelta(hours=48)),
            cog.market_embed(state, NOW),
            cog.inventory_embed(member, state, include_recipes=True),
            cog.crafting_embed(state),
            cog.talent_embed(member, state),
            cog.expedition_embed(state, NOW),
            cog.pve_embed(state, NOW),
            cog.exchange_embed(state, 10**12, NOW),
            cog.dashboard_embed(member, state, NOW, PANEL_PATH),
        )

        for embed in embeds:
            with self.subTest(title=embed.title):
                self.assert_embed_within_limits(self, embed)

    def test_market_and_expedition_embeds_expose_rotation_and_pity(self) -> None:
        cog, _ = make_cog()
        state = fresh_state()
        state["boss_pity"] = 9

        market = cog.market_embed(state, NOW)
        expedition = cog.expedition_embed(state, NOW)

        self.assertIn("Bốn món cơ bản", market.description)
        self.assertEqual(sum(field.name.startswith("📌") for field in market.fields), 4)
        self.assertEqual(sum(field.name.startswith("🔄") for field in market.fields), 4)
        self.assertIn("10/10", expedition.footer.text)


if __name__ == "__main__":
    unittest.main()
