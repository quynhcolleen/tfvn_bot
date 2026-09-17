"""Pure rules and calculations for the Tiên Lộ cultivation game."""

from __future__ import annotations

import hashlib
import random
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Mapping


SCHEMA_VERSION = 1
SECONDS_PER_HOUR = 3600
MIN_SETTLEMENT_SECONDS = 10 * 60
BASE_STORAGE_HOURS = 24
MAX_STORAGE_HOURS = 48
MAX_CAVE_LEVEL = 7
MAX_PROCESSED_REQUESTS = 32
# Vietnam has used UTC+7 without daylight-saving transitions for this game's
# supported dates. A fixed offset also works on Windows hosts without tzdata.
ICT = timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh")

FOCUS_BALANCED = "canbang"
FOCUS_QI = "tinhtu"
FOCUS_STONES = "khaikhoang"
FOCUS_MODIFIERS: dict[str, tuple[int, int]] = {
    FOCUS_BALANCED: (10_000, 10_000),
    FOCUS_QI: (12_500, 6_000),
    FOCUS_STONES: (7_500, 15_000),
}
FOCUS_NAMES = {
    FOCUS_BALANCED: "Cân Bằng",
    FOCUS_QI: "Tĩnh Tu",
    FOCUS_STONES: "Khai Khoáng",
}

CAVE_UPGRADE_COSTS = (500, 1_500, 4_000, 10_000, 25_000, 60_000)

TC_TO_STONE_RATE = 10
TC_BUY_WEEKLY_CAP = 50
STONE_TO_TC_RATE = 20
TC_SELL_WEEKLY_CAP = 20

BREAKTHROUGH_BASE_CHANCE = 70
BREAKTHROUGH_PITY_STEP = 10
BREAKTHROUGH_RETRY_SECONDS = 3600
BREAKTHROUGH_FAILURE_FEE_PERCENT = 25

PATH_NAMES = {
    "kiem": "Kiếm Tu",
    "the": "Thể Tu",
    "dan": "Đan Tu",
}
PATH_DESCRIPTIONS = {
    "kiem": "Lực chiến, tốc độ Bí Cảnh và tỉ lệ đột phá đại cảnh giới.",
    "the": "Lực chiến, trữ Bế Quan và giảm phí đột phá thất bại.",
    "dan": "Lực chiến, giảm chi phí luyện và nguyên liệu Bí Cảnh.",
}
REALM_GROUP_NAMES = {
    "pham": "Phàm Nhân",
    "luyen_khi": "Luyện Khí",
    "truc_co": "Trúc Cơ",
    "kim_dan": "Kim Đan",
}

GEAR_SLOT_NAMES = {
    "weapon": "Pháp Khí",
    "robe": "Pháp Bào",
    "artifact": "Pháp Bảo",
    "ring": "Nhẫn Trữ Vật",
}

MATERIAL_NAMES = {
    "linh_thao": "Linh Thảo",
    "huyen_thiet": "Huyền Thiết",
    "yeu_dan": "Yêu Đan",
    "manh_phap_bao": "Mảnh Pháp Bảo",
}


@dataclass(frozen=True)
class Stage:
    key: str
    realm_key: str
    name: str
    qi_rate: int
    stone_rate: int
    qi_cost: int | None
    stone_cost: int
    major_next: bool
    base_power: int


STAGES: tuple[Stage, ...] = (
    Stage("pham_nhan", "pham", "Phàm Nhân", 5, 3, 100, 0, False, 60),
    Stage("luyen_khi_1", "luyen_khi", "Luyện Khí · Tầng 1", 12, 6, 288, 0, False, 175),
    Stage("luyen_khi_2", "luyen_khi", "Luyện Khí · Tầng 2", 12, 6, 288, 0, False, 290),
    Stage("luyen_khi_3", "luyen_khi", "Luyện Khí · Tầng 3", 12, 6, 576, 0, False, 405),
    Stage("luyen_khi_4", "luyen_khi", "Luyện Khí · Tầng 4", 12, 6, 576, 0, False, 520),
    Stage("luyen_khi_5", "luyen_khi", "Luyện Khí · Tầng 5", 12, 6, 864, 0, False, 635),
    Stage("luyen_khi_6", "luyen_khi", "Luyện Khí · Tầng 6", 12, 6, 864, 0, False, 750),
    Stage("luyen_khi_7", "luyen_khi", "Luyện Khí · Tầng 7", 12, 6, 1_152, 0, False, 865),
    Stage("luyen_khi_8", "luyen_khi", "Luyện Khí · Tầng 8", 12, 6, 1_152, 0, False, 980),
    Stage("luyen_khi_9", "luyen_khi", "Luyện Khí · Tầng 9", 12, 6, 1_440, 1_000, True, 1_095),
    Stage("truc_co_so", "truc_co", "Trúc Cơ · Sơ Kỳ", 35, 12, 4_200, 0, False, 1_210),
    Stage("truc_co_trung", "truc_co", "Trúc Cơ · Trung Kỳ", 35, 12, 5_880, 0, False, 1_325),
    Stage("truc_co_hau", "truc_co", "Trúc Cơ · Hậu Kỳ", 35, 12, 8_400, 0, False, 1_440),
    Stage("truc_co_vien_man", "truc_co", "Trúc Cơ · Viên Mãn", 35, 12, 11_760, 5_000, True, 1_555),
    Stage("kim_dan_so", "kim_dan", "Kim Đan · Sơ Kỳ", 90, 25, 30_240, 0, False, 1_670),
    Stage("kim_dan_trung", "kim_dan", "Kim Đan · Trung Kỳ", 90, 25, 45_360, 0, False, 1_785),
    Stage("kim_dan_hau", "kim_dan", "Kim Đan · Hậu Kỳ", 90, 25, 60_480, 0, False, 1_900),
    Stage("kim_dan_vien_man", "kim_dan", "Kim Đan · Viên Mãn", 90, 25, None, 0, False, 2_015),
)


@dataclass(frozen=True)
class Talent:
    key: str
    path: str
    name: str
    description: str
    effect: str
    per_rank: int
    max_rank: int = 5


