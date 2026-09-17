"""Owner-only Discord dashboard for Tiên Lộ."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import discord

from cogs.cultivation import _cultivation_helpers as rules


if TYPE_CHECKING:
    from cogs.cultivation.cultivation import CultivationCog

DASHBOARD_TIMEOUT_SECONDS = 180
NONE_VALUE = "none"

PANEL_REALM = "realm"
PANEL_PATH = "path"
PANEL_MARKET = "market"
PANEL_INVENTORY = "inventory"
PANEL_PVE = "pve"
DASHBOARD_PANELS = (
    PANEL_REALM,
    PANEL_PATH,
    PANEL_MARKET,
    PANEL_INVENTORY,
    PANEL_PVE,
)
PANEL_CHOICES: tuple[tuple[str, str, str, str], ...] = (
    (PANEL_REALM, "Cảnh giới", "Bế Quan, đột phá và Động Phủ", "☯️"),
    (PANEL_PATH, "Phái & Thiên phú", "Chọn phái và cộng điểm Thiên Phú", "🌿"),
    (PANEL_MARKET, "Chợ", "Chợ Đen cố định và luân phiên theo ngày ICT", "🏪"),
    (PANEL_INVENTORY, "Kho & Trang bị", "Trang bị, phân rã và luyện khí", "🎒"),
    (
        PANEL_PVE,
        "Tháp Thí Luyện & Bí Cảnh",
        "Khiêu chiến tháp và đi Bí Cảnh",
        "⚔️",
    ),
)
DESTRUCTIVE_ACTIONS = frozenset(
    {"reset_path", "salvage", "expedition_cancel"}
)


def is_dashboard_panel(value: str | None) -> bool:
    return value in DASHBOARD_PANELS


class CultivationView(discord.ui.View):
    """Short-lived dashboard controlled only by the invoking member."""

    def __init__(
        self,
        cog: CultivationCog,
        author_id: int,
        *,
        panel: str = PANEL_REALM,
        state: dict | None = None,
    ) -> None:
        super().__init__(timeout=DASHBOARD_TIMEOUT_SECONDS)
        self.cog = cog
        self.author_id = author_id
        self.message: discord.Message | None = None
        self.panel = panel if is_dashboard_panel(panel) else PANEL_REALM
        self.selected_talent: str | None = None
        self.selected_market: str | None = None
        self.selected_item: str | None = None
        self.selected_recipe: str | None = None
        self.selected_zone: str | None = None
        self.selected_hours: int = 2
        self.confirming: tuple[str, str | None] | None = None
        self.claim_button: discord.ui.Button | None = None
        self.breakthrough_button: discord.ui.Button | None = None
        self.cave_button: discord.ui.Button | None = None
        self.trial_button: discord.ui.Button | None = None
        self.rebuild(state)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "Chỉ đạo hữu đã mở Tiên Lộ mới dùng được bảng này.",
            ephemeral=True,
        )
        return False

    async def _run(
        self,
        interaction: discord.Interaction,
        action: str,
        value: str | None = None,
    ) -> None:
        resolved = self._resolve_value(action, value)
        if action in {"buy", "equip", "salvage", "craft", "talent"} and not resolved:
            await interaction.response.send_message(
                "❌ Hãy chọn một mục hợp lệ trước.",
                ephemeral=True,
            )
            return
        if action == "expedition_start" and resolved is None:
            await interaction.response.send_message(
                "❌ Hãy chọn Bí Cảnh trước khi xuất phát.",
                ephemeral=True,
            )
            return
        if action in DESTRUCTIVE_ACTIONS:
            token = (action, resolved)
            if self.confirming != token:
                self.confirming = token
                await self.cog.handle_dashboard_action(
                    interaction,
                    self,
                    "refresh",
                    None,
                    notice=self._confirm_notice(action, resolved),
                    notice_name="Xác nhận",
                )
                return
        else:
            self.confirming = None
        await self.cog.handle_dashboard_action(
            interaction, self, action, resolved
        )

    def _resolve_value(self, action: str, value: str | None) -> str | None:
        if action == "buy":
            return value or self.selected_market
        if action in {"equip", "salvage"}:
            return value or self.selected_item
        if action == "craft":
            return value or self.selected_recipe
        if action == "talent":
            return value or self.selected_talent
        if action == "expedition_start":
            if self.selected_zone not in rules.EXPEDITION_ZONES:
                return None
            hours = self.selected_hours if self.selected_hours in rules.EXPEDITION_HOURS else 2
            return f"{self.selected_zone}:{hours}"
        return value

    def _confirm_notice(self, action: str, value: str | None) -> str:
        if action == "reset_path":
            return (
                "Nhấn **Tẩy tủy** lần nữa để xác nhận. "
                "Phái bị xóa, điểm Thiên Phú được hoàn, và phải chờ 7 ngày."
            )
        if action == "salvage":
            item = rules.ITEMS.get(str(value))
            name = item.name if item else "vật phẩm này"
            return (
                f"Nhấn **Phân rã** lần nữa để phá **{name}**. "
                "Không hoàn tác được."
            )
        return (
            "Nhấn **Hủy Bí Cảnh** lần nữa. "
            "Chuyến đi sẽ kết thúc và không nhận thưởng."
        )

    def apply_selection(self, token: str) -> bool:
        kind, _, raw = token.partition(":")
        if raw in {"", NONE_VALUE}:
            return False
        if kind == "talent" and raw in rules.TALENTS:
            self.selected_talent = raw
            return True
        if kind == "market" and raw in rules.ITEMS:
            self.selected_market = raw
            return True
        if kind == "item" and raw in rules.ITEMS:
            self.selected_item = raw
            return True
        if kind == "recipe" and raw in rules.RECIPES:
            self.selected_recipe = raw
            return True
        if kind == "zone" and raw in rules.EXPEDITION_ZONES:
            self.selected_zone = raw
            return True
        if kind == "hours":
            try:
                hours = int(raw)
            except (TypeError, ValueError):
                return False
            if hours in rules.EXPEDITION_HOURS:
                self.selected_hours = hours
                return True
        return False

    def remember_success(self, action: str) -> None:
        self.confirming = None
        if action == "salvage":
            self.selected_item = None
        elif action == "buy":
            self.selected_market = None
        elif action == "craft":
            self.selected_recipe = None
        elif action == "reset_path":
            self.selected_talent = None
        elif action == "expedition_start":
            self.selected_zone = None

    def rebuild(self, state: dict | None = None) -> None:
        self.clear_items()
        self.claim_button = None
        self.breakthrough_button = None
        self.cave_button = None
        self.trial_button = None
        if self.panel == PANEL_PATH:
            self._build_path_panel(state)
        elif self.panel == PANEL_MARKET:
            self._build_market_panel(state)
        elif self.panel == PANEL_INVENTORY:
            self._build_inventory_panel(state)
        elif self.panel == PANEL_PVE:
            self._build_pve_panel(state)
        else:
            self.panel = PANEL_REALM
            self._build_realm_panel(state)
        self.add_item(_PanelSelect(self.panel))

    def _build_realm_panel(self, state: dict | None) -> None:
        self.claim_button = _ActionButton(
            "claim",
            label="Thu công",
            emoji="🧘",
            style=discord.ButtonStyle.success,
            row=0,
        )
        self.breakthrough_button = _ActionButton(
            "breakthrough",
            label="Đột phá",
            emoji="⚡",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        self.cave_button = _ActionButton(
            "cave",
            label="Nâng Động Phủ",
            emoji="🏚️",
            style=discord.ButtonStyle.secondary,
            row=0,
            disabled=state is not None and rules.cave_upgrade_cost(state) is None,
        )
        self.add_item(self.claim_button)
        self.add_item(self.breakthrough_button)
        self.add_item(self.cave_button)
        current_focus = None
        if state is not None and state.get("focus") in rules.FOCUS_MODIFIERS:
            current_focus = str(state["focus"])
        self.add_item(_FocusSelect(current_focus))

    def _build_path_panel(self, state: dict | None) -> None:
        path = state.get("path") if isinstance(state, dict) else None
        stage_index = int(state["stage_index"]) if isinstance(state, dict) else 0
        can_choose = path not in rules.PATH_NAMES and stage_index >= 1
        self.add_item(_PathSelect(path if path in rules.PATH_NAMES else None, can_choose))
        talents = [
            definition
            for definition in rules.TALENTS.values()
            if definition.path == path
        ]
        points = int(state.get("talent_points", 0)) if isinstance(state, dict) else 0
        ranks = state.get("talents", {}) if isinstance(state, dict) else {}
        if not isinstance(ranks, dict):
            ranks = {}
        if self.selected_talent and (
            self.selected_talent not in rules.TALENTS
            or rules.TALENTS[self.selected_talent].path != path
        ):
            self.selected_talent = None
        selected_rank = int(ranks.get(self.selected_talent, 0)) if self.selected_talent else 0
        selected_max = (
            rules.TALENTS[self.selected_talent].max_rank
            if self.selected_talent in rules.TALENTS
            else 0
        )
        self.add_item(
            _ChoiceSelect(
                "talent",
                placeholder="Chọn Thiên Phú để cộng điểm…",
                options=[
                    discord.SelectOption(
                        label=f"{definition.name} · {int(ranks.get(definition.key, 0))}/{definition.max_rank}",
                        value=definition.key,
                        description=rules.clip_text(definition.description, 100),
                        default=definition.key == self.selected_talent,
                    )
                    for definition in talents
                ],
                empty_label="Chọn phái trước",
                row=1,
                disabled=not talents,
            )
        )
        reset_locked = False
        if isinstance(state, dict):
            reset_at = state.get("path_reset_at")
            now = state.get("updated_at")
            reset_locked = (
                isinstance(reset_at, datetime)
                and isinstance(now, datetime)
                and now < reset_at
            )
        confirming_reset = self.confirming == ("reset_path", None)
        self.add_item(
            _ActionButton(
                "talent",
                label="Cộng 1 điểm",
                emoji="🌿",
                style=discord.ButtonStyle.primary,
                row=2,
                disabled=(
                    not self.selected_talent
                    or points < 1
                    or selected_rank >= selected_max
                ),
            )
        )
        self.add_item(
            _ActionButton(
                "reset_path",
                label="Xác nhận tẩy tủy" if confirming_reset else "Tẩy tủy",
                emoji="💧",
                style=discord.ButtonStyle.danger,
                row=2,
                disabled=path not in rules.PATH_NAMES or reset_locked,
            )
        )

    def _build_market_panel(self, state: dict | None) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if isinstance(state, dict) and isinstance(state.get("updated_at"), datetime):
            now = state["updated_at"]
        owned = set(state.get("owned_items", [])) if isinstance(state, dict) else set()
        offers = rules.daily_market(now)
        if self.selected_market and self.selected_market not in {item.key for item in offers}:
            self.selected_market = None
        self.add_item(
            _ChoiceSelect(
                "market",
                placeholder="Chọn vật phẩm Chợ Đen…",
                options=[
                    discord.SelectOption(
                        label=rules.clip_text(item.name, 100),
                        value=item.key,
                        description=rules.clip_text(
                            (
                                f"{item.price:,} LT · đã sở hữu"
                                if item.key in owned
                                else f"{item.price:,} LT · {rules.item_stat_summary(item)}"
                            ),
                            100,
                        ),
                        emoji="📌" if item.permanent_market else "🔄",
                        default=item.key == self.selected_market,
                    )
                    for item in offers
                ],
                empty_label="Chợ đang trống",
                row=0,
            )
        )
        selected = rules.ITEMS.get(self.selected_market) if self.selected_market else None
        stage_index = int(state["stage_index"]) if isinstance(state, dict) else 0
        stones = int(state.get("spirit_stones", 0)) if isinstance(state, dict) else 0
        cannot_buy = (
            selected is None
            or selected.key in owned
            or stones < selected.price
            or stage_index < selected.min_stage
        )
        self.add_item(
            _ActionButton(
                "buy",
                label="Mua",
                emoji="🪙",
                style=discord.ButtonStyle.success,
                row=1,
                disabled=cannot_buy,
            )
        )

    def _build_inventory_panel(self, state: dict | None) -> None:
        owned_keys = [
            key
            for key in (state.get("owned_items", []) if isinstance(state, dict) else [])
            if key in rules.ITEMS
        ]
        equipped = state.get("equipped", {}) if isinstance(state, dict) else {}
        if not isinstance(equipped, dict):
            equipped = {}
        if self.selected_item not in owned_keys:
            self.selected_item = None
        stage_index = int(state["stage_index"]) if isinstance(state, dict) else 0
        selected_item = rules.ITEMS.get(self.selected_item) if self.selected_item else None
        self.add_item(
            _ChoiceSelect(
                "item",
                placeholder="Chọn trang bị trong kho…",
                options=[
                    discord.SelectOption(
                        label=rules.clip_text(rules.ITEMS[key].name, 100),
                        value=key,
                        description=rules.clip_text(
                            (
                                f"{rules.GEAR_SLOT_NAMES[rules.ITEMS[key].slot]}"
                                + (
                                    " · đang mặc"
                                    if equipped.get(rules.ITEMS[key].slot) == key
                                    else ""
                                )
                                + f" · {rules.item_stat_summary(rules.ITEMS[key])}"
                            ),
                            100,
                        ),
                        default=key == self.selected_item,
                    )
                    for key in owned_keys
                ],
                empty_label="Chưa có trang bị",
                row=0,
            )
        )
        confirming_salvage = self.confirming == ("salvage", self.selected_item)
        self.add_item(
            _ActionButton(
                "equip",
                label="Trang bị",
                emoji="🛡️",
                style=discord.ButtonStyle.primary,
                row=1,
                disabled=selected_item is None or stage_index < selected_item.min_stage,
            )
        )
        self.add_item(
            _ActionButton(
                "salvage",
                label="Xác nhận phân rã" if confirming_salvage else "Phân rã",
                emoji="♻️",
                style=discord.ButtonStyle.danger,
                row=1,
                disabled=selected_item is None,
            )
        )
        if self.selected_recipe and self.selected_recipe not in rules.RECIPES:
            self.selected_recipe = None
        owned = set(owned_keys)
        self.add_item(
            _ChoiceSelect(
                "recipe",
                placeholder="Chọn công thức luyện khí…",
                options=[
                    discord.SelectOption(
                        label=rules.clip_text(rules.ITEMS[recipe.result_item].name, 100),
                        value=recipe.key,
                        description=rules.clip_text(
                            (
                                "Đã sở hữu"
                                if recipe.result_item in owned
                                else rules.recipe_cost_text(recipe, state)
                            ),
                            100,
                        ),
                        default=recipe.key == self.selected_recipe,
                    )
                    for recipe in rules.RECIPES.values()
                ],
                empty_label="Không có công thức",
                row=2,
            )
        )
        recipe = rules.RECIPES.get(self.selected_recipe) if self.selected_recipe else None
        self.add_item(
            _ActionButton(
                "craft",
                label="Luyện",
                emoji="🔨",
                style=discord.ButtonStyle.success,
                row=3,
                disabled=recipe is None or recipe.result_item in owned,
            )
        )

    def _build_pve_panel(self, state: dict | None) -> None:
        tower_floor = int(state.get("tower_floor", 0)) if isinstance(state, dict) else 0
        session = state.get("session") if isinstance(state, dict) else None
        in_expedition = isinstance(session, dict) and session.get("kind") == "expedition"
        finished = False
        if in_expedition:
            ends_at = session.get("ends_at")
            now = state.get("updated_at") if isinstance(state, dict) else None
            finished = (
                isinstance(ends_at, datetime)
                and isinstance(now, datetime)
                and now >= ends_at
            )
        confirming_cancel = self.confirming == ("expedition_cancel", None)
        if self.selected_zone and self.selected_zone not in rules.EXPEDITION_ZONES:
            self.selected_zone = None
        stage_index = int(state["stage_index"]) if isinstance(state, dict) else 0
        selected_zone = rules.EXPEDITION_ZONES.get(self.selected_zone)
        start_locked = (
            in_expedition
            or selected_zone is None
            or stage_index < selected_zone.min_stage
        )
        self.trial_button = _ActionButton(
            "trial",
            label="Khiêu chiến tầng kế",
            emoji="⚔️",
            style=discord.ButtonStyle.primary,
            row=0,
            disabled=tower_floor >= len(rules.TOWER_FLOORS),
        )
        self.add_item(self.trial_button)
        self.add_item(
            _ActionButton(
                "expedition_start",
                label="Vào Bí Cảnh",
                emoji="🌌",
                style=discord.ButtonStyle.success,
                row=0,
                disabled=start_locked,
            )
        )
        self.add_item(
            _ActionButton(
                "expedition_claim",
                label="Thu công Bí Cảnh",
                emoji="📥",
                style=discord.ButtonStyle.secondary,
                row=0,
                disabled=not in_expedition or not finished,
            )
        )
        self.add_item(
            _ActionButton(
                "expedition_cancel",
                label="Xác nhận hủy" if confirming_cancel else "Hủy Bí Cảnh",
                emoji="🚪",
                style=discord.ButtonStyle.danger,
                row=0,
                disabled=not in_expedition or finished,
            )
        )
        self.add_item(
            _ChoiceSelect(
                "zone",
                placeholder="Chọn Bí Cảnh…",
                options=[
                    discord.SelectOption(
                        label=zone.name,
                        value=zone.key,
                        description=rules.clip_text(
                            (
                                f"{zone.base_stones_per_two_hours} LT/2h"
                                + (
                                    " · chưa đủ cảnh giới"
                                    if stage_index < zone.min_stage
                                    else ""
                                )
                            ),
                            100,
                        ),
                        default=zone.key == self.selected_zone,
                    )
                    for zone in rules.EXPEDITION_ZONES.values()
                ],
                empty_label="Không có Bí Cảnh",
                row=1,
                disabled=in_expedition,
            )
        )
        hour_options: list[discord.SelectOption] = []
        for hours in rules.EXPEDITION_HOURS:
            duration = hours * rules.SECONDS_PER_HOUR
            if isinstance(state, dict):
                duration = rules.expedition_duration_seconds(state, hours)
            hour_options.append(
                discord.SelectOption(
                    label=f"{hours} giờ",
                    value=str(hours),
                    description=f"Thực tế {rules.format_duration_seconds(duration)}",
                    default=hours == self.selected_hours,
                )
            )
        self.add_item(
            _ChoiceSelect(
                "hours",
                placeholder="Chọn thời lượng Bí Cảnh…",
                options=hour_options,
                empty_label="Không có thời lượng",
                row=2,
                disabled=in_expedition,
            )
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class _ActionButton(discord.ui.Button):
    def __init__(self, action: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, CultivationView):
            return
        await view._run(interaction, self.action)


class _PanelSelect(discord.ui.Select):
    def __init__(self, current: str) -> None:
        super().__init__(
            placeholder="Chuyển bảng Tiên Lộ…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=label,
                    value=key,
                    description=description,
                    emoji=emoji,
                    default=key == current,
                )
                for key, label, description, emoji in PANEL_CHOICES
            ],
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, CultivationView):
            return
        await view._run(interaction, "navigate", self.values[0])


class _FocusSelect(discord.ui.Select):
    def __init__(self, current: str | None) -> None:
        super().__init__(
            placeholder="Chọn hướng Bế Quan…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=rules.FOCUS_NAMES[key],
                    value=key,
                    description=description,
                    emoji=emoji,
                    default=key == current,
                )
                for key, description, emoji in (
                    (rules.FOCUS_BALANCED, "100% Tu Vi · 100% Linh Thạch", "⚖️"),
                    (rules.FOCUS_QI, "125% Tu Vi · 60% Linh Thạch", "🧘"),
                    (rules.FOCUS_STONES, "75% Tu Vi · 150% Linh Thạch", "⛏️"),
                )
            ],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, CultivationView):
            return
        await view._run(interaction, "focus", self.values[0])


class _PathSelect(discord.ui.Select):
    def __init__(self, current: str | None, enabled: bool) -> None:
        super().__init__(
            placeholder="Chọn phái tu luyện…" if enabled else "Phái đã khóa",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=name,
                    value=key,
                    description=rules.clip_text(rules.PATH_DESCRIPTIONS[key], 100),
                    default=key == current,
                )
                for key, name in rules.PATH_NAMES.items()
            ],
            row=0,
            disabled=not enabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, CultivationView):
            return
        await view._run(interaction, "path", self.values[0])


class _ChoiceSelect(discord.ui.Select):
    def __init__(
        self,
        kind: str,
        *,
        placeholder: str,
        options: list[discord.SelectOption],
        empty_label: str,
        row: int,
        disabled: bool = False,
    ) -> None:
        self.kind = kind
        if options:
            select_options = options
            select_disabled = disabled
        else:
            select_options = [
                discord.SelectOption(label=empty_label, value=NONE_VALUE)
            ]
            select_disabled = True
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=select_options,
            row=row,
            disabled=select_disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, CultivationView):
            return
        await view._run(interaction, "select", f"{self.kind}:{self.values[0]}")
