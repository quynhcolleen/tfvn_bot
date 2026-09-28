"""Pure tarot deck, spreads, drawing, and reading-state helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import random
import unicodedata

from cogs.funny_things.tarot._tarot_cards import (
    MAJOR_ARCANA,
    MINOR_RANKS,
    SUIT_MEANINGS,
    SUIT_META,
)


MAX_QUESTION_LENGTH = 200
MAJOR_COUNT = 22
MINOR_PER_SUIT = 14
DECK_SIZE = MAJOR_COUNT + MINOR_PER_SUIT * 4

SPREAD_ORDER = ("single", "three", "five", "seven", "celtic")


@dataclass(frozen=True)
class TarotCard:
    """One card from a 78-card Rider-Waite-style deck."""

    id: str
    name_en: str
    name_vi: str
    short_en: str
    short_vi: str
    arcana: str
    suit: str | None
    number: int
    roman: str
    upright: str
    reversed: str
    keywords_upright: str
    keywords_reversed: str

    def meaning(self, reversed: bool) -> str:
        return self.reversed if reversed else self.upright

    def keywords(self, reversed: bool) -> str:
        return self.keywords_reversed if reversed else self.keywords_upright

    @property
    def asset_filename(self) -> str:
        """Bundled Rider-Waite-Smith image name under assets/tarot/."""

        return f"{self.id.replace(':', '-')}.webp"


@dataclass(frozen=True)
class SpreadPosition:
    """One labeled seat in a spread."""

    index: int
    key: str
    name_vi: str
    prompt: str


@dataclass(frozen=True)
class Spread:
    """A named layout with a fixed number of positions."""

    key: str
    name_vi: str
    name_en: str
    description: str
    aliases: tuple[str, ...]
    positions: tuple[SpreadPosition, ...]

    @property
    def card_count(self) -> int:
        return len(self.positions)


@dataclass(frozen=True)
class DrawnCard:
    """A card drawn into a specific spread seat."""

    card: TarotCard
    reversed: bool
    position: SpreadPosition

    @property
    def orientation_vi(self) -> str:
        return "Ngược" if self.reversed else "Xuôi"

    @property
    def meaning(self) -> str:
        return self.card.meaning(self.reversed)

    @property
    def keywords(self) -> str:
        return self.card.keywords(self.reversed)


@dataclass
class TarotReading:
    """Mutable reveal state for one dealt spread."""

    spread: Spread
    cards: tuple[DrawnCard, ...]
    question: str
    revealed: list[bool] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.cards) != self.spread.card_count:
            raise ValueError("Drawn cards must match the spread size")
        expected = [position.index for position in self.spread.positions]
        actual = [drawn.position.index for drawn in self.cards]
        if actual != expected:
            raise ValueError("Drawn card positions must follow the spread")
        if len(self.revealed) != len(self.cards):
            self.revealed = [False] * len(self.cards)
        else:
            self.revealed = [bool(flag) for flag in self.revealed]
        self.question = normalize_question(self.question)

    @property
    def all_revealed(self) -> bool:
        return all(self.revealed)

    @property
    def revealed_count(self) -> int:
        return sum(1 for flag in self.revealed if flag)

    def flip(self, index: int) -> bool:
        """Reveal one card. Return True when this call changed state."""

        if isinstance(index, bool) or not isinstance(index, int):
            raise IndexError("Card index is out of range")
        if not 0 <= index < len(self.cards):
            raise IndexError("Card index is out of range")
        if self.revealed[index]:
            return False
        self.revealed[index] = True
        return True

    def flip_all(self) -> int:
        """Reveal every remaining card. Return how many were newly flipped."""

        flipped = 0
        for index in range(len(self.cards)):
            if self.flip(index):
                flipped += 1
        return flipped


def major_short_en(name_en: str) -> str:
    """Compact English label that fits on a painted card face."""

    shortened = {
        "Wheel of Fortune": "Wheel",
        "The High Priestess": "Priestess",
        "The Hanged Man": "Hanged Man",
    }
    if name_en in shortened:
        return shortened[name_en]
    if name_en.startswith("The "):
        return name_en[4:]
    return name_en


def roman_numeral(value: int) -> str:
    """Render a major-arcana number as a compact Roman numeral."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Roman numeral input must be an integer")
    if value == 0:
        return "0"
    if not 1 <= value <= 21:
        raise ValueError("Roman numeral input must be from 0 through 21")
    parts: list[str] = []
    remaining = value
    for amount, glyph in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while remaining >= amount:
            parts.append(glyph)
            remaining -= amount
    return "".join(parts)


def _fold_alias(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold().strip())
    without_marks = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    return "".join(char for char in without_marks if char.isalnum())