TALENTS: dict[str, Talent] = {
    "kiem_y": Talent("kiem_y", "kiem", "Kiếm Ý", "+5% lực chiến mỗi bậc.", "power_pct", 5),
    "ngu_kiem": Talent("ngu_kiem", "kiem", "Ngự Kiếm", "Giảm 4% thời gian Bí Cảnh mỗi bậc.", "expedition_speed_pct", 4),
    "pha_canh": Talent("pha_canh", "kiem", "Phá Cảnh", "+2% tỉ lệ đột phá đại cảnh giới mỗi bậc.", "breakthrough_pp", 2),
    "the_phach": Talent("the_phach", "the", "Kim Cương Thể", "+5% lực chiến mỗi bậc.", "power_pct", 5),
    "ben_bi": Talent("ben_bi", "the", "Bền Bỉ", "+1 giờ trữ Bế Quan mỗi bậc.", "storage_hours", 1),
    "ho_mach": Talent("ho_mach", "the", "Hộ Mạch", "Giảm 3 điểm % phí thất bại mỗi bậc.", "failure_fee_pp", 3),
    "hoa_hau": Talent("hoa_hau", "dan", "Hỏa Hầu", "+5% lực chiến mỗi bậc.", "power_pct", 5),
    "luyen_dan": Talent("luyen_dan", "dan", "Luyện Đan", "Giảm 4% nguyên liệu chế tạo mỗi bậc.", "craft_discount_pct", 4),
    "tam_duoc": Talent("tam_duoc", "dan", "Tầm Dược", "+5% nguyên liệu Bí Cảnh mỗi bậc.", "material_yield_pct", 5),
}


@dataclass(frozen=True)
class Item:
    key: str
    name: str
    slot: str
    min_stage: int
    price: int
    power: int = 0
    qi_bonus_bp: int = 0
    stone_bonus_bp: int = 0
    storage_hours: int = 0
    permanent_market: bool = False
    boss_drop: bool = False
    salvage_fragments: int = 1


ITEMS: dict[str, Item] = {
    "thanh_truc_kiem": Item("thanh_truc_kiem", "Thanh Trúc Kiếm", "weapon", 0, 250, power=80, permanent_market=True),
    "thanh_van_bao": Item("thanh_van_bao", "Thanh Vân Bào", "robe", 0, 250, power=60, permanent_market=True),
    "tu_linh_chau": Item("tu_linh_chau", "Tụ Linh Châu", "artifact", 0, 300, qi_bonus_bp=500, permanent_market=True),
    "nhan_tieu_na": Item("nhan_tieu_na", "Nhẫn Tiểu Na", "ring", 0, 300, stone_bonus_bp=250, storage_hours=2, permanent_market=True),
    "huyen_thiet_kiem": Item("huyen_thiet_kiem", "Huyền Thiết Kiếm", "weapon", 4, 1_000, power=180, boss_drop=True, salvage_fragments=2),
    "kim_tam_phap_bao": Item("kim_tam_phap_bao", "Kim Tàm Pháp Bào", "robe", 4, 1_000, power=140, boss_drop=True, salvage_fragments=2),
    "bach_ngoc_lien": Item("bach_ngoc_lien", "Bạch Ngọc Liên", "artifact", 4, 1_200, qi_bonus_bp=1_000, boss_drop=True, salvage_fragments=2),
    "nhan_can_khon": Item("nhan_can_khon", "Nhẫn Càn Khôn", "ring", 4, 1_200, stone_bonus_bp=500, storage_hours=4, boss_drop=True, salvage_fragments=2),
    "thanh_phong_kiem": Item("thanh_phong_kiem", "Thanh Phong Kiếm", "weapon", 10, 4_000, power=350, boss_drop=True, salvage_fragments=3),
    "hoang_kim_bao_y": Item("hoang_kim_bao_y", "Hoàng Kim Bảo Y", "robe", 10, 4_000, power=300, boss_drop=True, salvage_fragments=3),
    "that_tinh_chau": Item("that_tinh_chau", "Thất Tinh Châu", "artifact", 10, 4_500, power=50, qi_bonus_bp=1_500, boss_drop=True, salvage_fragments=3),
    "nhan_huyen_khong": Item("nhan_huyen_khong", "Nhẫn Huyền Không", "ring", 10, 4_500, stone_bonus_bp=1_000, storage_hours=6, boss_drop=True, salvage_fragments=3),
    "xich_tieu_kiem": Item("xich_tieu_kiem", "Xích Tiêu Kiếm", "weapon", 14, 10_000, power=600, boss_drop=True, salvage_fragments=5),
    "cuc_quang_bao": Item("cuc_quang_bao", "Cực Quang Bào", "robe", 14, 10_000, power=500, boss_drop=True, salvage_fragments=5),
    "hon_nguyen_chau": Item("hon_nguyen_chau", "Hỗn Nguyên Châu", "artifact", 14, 11_000, power=100, qi_bonus_bp=2_000, boss_drop=True, salvage_fragments=5),
    "nhan_tinh_ha": Item("nhan_tinh_ha", "Nhẫn Tinh Hà", "ring", 14, 11_000, stone_bonus_bp=1_500, storage_hours=8, boss_drop=True, salvage_fragments=5),
}


@dataclass(frozen=True)
class Recipe:
    key: str
    result_item: str
    stone_cost: int
    materials: Mapping[str, int]


RECIPES: dict[str, Recipe] = {
    "huyen_thiet_kiem": Recipe("huyen_thiet_kiem", "huyen_thiet_kiem", 500, {"huyen_thiet": 12, "manh_phap_bao": 2}),
    "kim_tam_phap_bao": Recipe("kim_tam_phap_bao", "kim_tam_phap_bao", 500, {"linh_thao": 8, "huyen_thiet": 8}),
    "bach_ngoc_lien": Recipe("bach_ngoc_lien", "bach_ngoc_lien", 600, {"linh_thao": 12, "yeu_dan": 2}),
    "nhan_can_khon": Recipe("nhan_can_khon", "nhan_can_khon", 600, {"huyen_thiet": 10, "manh_phap_bao": 2}),
    "thanh_phong_kiem": Recipe("thanh_phong_kiem", "thanh_phong_kiem", 2_000, {"huyen_thiet": 30, "yeu_dan": 8, "manh_phap_bao": 8}),
    "hoang_kim_bao_y": Recipe("hoang_kim_bao_y", "hoang_kim_bao_y", 2_000, {"linh_thao": 25, "huyen_thiet": 20, "manh_phap_bao": 8}),
    "that_tinh_chau": Recipe("that_tinh_chau", "that_tinh_chau", 2_250, {"linh_thao": 30, "yeu_dan": 10, "manh_phap_bao": 8}),
    "nhan_huyen_khong": Recipe("nhan_huyen_khong", "nhan_huyen_khong", 2_250, {"huyen_thiet": 25, "yeu_dan": 8, "manh_phap_bao": 10}),
}


@dataclass(frozen=True)
class ExpeditionZone:
    key: str
    name: str
    min_stage: int
    base_stones_per_two_hours: int
    materials_per_two_hours: Mapping[str, int]
    gear_drop_percent: int


EXPEDITION_ZONES: dict[str, ExpeditionZone] = {
    "linhduoc": ExpeditionZone("linhduoc", "Linh Dược Cốc", 1, 20, {"linh_thao": 5}, 0),
    "cokhoang": ExpeditionZone("cokhoang", "Cổ Khoáng", 1, 50, {"huyen_thiet": 3}, 0),
    "yeuthuson": ExpeditionZone("yeuthuson", "Yêu Thú Sơn", 4, 30, {"yeu_dan": 2, "manh_phap_bao": 1}, 5),
}
EXPEDITION_HOURS = (2, 4, 8)


@dataclass(frozen=True)
class TowerFloor:
    floor: int
    required_power: int
    stone_reward: int
    material: str | None
    material_amount: int
    boss: bool