def _position(
    index: int,
    key: str,
    name_vi: str,
    prompt: str,
) -> SpreadPosition:
    return SpreadPosition(index=index, key=key, name_vi=name_vi, prompt=prompt)


def _spread(
    key: str,
    name_vi: str,
    name_en: str,
    description: str,
    aliases: tuple[str, ...],
    positions: tuple[SpreadPosition, ...],
) -> Spread:
    return Spread(
        key=key,
        name_vi=name_vi,
        name_en=name_en,
        description=description,
        aliases=aliases,
        positions=positions,
    )


SPREADS: dict[str, Spread] = {
    "single": _spread(
        "single",
        "Một lá",
        "Single card",
        "Một lá cho năng lượng hôm nay hoặc một câu hỏi ngắn.",
        ("1", "1la", "single", "daily", "one", "homnay"),
        (
            _position(
                1,
                "today",
                "Năng lượng",
                "Năng lượng chủ đạo hoặc lời đáp trực tiếp.",
            ),
        ),
    ),
    "three": _spread(
        "three",
        "Ba lá",
        "Past / Present / Future",
        "Quá khứ, hiện tại và hướng sắp tới.",
        ("3", "3la", "three", "ppf", "pastpresentfuture"),
        (
            _position(1, "past", "Quá khứ", "Gốc rễ và điều đã đưa bạn tới đây."),
            _position(2, "present", "Hiện tại", "Tình huống đang diễn ra."),
            _position(3, "future", "Tương lai", "Hướng gần nếu giữ đà hiện tại."),
        ),
    ),
    "five": _spread(
        "five",
        "Năm lá",
        "Cross",
        "Thập tự nhỏ: hiện tại, nền tảng, quá khứ, mục tiêu và hướng tới.",
        ("5", "5la", "five", "cross", "thaptu"),
        (
            _position(1, "present", "Hiện tại", "Trọng tâm của câu hỏi."),
            _position(2, "root", "Nền tảng", "Điều đang nâng hoặc níu từ bên dưới."),
            _position(3, "past", "Quá khứ", "Ảnh hưởng vừa khép lại."),
            _position(4, "aim", "Mục tiêu", "Điều ý thức đang hướng tới."),
            _position(5, "ahead", "Hướng tới", "Bước tiếp theo đang mở."),
        ),
    ),
    "seven": _spread(
        "seven",
        "Bảy lá",
        "Horseshoe",
        "Móng ngựa: từ quá khứ đến kết quả, gồm ẩn số, trở ngại và lời khuyên.",
        ("7", "7la", "seven", "horseshoe", "mongngua"),
        (
            _position(1, "past", "Quá khứ", "Điều đã qua còn in dấu."),
            _position(2, "present", "Hiện tại", "Chỗ bạn đang đứng."),
            _position(3, "hidden", "Ẩn số", "Điều chưa nói hoặc chưa thấy."),
            _position(4, "obstacle", "Trở ngại", "Thứ đang chắn đường."),
            _position(5, "environment", "Môi trường", "Người khác và hoàn cảnh quanh bạn."),
            _position(6, "advice", "Lời khuyên", "Cách đi tiếp khôn hơn."),
            _position(7, "outcome", "Kết quả", "Hướng kết nếu làm theo nhịp này."),
        ),
    ),
    "celtic": _spread(
        "celtic",
        "Celtic Cross",
        "Celtic Cross",
        "Mười lá: thập tự trung tâm và cột nhân vật bên phải.",
        ("10", "10la", "celtic", "celticcross", "cc"),
        (
            _position(1, "present", "Hiện tại", "Tình huống và tâm điểm câu hỏi."),
            _position(2, "challenge", "Thách thức", "Điều cắt ngang, thử hoặc cản."),
            _position(3, "foundation", "Nền tảng", "Gốc rễ sâu, thường là xa hơn."),
            _position(4, "recent", "Quá khứ gần", "Ảnh hưởng vừa đi qua."),
            _position(5, "crown", "Mục tiêu", "Ý thức, khát vọng, điều có thể đạt."),
            _position(6, "near", "Tương lai gần", "Điều đang tiến tới."),
            _position(7, "self", "Bản thân", "Cách bạn đứng trong chuyện này."),
            _position(8, "others", "Môi trường", "Người quanh bạn và không khí chung."),
            _position(9, "hopes", "Hy vọng & lo", "Điều bạn mong và điều bạn sợ."),
            _position(10, "outcome", "Kết quả", "Hướng tổng nếu các lực này tiếp diễn."),
        ),
    ),
}

_ALIAS_TO_KEY: dict[str, str] = {}
for _spread_item in SPREADS.values():
    for _alias in (_spread_item.key, *_spread_item.aliases):
        folded = _fold_alias(_alias)
        if folded in _ALIAS_TO_KEY and _ALIAS_TO_KEY[folded] != _spread_item.key:
            raise RuntimeError(f"Duplicate tarot spread alias: {_alias}")
        _ALIAS_TO_KEY[folded] = _spread_item.key


def _build_deck() -> tuple[TarotCard, ...]:
    cards: list[TarotCard] = []
    for number, name_en, name_vi, short_vi, upright, reversed, kw_up, kw_rev in MAJOR_ARCANA:
        cards.append(
            TarotCard(
                id=f"major:{number:02d}",
                name_en=name_en,
                name_vi=name_vi,
                short_en=major_short_en(name_en),
                short_vi=short_vi,
                arcana="major",
                suit=None,
                number=number,
                roman=roman_numeral(number),
                upright=upright,
                reversed=reversed,
                keywords_upright=kw_up,
                keywords_reversed=kw_rev,
            )
        )

    rank_by_number = {number: row for number, *row in MINOR_RANKS}
    for suit_key, suit_en, suit_vi in SUIT_META:
        meanings = SUIT_MEANINGS[suit_key]
        if len(meanings) != MINOR_PER_SUIT:
            raise RuntimeError(f"Suit {suit_key} must have {MINOR_PER_SUIT} cards")
        for number, upright, reversed, kw_up, kw_rev in meanings:
            rank_en, rank_vi, rank_short = rank_by_number[number]
            cards.append(
                TarotCard(
                    id=f"{suit_key}:{number:02d}",
                    name_en=f"{rank_en} of {suit_en}",
                    name_vi=f"{rank_vi} {suit_vi}",
                    short_en=f"{rank_en} of {suit_en}",
                    short_vi=f"{rank_short} {suit_vi}",
                    arcana="minor",
                    suit=suit_key,
                    number=number,
                    roman=str(number),
                    upright=upright,
                    reversed=reversed,
                    keywords_upright=kw_up,
                    keywords_reversed=kw_rev,
                )
            )
    return tuple(cards)


ALL_CARDS: tuple[TarotCard, ...] = _build_deck()
CARDS_BY_ID: dict[str, TarotCard] = {card.id: card for card in ALL_CARDS}