TOWER_FLOORS: tuple[TowerFloor, ...] = tuple(
    TowerFloor(
        floor=floor,
        required_power=80 + floor * 85 + (floor // 5) * 100,
        stone_reward=40 + floor * 15,
        material="manh_phap_bao" if floor % 5 == 0 else None,
        material_amount=max(1, floor // 5) if floor % 5 == 0 else 0,
        boss=floor % 5 == 0,
    )
    for floor in range(1, 31)
)


class RuleError(ValueError):
    """Safe validation failure raised by pure gameplay helpers."""


@dataclass(frozen=True)
class Settlement:
    state: dict
    qi_gained: int = 0
    stones_gained: int = 0
    materials_gained: Mapping[str, int] | None = None
    expedition_completed: bool = False
    dropped_item: str | None = None
    duplicate_fragments: int = 0
    settled_seconds: int = 0
    capped_seconds: int = 0


@dataclass(frozen=True)
class BreakthroughResult:
    state: dict
    success: bool
    chance: int
    roll: int
    fee_paid: int
    new_stage: Stage | None


def naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _safe_int(value: object, default: int = 0, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, parsed)


def _ceil_div(numerator: int, denominator: int) -> int:
    """Return an integer ceiling without floating-point arithmetic."""
    return -(-int(numerator) // int(denominator))


def default_state(now: datetime) -> dict:
    now = naive_utc(now)
    return {
        "schema_version": SCHEMA_VERSION,
        "stage_index": 0,
        "qi": 0,
        "lifetime_qi": 0,
        "spirit_stones": 0,
        "cave_level": 1,
        "focus": FOCUS_BALANCED,
        "session": None,
        "qi_remainder": 0,
        "stone_remainder": 0,
        "path": None,
        "talent_points": 0,
        "talents": {},
        "path_reset_at": None,
        "breakthrough_failures": 0,
        "breakthrough_retry_at": None,
        "owned_items": [],
        "equipped": {slot: None for slot in GEAR_SLOT_NAMES},
        "materials": {key: 0 for key in MATERIAL_NAMES},
        "boss_pity": 0,
        "tower_floor": 0,
        "exchange_week": week_key_ict(now),
        "tc_spent_week": 0,
        "tc_earned_week": 0,
        "profile_public": True,
        "processed_requests": [],
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }


def normalize_state(raw: object, now: datetime) -> dict:
    """Return a bounded, forward-compatible copy of persisted state."""
    now = naive_utc(now)
    state = default_state(now)
    if not isinstance(raw, Mapping):
        return state
    source = dict(raw)
    stage_index = min(_safe_int(source.get("stage_index")), len(STAGES) - 1)
    cave_level = min(max(1, _safe_int(source.get("cave_level"), 1)), MAX_CAVE_LEVEL)
    state.update(
        {
            "stage_index": stage_index,
            "qi": _safe_int(source.get("qi")),
            "lifetime_qi": _safe_int(source.get("lifetime_qi")),
            "spirit_stones": _safe_int(source.get("spirit_stones")),
            "cave_level": cave_level,
            "focus": source.get("focus") if source.get("focus") in FOCUS_MODIFIERS else FOCUS_BALANCED,
            "qi_remainder": _safe_int(source.get("qi_remainder"))
            % (SECONDS_PER_HOUR * 100_000_000),
            "stone_remainder": _safe_int(source.get("stone_remainder"))
            % (SECONDS_PER_HOUR * 100_000_000),
            "path": source.get("path") if source.get("path") in PATH_NAMES else None,
            "talent_points": _safe_int(source.get("talent_points")),
            "breakthrough_failures": min(_safe_int(source.get("breakthrough_failures")), 3),
            "boss_pity": min(_safe_int(source.get("boss_pity")), 9),
            "tower_floor": min(_safe_int(source.get("tower_floor")), 30),
            "tc_spent_week": _safe_int(source.get("tc_spent_week")),
            "tc_earned_week": _safe_int(source.get("tc_earned_week")),
            "profile_public": bool(source.get("profile_public", True)),
            "version": max(1, _safe_int(source.get("version"), 1)),
            "schema_version": SCHEMA_VERSION,
        }
    )
    for key in ("created_at", "updated_at", "path_reset_at", "breakthrough_retry_at"):
        value = source.get(key)
        if isinstance(value, datetime):
            state[key] = naive_utc(value)
    talents = source.get("talents")
    if isinstance(talents, Mapping):
        state["talents"] = {
            key: min(_safe_int(value), TALENTS[key].max_rank)
            for key, value in talents.items()
            if key in TALENTS and TALENTS[key].path == state["path"] and _safe_int(value) > 0
        }
    owned = source.get("owned_items")
    if isinstance(owned, (list, tuple, set)):
        state["owned_items"] = list(dict.fromkeys(str(item) for item in owned if str(item) in ITEMS))
    equipped = source.get("equipped")
    if isinstance(equipped, Mapping):
        for slot in GEAR_SLOT_NAMES:
            item_key = equipped.get(slot)
            if item_key in state["owned_items"] and ITEMS[str(item_key)].slot == slot:
                state["equipped"][slot] = str(item_key)
    materials = source.get("materials")
    if isinstance(materials, Mapping):
        state["materials"] = {key: _safe_int(materials.get(key)) for key in MATERIAL_NAMES}
    processed = source.get("processed_requests")
    if isinstance(processed, (list, tuple)):
        state["processed_requests"] = [str(value)[:120] for value in processed[-MAX_PROCESSED_REQUESTS:]]
    session = source.get("session")
    if isinstance(session, Mapping) and session.get("kind") in {"meditation", "expedition"}:
        started_at = session.get("started_at")
        settled_at = session.get("settled_at", started_at)
        if isinstance(started_at, datetime) and isinstance(settled_at, datetime):
            normalized_session = {
                "kind": session["kind"],
                "started_at": naive_utc(started_at),
                "settled_at": naive_utc(settled_at),
                "focus": session.get("focus") if session.get("focus") in FOCUS_MODIFIERS else state["focus"],
            }
            if session["kind"] == "expedition":
                zone = session.get("zone")
                hours = _safe_int(session.get("hours"))
                ends_at = session.get("ends_at")
                if zone in EXPEDITION_ZONES and hours in EXPEDITION_HOURS and isinstance(ends_at, datetime):
                    normalized_session.update({"zone": zone, "hours": hours, "ends_at": naive_utc(ends_at)})
                else:
                    normalized_session = None
            state["session"] = normalized_session
    week = source.get("exchange_week")
    current_week = week_key_ict(now)
    if week == current_week:
        state["exchange_week"] = current_week
    else:
        state["exchange_week"] = current_week
        state["tc_spent_week"] = 0
        state["tc_earned_week"] = 0
    state["updated_at"] = now
    return state


def stage_for(state: Mapping[str, object]) -> Stage:
    return STAGES[min(_safe_int(state.get("stage_index")), len(STAGES) - 1)]


def next_stage_for(state: Mapping[str, object]) -> Stage | None:
    index = min(_safe_int(state.get("stage_index")), len(STAGES) - 1)
    return STAGES[index + 1] if index + 1 < len(STAGES) else None


def talent_effects(state: Mapping[str, object]) -> dict[str, int]:
    effects: dict[str, int] = {}
    talents = state.get("talents")
    if not isinstance(talents, Mapping):
        return effects
    for key, raw_rank in talents.items():
        if key not in TALENTS:
            continue
        rank = min(_safe_int(raw_rank), TALENTS[key].max_rank)
        definition = TALENTS[key]
        effects[definition.effect] = effects.get(definition.effect, 0) + definition.per_rank * rank
    return effects


def equipped_items(state: Mapping[str, object]) -> tuple[Item, ...]:
    equipped = state.get("equipped")
    if not isinstance(equipped, Mapping):
        return ()
    return tuple(ITEMS[str(value)] for value in equipped.values() if value in ITEMS)


def storage_cap_hours(state: Mapping[str, object]) -> int:
    cave = min(max(1, _safe_int(state.get("cave_level"), 1)), MAX_CAVE_LEVEL)
    talent_hours = talent_effects(state).get("storage_hours", 0)
    gear_hours = sum(item.storage_hours for item in equipped_items(state))
    return min(MAX_STORAGE_HOURS, BASE_STORAGE_HOURS + 4 * (cave - 1) + talent_hours + gear_hours)


def _production_bonus_bp(state: Mapping[str, object]) -> tuple[int, int]:
    cave = min(max(1, _safe_int(state.get("cave_level"), 1)), MAX_CAVE_LEVEL)
    cave_bp = 10_000 + 500 * (cave - 1)
    qi_gear_bp = sum(item.qi_bonus_bp for item in equipped_items(state))
    stone_gear_bp = sum(item.stone_bonus_bp for item in equipped_items(state))
    qi_total_bp = min(17_500, cave_bp + qi_gear_bp)
    stone_total_bp = min(17_500, cave_bp + stone_gear_bp)
    return qi_total_bp, stone_total_bp


def production_rates(state: Mapping[str, object]) -> tuple[int, int]:
    """Display whole-number effective rates; settlement keeps exact fractions."""
    stage = stage_for(state)
    qi_bp, stone_bp = _production_bonus_bp(state)
    focus = state.get("focus") if state.get("focus") in FOCUS_MODIFIERS else FOCUS_BALANCED
    qi_focus, stone_focus = FOCUS_MODIFIERS[str(focus)]
    divisor = 100_000_000
    return (
        stage.qi_rate * qi_bp * qi_focus // divisor,
        stage.stone_rate * stone_bp * stone_focus // divisor,
    )


def calculate_afk_rewards(state: Mapping[str, object], elapsed_seconds: int) -> tuple[int, int, int, int]:
    """Return qi, stones, qi remainder, stone remainder for bounded elapsed time."""
    elapsed = max(0, min(int(elapsed_seconds), storage_cap_hours(state) * SECONDS_PER_HOUR))
    stage = stage_for(state)
    qi_bp, stone_bp = _production_bonus_bp(state)
    focus = state.get("focus") if state.get("focus") in FOCUS_MODIFIERS else FOCUS_BALANCED
    qi_mod, stone_mod = FOCUS_MODIFIERS[str(focus)]
    qi_numerator = (
        elapsed * stage.qi_rate * qi_bp * qi_mod
        + _safe_int(state.get("qi_remainder"))
    )
    stone_numerator = (
        elapsed * stage.stone_rate * stone_bp * stone_mod
        + _safe_int(state.get("stone_remainder"))
    )
    divisor = SECONDS_PER_HOUR * 100_000_000
    qi, qi_remainder = divmod(qi_numerator, divisor)
    stones, stone_remainder = divmod(stone_numerator, divisor)
    return qi, stones, qi_remainder, stone_remainder


def start_meditation(state: Mapping[str, object], now: datetime, focus: str | None = None) -> dict:
    result = normalize_state(state, now)
    if focus is not None and focus not in FOCUS_MODIFIERS:
        raise RuleError("Hướng tu không hợp lệ.")
    chosen = focus or str(result["focus"])
    result["focus"] = chosen
    result["session"] = {"kind": "meditation", "started_at": naive_utc(now), "settled_at": naive_utc(now), "focus": chosen}
    return result


def _eligible_boss_items(state: Mapping[str, object]) -> list[str]:
    stage_index = _safe_int(state.get("stage_index"))
    return [item.key for item in ITEMS.values() if item.boss_drop and item.min_stage <= stage_index]


def _roll_expedition_drop(state: dict, percent: int, rng: Callable[[int], int]) -> tuple[str | None, int]:
    if percent <= 0:
        return None, 0
    pity = min(_safe_int(state.get("boss_pity")), 9)
    success = pity >= 9 or rng(100) < percent
    if not success:
        state["boss_pity"] = pity + 1
        return None, 0
    state["boss_pity"] = 0
    eligible = _eligible_boss_items(state)
    if not eligible:
        return None, 0
    item_key = eligible[rng(len(eligible))]
    if item_key in state["owned_items"]:
        fragments = ITEMS[item_key].salvage_fragments
        state["materials"]["manh_phap_bao"] += fragments
        return None, fragments
    state["owned_items"].append(item_key)
    return item_key, 0


def settle_state(
    raw_state: Mapping[str, object],
    now: datetime,
    *,
    rng: Callable[[int], int] | None = None,
    force: bool = False,
) -> Settlement:
    """Lazily settle meditation or a completed expedition."""
    now = naive_utc(now)
    state = normalize_state(raw_state, now)
    session = state.get("session")
    if not isinstance(session, Mapping):
        return Settlement(state)
    rng = rng or random.randrange
    if session.get("kind") == "expedition":
        ends_at = session.get("ends_at")
        if not isinstance(ends_at, datetime) or now < ends_at:
            return Settlement(state)
        zone = EXPEDITION_ZONES[str(session["zone"])]
        hours = _safe_int(session.get("hours"))
        units = hours // 2
        effects = talent_effects(state)
        material_bp = min(12_500, 10_000 + effects.get("material_yield_pct", 0) * 100)
        gained_materials = {
            key: amount * units * material_bp // 10_000
            for key, amount in zone.materials_per_two_hours.items()
        }
        for key, amount in gained_materials.items():
            state["materials"][key] += amount
        stones = zone.base_stones_per_two_hours * units
        state["spirit_stones"] += stones
        drop_percent = min(20, zone.gear_drop_percent * units)
        dropped, fragments = _roll_expedition_drop(state, drop_percent, rng)
        focus = str(session.get("focus", state["focus"]))
        state = start_meditation(state, ends_at, focus)
        # Continue accruing from expedition completion to claim time.
        follow_up = settle_state(state, now, rng=rng)
        follow_state = follow_up.state
        return Settlement(
            follow_state,
            qi_gained=follow_up.qi_gained,
            stones_gained=stones + follow_up.stones_gained,
            materials_gained=gained_materials,
            expedition_completed=True,
            dropped_item=dropped,
            duplicate_fragments=fragments,
            settled_seconds=follow_up.settled_seconds,
            capped_seconds=follow_up.capped_seconds,
        )
    settled_at = session.get("settled_at")
    if not isinstance(settled_at, datetime) or now <= settled_at:
        return Settlement(state)
    raw_elapsed = int((now - settled_at).total_seconds())
    if raw_elapsed < MIN_SETTLEMENT_SECONDS and not force:
        return Settlement(state)
    capped = min(raw_elapsed, storage_cap_hours(state) * SECONDS_PER_HOUR)
    qi, stones, qi_rem, stone_rem = calculate_afk_rewards(state, capped)
    state["qi"] += qi
    state["lifetime_qi"] += qi
    state["spirit_stones"] += stones
    state["qi_remainder"] = qi_rem
    state["stone_remainder"] = stone_rem
    state["session"]["settled_at"] = now
    state["updated_at"] = now
    return Settlement(state, qi, stones, settled_seconds=capped, capped_seconds=max(0, raw_elapsed - capped))


def change_focus(raw_state: Mapping[str, object], focus: str, now: datetime) -> Settlement:
    if focus not in FOCUS_MODIFIERS:
        raise RuleError("Hướng tu phải là canbang, tinhtu hoặc khaikhoang.")
    settled = settle_state(raw_state, now, force=True)
    state = settled.state
    if isinstance(state.get("session"), Mapping) and state["session"].get("kind") == "expedition":
        raise RuleError("Không thể đổi hướng tu khi đang ở Bí Cảnh.")
    state = start_meditation(state, now, focus)
    return Settlement(state, settled.qi_gained, settled.stones_gained, settled_seconds=settled.settled_seconds, capped_seconds=settled.capped_seconds)


def cave_upgrade_cost(state: Mapping[str, object]) -> int | None:
    level = min(max(1, _safe_int(state.get("cave_level"), 1)), MAX_CAVE_LEVEL)
    return CAVE_UPGRADE_COSTS[level - 1] if level < MAX_CAVE_LEVEL else None


def upgrade_cave(raw_state: Mapping[str, object], now: datetime) -> Settlement:
    settled = settle_state(raw_state, now, force=True)
    state = settled.state
    cost = cave_upgrade_cost(state)
    if cost is None:
        raise RuleError("Động Phủ đã đạt cấp tối đa.")
    if state["spirit_stones"] < cost:
        raise RuleError(f"Cần {cost:,} Linh Thạch để nâng cấp.")
    state["spirit_stones"] -= cost
    state["cave_level"] += 1
    return Settlement(state, settled.qi_gained, settled.stones_gained, settled_seconds=settled.settled_seconds, capped_seconds=settled.capped_seconds)


def select_path(raw_state: Mapping[str, object], path: str) -> dict:
    if path not in PATH_NAMES:
        raise RuleError("Phái phải là kiem, the hoặc dan.")
    state = deepcopy(dict(raw_state))
    if _safe_int(state.get("stage_index")) < 1:
        raise RuleError("Cần đạt Luyện Khí · Tầng 1 trước khi chọn phái.")
    if state.get("path") and state.get("path") != path:
        raise RuleError("Dùng `tutien phai reset` trước khi đổi phái.")
    state["path"] = path
    return state


def spend_talent(raw_state: Mapping[str, object], talent_key: str, points: int = 1) -> dict:
    state = deepcopy(dict(raw_state))
    path = state.get("path")
    definition = TALENTS.get(talent_key)
    if definition is None or definition.path != path:
        raise RuleError("Thiên phú không thuộc phái hiện tại.")
    points = int(points)
    if points < 1:
        raise RuleError("Số điểm phải lớn hơn 0.")
    current = _safe_int(state.get("talents", {}).get(talent_key))
    if current + points > definition.max_rank:
        raise RuleError(f"{definition.name} chỉ có tối đa {definition.max_rank} bậc.")
    if _safe_int(state.get("talent_points")) < points:
        raise RuleError("Không đủ điểm Thiên Phú.")
    state["talent_points"] -= points
    state.setdefault("talents", {})[talent_key] = current + points
    return state


def path_reset_cost(state: Mapping[str, object]) -> int:
    realm_index = {"pham": 1, "luyen_khi": 1, "truc_co": 2, "kim_dan": 3}[stage_for(state).realm_key]
    return 1_000 * realm_index


def reset_path(raw_state: Mapping[str, object], now: datetime) -> dict:
    now = naive_utc(now)
    state = normalize_state(raw_state, now)
    if state.get("path") not in PATH_NAMES:
        raise RuleError("Bạn chưa chọn phái để tẩy tủy.")
    reset_at = state.get("path_reset_at")
    if isinstance(reset_at, datetime) and now < reset_at:
        raise RuleError(f"Có thể tẩy tủy lại <t:{int(reset_at.replace(tzinfo=timezone.utc).timestamp())}:R>.")
    cost = path_reset_cost(state)
    if state["spirit_stones"] < cost:
        raise RuleError(f"Cần {cost:,} Linh Thạch để tẩy tủy.")
    spent = sum(_safe_int(value) for value in state["talents"].values())
    state["spirit_stones"] -= cost
    state["talent_points"] += spent
    state["talents"] = {}
    state["path"] = None
    state["path_reset_at"] = naive_utc(now) + timedelta(days=7)
    return state


def breakthrough_chance(state: Mapping[str, object]) -> int:
    stage = stage_for(state)
    if not stage.major_next:
        return 100
    bonus = talent_effects(state).get("breakthrough_pp", 0)
    pity = min(_safe_int(state.get("breakthrough_failures")), 3)
    return min(100, BREAKTHROUGH_BASE_CHANCE + BREAKTHROUGH_PITY_STEP * pity + bonus)


def failure_fee(state: Mapping[str, object]) -> int:
    stage = stage_for(state)
    reduction = min(15, talent_effects(state).get("failure_fee_pp", 0))
    percent = max(10, BREAKTHROUGH_FAILURE_FEE_PERCENT - reduction)
    return _ceil_div(stage.stone_cost * percent, 100)


def required_trial_floor(state: Mapping[str, object]) -> int:
    stage = stage_for(state)
    if stage.key == "luyen_khi_9":
        return 10
    if stage.key == "truc_co_vien_man":
        return 20
    return 0


def attempt_breakthrough(
    raw_state: Mapping[str, object],
    now: datetime,
    *,
    roll: int,
) -> BreakthroughResult:
    now = naive_utc(now)
    state = normalize_state(raw_state, now)
    stage = stage_for(state)
    following = next_stage_for(state)
    if following is None or stage.qi_cost is None:
        raise RuleError("Đã đạt cảnh giới cao nhất của phiên bản này.")
    retry_at = state.get("breakthrough_retry_at")
    if isinstance(retry_at, datetime) and now < retry_at:
        raise RuleError(f"Kinh mạch chưa ổn định; thử lại <t:{int(retry_at.replace(tzinfo=timezone.utc).timestamp())}:R>.")
    if state["qi"] < stage.qi_cost:
        raise RuleError(f"Cần {stage.qi_cost:,} Tu Vi để đột phá.")
    if stage.major_next and state["tower_floor"] < required_trial_floor(state):
        raise RuleError(f"Cần vượt tầng {required_trial_floor(state)} Tháp Thí Luyện trước.")
    if stage.major_next and state["spirit_stones"] < stage.stone_cost:
        raise RuleError(f"Cần {stage.stone_cost:,} Linh Thạch để đột phá.")
    chance = breakthrough_chance(state)
    normalized_roll = min(99, max(0, int(roll)))
    success = normalized_roll < chance
    if not success:
        fee = failure_fee(state)
        if state["spirit_stones"] < fee:
            raise RuleError(f"Cần {fee:,} Linh Thạch cho lần thử này.")
        state["spirit_stones"] -= fee
        state["breakthrough_failures"] = min(3, state["breakthrough_failures"] + 1)
        state["breakthrough_retry_at"] = naive_utc(now) + timedelta(seconds=BREAKTHROUGH_RETRY_SECONDS)
        return BreakthroughResult(state, False, chance, normalized_roll, fee, None)
    state["qi"] -= stage.qi_cost
    state["spirit_stones"] -= stage.stone_cost
    state["stage_index"] += 1
    state["talent_points"] += 2 if stage.major_next else 1
    state["breakthrough_failures"] = 0
    state["breakthrough_retry_at"] = None
    return BreakthroughResult(state, True, chance, normalized_roll, stage.stone_cost, following)


def combat_power(state: Mapping[str, object]) -> int:
    base = stage_for(state).base_power + sum(item.power for item in equipped_items(state))
    bonus = min(50, talent_effects(state).get("power_pct", 0))
    return base * (100 + bonus) // 100


def daily_market(day: date | datetime) -> tuple[Item, ...]:
    if isinstance(day, datetime):
        day = day.astimezone(ICT).date() if day.tzinfo else day.replace(tzinfo=timezone.utc).astimezone(ICT).date()
    permanent = [item for item in ITEMS.values() if item.permanent_market]
    rotating = [item for item in ITEMS.values() if not item.permanent_market]
    digest = hashlib.sha256(f"tfvn-cultivation-market-v1:{day.isoformat()}".encode()).digest()
    seeded = random.Random(int.from_bytes(digest[:8], "big"))
    selected = seeded.sample(rotating, k=min(4, len(rotating)))
    return tuple(permanent + selected)


def buy_item(raw_state: Mapping[str, object], item_key: str, day: date | datetime) -> dict:
    state = deepcopy(dict(raw_state))
    item = ITEMS.get(item_key)
    if item is None or item not in daily_market(day):
        raise RuleError("Vật phẩm không có trong Chợ Đen hôm nay.")
    if _safe_int(state.get("stage_index")) < item.min_stage:
        raise RuleError("Cảnh giới hiện tại chưa thể sử dụng vật phẩm này.")
    if item_key in state.get("owned_items", []):
        raise RuleError("Đã sở hữu vật phẩm này.")
    if _safe_int(state.get("spirit_stones")) < item.price:
        raise RuleError(f"Cần {item.price:,} Linh Thạch.")
    state["spirit_stones"] -= item.price
    state.setdefault("owned_items", []).append(item_key)
    return state


def equip_item(raw_state: Mapping[str, object], item_key: str) -> dict:
    state = deepcopy(dict(raw_state))
    item = ITEMS.get(item_key)
    if item is None or item_key not in state.get("owned_items", []):
        raise RuleError("Bạn chưa sở hữu vật phẩm này.")
    if _safe_int(state.get("stage_index")) < item.min_stage:
        raise RuleError("Cảnh giới hiện tại chưa thể trang bị vật phẩm này.")
    state.setdefault("equipped", {})[item.slot] = item_key
    return state


def salvage_item(raw_state: Mapping[str, object], item_key: str) -> dict:
    state = deepcopy(dict(raw_state))
    item = ITEMS.get(item_key)
    if item is None or item_key not in state.get("owned_items", []):
        raise RuleError("Bạn chưa sở hữu vật phẩm này.")
    state["owned_items"].remove(item_key)
    if state.get("equipped", {}).get(item.slot) == item_key:
        state["equipped"][item.slot] = None
    state.setdefault("materials", {}).setdefault("manh_phap_bao", 0)
    state["materials"]["manh_phap_bao"] += item.salvage_fragments
    return state


def craft_item(raw_state: Mapping[str, object], recipe_key: str) -> dict:
    state = deepcopy(dict(raw_state))
    recipe = RECIPES.get(recipe_key)
    if recipe is None:
        raise RuleError("Công thức không tồn tại.")
    if recipe.result_item in state.get("owned_items", []):
        raise RuleError("Đã sở hữu vật phẩm này.")
    discount = min(30, talent_effects(state).get("craft_discount_pct", 0))
    stone_cost = _ceil_div(recipe.stone_cost * (100 - discount), 100)
    if _safe_int(state.get("spirit_stones")) < stone_cost:
        raise RuleError(f"Cần {stone_cost:,} Linh Thạch để luyện.")
    costs = {
        key: _ceil_div(value * (100 - discount), 100)
        for key, value in recipe.materials.items()
    }
    for key, amount in costs.items():
        if _safe_int(state.get("materials", {}).get(key)) < amount:
            raise RuleError(f"Không đủ {MATERIAL_NAMES[key]}.")
    state["spirit_stones"] -= stone_cost
    for key, amount in costs.items():
        state["materials"][key] -= amount
    state.setdefault("owned_items", []).append(recipe.result_item)
    return state


def clear_tower_floor(raw_state: Mapping[str, object], floor: int | None = None) -> dict:
    state = deepcopy(dict(raw_state))
    current = _safe_int(state.get("tower_floor"))
    requested = current + 1 if floor is None else int(floor)
    if requested != current + 1 or requested < 1 or requested > len(TOWER_FLOORS):
        raise RuleError("Chỉ có thể khiêu chiến tầng kế tiếp.")
    definition = TOWER_FLOORS[requested - 1]
    power = combat_power(state)
    if power < definition.required_power:
        raise RuleError(f"Cần {definition.required_power:,} lực chiến; hiện có {power:,}.")
    state["tower_floor"] = requested
    state["spirit_stones"] += definition.stone_reward
    if definition.material:
        state["materials"][definition.material] += definition.material_amount
    return state


def expedition_duration_seconds(state: Mapping[str, object], hours: int) -> int:
    if hours not in EXPEDITION_HOURS:
        raise RuleError("Thời lượng Bí Cảnh phải là 2, 4 hoặc 8 giờ.")
    reduction = min(30, talent_effects(state).get("expedition_speed_pct", 0))
    return _ceil_div(hours * SECONDS_PER_HOUR * (100 - reduction), 100)


def start_expedition(raw_state: Mapping[str, object], zone_key: str, hours: int, now: datetime) -> Settlement:
    settled = settle_state(raw_state, now, force=True)
    state = settled.state
    zone = EXPEDITION_ZONES.get(zone_key)
    if zone is None:
        raise RuleError("Bí Cảnh phải là linhduoc, cokhoang hoặc yeuthuson.")
    if state["stage_index"] < zone.min_stage:
        raise RuleError("Cảnh giới chưa đủ để vào Bí Cảnh này.")
    if isinstance(state.get("session"), Mapping) and state["session"].get("kind") == "expedition":
        raise RuleError("Đang ở trong một Bí Cảnh khác.")
    duration = expedition_duration_seconds(state, int(hours))
    now = naive_utc(now)
    state["session"] = {
        "kind": "expedition",
        "started_at": now,
        "settled_at": now,
        "ends_at": now + timedelta(seconds=duration),
        "zone": zone_key,
        "hours": int(hours),
        "focus": state["focus"],
    }
    return Settlement(state, settled.qi_gained, settled.stones_gained, settled_seconds=settled.settled_seconds, capped_seconds=settled.capped_seconds)


def cancel_expedition(raw_state: Mapping[str, object], now: datetime) -> dict:
    state = normalize_state(raw_state, now)
    session = state.get("session")
    if not isinstance(session, Mapping) or session.get("kind") != "expedition":
        raise RuleError("Bạn không ở trong Bí Cảnh.")
    ends_at = session.get("ends_at")
    if isinstance(ends_at, datetime) and naive_utc(now) >= ends_at:
        raise RuleError("Bí Cảnh đã hoàn tất; dùng `tutien bicanh claim` để nhận thưởng.")
    return start_meditation(state, now, str(session.get("focus", state["focus"])))


def week_key_ict(value: datetime) -> str:
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    local = aware.astimezone(ICT)
    monday = local.date() - timedelta(days=local.weekday())
    return monday.isoformat()


def reset_exchange_week(state: dict, now: datetime) -> dict:
    current = week_key_ict(now)
    if state.get("exchange_week") != current:
        state["exchange_week"] = current
        state["tc_spent_week"] = 0
        state["tc_earned_week"] = 0
    return state


def exchange_buy_stones(raw_state: Mapping[str, object], tc_amount: int, now: datetime) -> tuple[dict, int]:
    state = reset_exchange_week(normalize_state(raw_state, now), now)
    amount = int(tc_amount)
    if amount < 1:
        raise RuleError("Số TC phải lớn hơn 0.")
    remaining = TC_BUY_WEEKLY_CAP - state["tc_spent_week"]
    if amount > remaining:
        raise RuleError(f"Tuần này chỉ còn đổi được {remaining} TC.")
    state["tc_spent_week"] += amount
    state["spirit_stones"] += amount * TC_TO_STONE_RATE
    return state, -amount


def exchange_sell_stones(raw_state: Mapping[str, object], stone_amount: int, now: datetime) -> tuple[dict, int]:
    state = reset_exchange_week(normalize_state(raw_state, now), now)
    amount = int(stone_amount)
    if amount < STONE_TO_TC_RATE or amount % STONE_TO_TC_RATE:
        raise RuleError(f"Số Linh Thạch phải là bội số của {STONE_TO_TC_RATE}.")
    tc_amount = amount // STONE_TO_TC_RATE
    remaining = TC_SELL_WEEKLY_CAP - state["tc_earned_week"]
    if tc_amount > remaining:
        raise RuleError(f"Tuần này chỉ còn nhận được {remaining} TC.")
    if state["spirit_stones"] < amount:
        raise RuleError("Không đủ Linh Thạch.")
    state["spirit_stones"] -= amount
    state["tc_earned_week"] += tc_amount
    return state, tc_amount


def progress_bar(current: int, required: int | None, segments: int = 12) -> str:
    if required is None or required <= 0:
        return "█" * segments
    filled = max(0, min(segments, current * segments // required))
    return "█" * filled + "░" * (segments - filled)


def clip_text(text: str, limit: int) -> str:
    value = str(text)
    if len(value) <= limit:
        return value
    if limit <= 1:
        return "…"[:limit]
    return value[: limit - 1] + "…"


def item_stat_summary(item: Item) -> str:
    parts: list[str] = []
    if item.power:
        parts.append(f"+{item.power} lực chiến")
    if item.qi_bonus_bp:
        parts.append(f"+{item.qi_bonus_bp // 100}% Tu Vi")
    if item.stone_bonus_bp:
        parts.append(f"+{item.stone_bonus_bp // 100}% LT")
    if item.storage_hours:
        parts.append(f"+{item.storage_hours}h trữ")
    return " · ".join(parts) if parts else "Trang bị cơ bản"


def stage_short_name(stage: Stage) -> str:
    if " · " in stage.name:
        return stage.name.split(" · ", 1)[1]
    return stage.name


def realm_map_text(state: Mapping[str, object]) -> str:
    current = min(_safe_int(state.get("stage_index")), len(STAGES) - 1)
    groups: dict[str, list[tuple[int, Stage]]] = {}
    for index, stage in enumerate(STAGES):
        groups.setdefault(stage.realm_key, []).append((index, stage))
    lines: list[str] = []
    for realm_key, title in REALM_GROUP_NAMES.items():
        parts: list[str] = []
        for index, stage in groups.get(realm_key, ()):
            label = stage_short_name(stage)
            if index == current:
                parts.append(f"**▶{label}**")
            elif index < current:
                parts.append(f"✓{label}")
            else:
                parts.append(label)
        lines.append(f"{title}: {' · '.join(parts)}")
    return "\n".join(lines)


def tower_map_text(cleared_floor: int) -> str:
    cleared = min(30, max(0, int(cleared_floor)))
    lines: list[str] = []
    for start in range(1, 31, 5):
        cells: list[str] = []
        for floor in range(start, start + 5):
            if floor <= cleared:
                cells.append("👑" if floor % 5 == 0 else "✅")
            else:
                cells.append("⬜")
        lines.append(f"{start:>2}–{start + 4}: {''.join(cells)}")
    return "\n".join(lines)


def recipe_cost_text(recipe: Recipe, state: Mapping[str, object] | None = None) -> str:
    discount = 0
    if state is not None:
        discount = min(30, talent_effects(state).get("craft_discount_pct", 0))
    stone_cost = _ceil_div(recipe.stone_cost * (100 - discount), 100)
    materials = " · ".join(
        f"{MATERIAL_NAMES[key]} ×{_ceil_div(amount * (100 - discount), 100)}"
        for key, amount in recipe.materials.items()
    )
    return f"{stone_cost:,} LT · {materials}"


def format_duration_seconds(seconds: int) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, SECONDS_PER_HOUR)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes:02d}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