def normalize_question(text: str, limit: int = MAX_QUESTION_LENGTH) -> str:
    """Collapse whitespace and cap the stored question length."""

    cleaned = " ".join(str(text).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def resolve_spread(token: str | None) -> Spread | None:
    """Return a spread for a command token, or None when it is not an alias."""

    if token is None:
        return None
    folded = _fold_alias(token)
    if not folded:
        return None
    key = _ALIAS_TO_KEY.get(folded)
    if key is None:
        return None
    return SPREADS[key]


def parse_tarot_query(query: str) -> tuple[Spread | None, str]:
    """Split `tarot` rest text into an optional spread and a question."""

    text = str(query).strip()
    if not text:
        return None, ""
    first, separator, rest = text.partition(" ")
    spread = resolve_spread(first)
    if spread is not None:
        return spread, normalize_question(rest)
    return None, normalize_question(text)


def deal_reading(
    spread: Spread,
    question: str = "",
    *,
    rng: random.Random | None = None,
) -> TarotReading:
    """Shuffle the deck and deal one card per spread seat, without replacement."""

    if spread.card_count > len(ALL_CARDS):
        raise ValueError("Spread is larger than the tarot deck")
    roller = rng if rng is not None else random.Random()
    pool = list(ALL_CARDS)
    roller.shuffle(pool)
    drawn = tuple(
        DrawnCard(
            card=card,
            reversed=roller.choice((False, True)),
            position=position,
        )
        for card, position in zip(
            pool[: spread.card_count],
            spread.positions,
            strict=True,
        )
    )
    return TarotReading(spread=spread, cards=drawn, question=question)


def card_button_label(position: SpreadPosition, card_count: int) -> str:
    """Short button label; names fit on 1–5 card spreads, numbers on larger ones."""

    if card_count <= 5:
        return f"{position.index}. {position.name_vi}"
    return str(position.index)


def card_field_name(drawn: DrawnCard) -> str:
    return f"{drawn.position.index}. {drawn.position.name_vi}"


def card_field_value(drawn: DrawnCard, revealed: bool) -> str:
    """Discord embed field body for one seat."""

    if not revealed:
        return f"🂠 *Chưa lật — {drawn.position.prompt}*"
    return (
        f"**{drawn.card.name_en}** — {drawn.orientation_vi}\n"
        f"*{drawn.keywords}*\n"
        f"{drawn.meaning}"
    )


def how_to_read_tarot() -> str:
    """Short intro at the top of the Hướng dẫn panel."""

    return (
        "78 lá Rider–Waite–Smith đã được xào và đặt **úp** xuống. "
        "Mỗi chỗ trên trải bài là một câu hỏi; bạn lật khi sẵn sàng để "
        "gắn đúng một lá vào đúng chỗ đó. Chỉ người hỏi được lật; "
        "mọi người vẫn mở được hướng dẫn này."
    )


def how_to_flip_tarot() -> str:
    """Detailed instructions for revealing cards one-by-one or all at once."""

    return (
        "• Ảnh lúc đầu chỉ thấy **mặt sau** (hoa hồng & lily) và **số vàng** — "
        "số đó trùng nút bên dưới.\n"
        "• Nhấn nút **số** (hoặc tên vị trí, với trải nhỏ) để lật **đúng một lá**. "
        "Ảnh đổi sang hình Rider–Waite, ô trên bảng hiện tên tiếng Anh, từ khóa "
        "và nghĩa.\n"
        "• Lá vừa lật: nút chuyển ✅, đổi màu, rồi **khóa**. Không úp lại được.\n"
        "• **Lật tất cả** mở mọi lá còn úp cùng lúc. Các lá đã lật giữ nguyên.\n"
        "• Có thể lật từng lá (1 → 2 → 3…) rồi mới Lật tất cả phần còn lại.\n"
        "• Nút lá đã lật không bấm lại; nếu bấm nhầm sẽ được báo là đã lật.\n"
        "• Chỉ người gọi lệnh lật được bài. Phiên đóng sau **5 phút** không thao tác."
    )


def how_to_interpret_flip() -> str:
    """How to read a card after it has been turned face-up."""

    return (
        "1. Đọc **tên vị trí** trước (Quá khứ, Thách thức, …): đó là câu hỏi "
        "của chỗ vừa lật, không phải nghĩa chung của cả trải.\n"
        "2. Nhìn **hình** trên lá, rồi **tên tiếng Anh** (The Fool, Ace of Cups, …) "
        "trên ảnh và trên bảng.\n"
        "3. Đọc dòng *từ khóa* rồi đoạn nghĩa bên dưới — nghĩa đã chọn sẵn "
        "xuôi hoặc ngược đúng hướng lá đang nằm.\n"
        "4. **Xuôi** (chữ vàng, hình đứng): năng lượng đi ra ngoài, đúng hướng "
        "của lá, việc đang hoặc sắp biểu hiện.\n"
        "5. **Ngược** (chữ đỏ, hình xoay 180°): cùng chủ đề nhưng bị cản, chậm, "
        "hướng vào trong, hoặc mặt trái — không tự động là điềm xấu.\n"
        "6. Đọc theo số **1 → lá cuối** để thành một mạch. Celtic Cross: "
        "thập tự giữa (1–6) rồi cột bên phải (7–10)."
    )


def picker_spread_guide() -> str:
    """Short catalog of every spread for the picker guide."""

    lines = []
    for key in SPREAD_ORDER:
        spread = SPREADS[key]
        lines.append(
            f"**{spread.name_vi}** ({spread.card_count} lá) — {spread.description}"
        )
    return "\n".join(lines)


def spread_position_guide(spread: Spread) -> str:
    """Numbered seat list for one spread."""

    lines = [
        f"{spread.name_vi} ({spread.name_en}) — {spread.description}",
        "Số vàng trên ảnh trùng nút lật. Đọc tên vị trí trước, rồi mới đọc lá.",
        "",
    ]
    lines.extend(
        f"**{position.index}. {position.name_vi}** — {position.prompt}"
        for position in spread.positions
    )
    return "\n".join(lines)


def reading_status_text(reading: TarotReading) -> str:
    if reading.all_revealed:
        return (
            f"Đã lật hết {reading.spread.card_count} lá. "
            "Đọc từng vị trí bên dưới, hoặc mở **Hướng dẫn**."
        )
    remaining = reading.spread.card_count - reading.revealed_count
    if remaining == reading.spread.card_count:
        return (
            "Nhấn từng lá để lật, **Lật tất cả**, hoặc **Hướng dẫn** để xem "
            "cách đọc trải này."
        )
    return (
        f"Đã lật {reading.revealed_count}/{reading.spread.card_count} lá. "
        f"Còn {remaining} lá úp."
    )


def flip_all_row(card_count: int) -> int:
    """Row for the flip-all button, sharing the last card row when a slot remains."""

    if card_count < 1:
        raise ValueError("Card count must be positive")
    last_card_row = (card_count - 1) // 5
    used_on_last_row = card_count - last_card_row * 5
    if used_on_last_row < 5:
        return last_card_row
    return last_card_row + 1
