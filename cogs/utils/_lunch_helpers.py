"""Pure parsing and selection helpers for Vietnamese lunch suggestions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import random
import re


MAX_BUDGET_DIGITS = 9
_BUDGET_PATTERN = re.compile(rf"[0-9]{{1,{MAX_BUDGET_DIGITS}}}[kK]?")
_FILTER_USAGE = (
    "Dùng một ngân sách nguyên dương (nghìn đồng, tối đa 9 chữ số) "
    "và/hoặc chay. Ví dụ: lunch 50 chay hoặc lunch chay 50k."
)


@dataclass(frozen=True)
class Food:
    """One source dish, with its estimated price in thousands of VND."""

    image: int
    name: str
    sub: str
    price: int
    veg: bool = False
    quip: str = ""


@dataclass(frozen=True)
class LunchFilters:
    """Optional price ceiling in thousands of VND and vegetarian filter."""

    budget: int | None = None
    vegetarian: bool = False


def load_foods(path: Path) -> tuple[Food, ...]:
    """Read and validate a UTF-8 source snapshot without accessing the network."""
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not load lunch foods from {path}: {error}") from error

    if not isinstance(records, list) or not records:
        raise ValueError(f"Lunch foods in {path} must be a nonempty JSON array.")

    foods: list[Food] = []
    images: set[int] = set()
    for index, record in enumerate(records, start=1):
        location = f"Lunch food {index} in {path}"
        if not isinstance(record, dict):
            raise ValueError(f"{location} must be a JSON object.")

        image = record.get("image")
        if type(image) is not int or image < 0:
            raise ValueError(f"{location} needs a nonnegative integer image ID.")
        if image in images:
            raise ValueError(f"{location} has duplicate image ID {image}.")

        for field in ("name", "sub"):
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{location} needs nonempty text for {field}.")

        price = record.get("price")
        if type(price) is not int or price <= 0:
            raise ValueError(f"{location} needs a positive integer price.")
        veg = record.get("veg", False)
        if not isinstance(veg, bool):
            raise ValueError(f"{location} needs a boolean veg flag.")
        quip = record.get("quip", "")
        if not isinstance(quip, str):
            raise ValueError(f"{location} needs text for quip.")

        foods.append(Food(image, record["name"], record["sub"], price, veg, quip))
        images.add(image)

    return tuple(foods)


def parse_budget(value: str) -> int:
    """Parse an optional k-suffixed positive budget in thousands of VND."""
    text = value.strip()
    if _BUDGET_PATTERN.fullmatch(text):
        budget = int(text.rstrip("kK"))
        if budget > 0:
            return budget
    raise ValueError(
        "Ngân sách phải là số nguyên dương, tối đa 9 chữ số "
        "(đơn vị nghìn đồng). Ví dụ: 50 hoặc 50k."
    )


def parse_lunch_filters(arguments: str) -> LunchFilters:
    """Parse one optional budget and one optional chay token in either order."""
    budget: int | None = None
    vegetarian = False
    for token in arguments.split():
        if token.casefold() == "chay":
            if vegetarian:
                raise ValueError(_FILTER_USAGE)
            vegetarian = True
        elif budget is None:
            try:
                budget = parse_budget(token)
            except ValueError as error:
                raise ValueError(_FILTER_USAGE) from error
        else:
            raise ValueError(_FILTER_USAGE)
    return LunchFilters(budget=budget, vegetarian=vegetarian)


def eligible_foods(
    foods: Sequence[Food], filters: LunchFilters,
) -> tuple[Food, ...]:
    """Return every dish satisfying both filters, including the budget boundary."""
    return tuple(
        food for food in foods
        if (filters.budget is None or food.price <= filters.budget)
        and (not filters.vegetarian or food.veg)
    )


def choose_food(
    foods: Sequence[Food], previous_image: int | None = None,
) -> Food:
    """Choose uniformly, avoiding the previous dish when an alternative exists."""
    if not foods:
        raise ValueError("Cannot choose a lunch dish from an empty selection.")
    candidates = tuple(food for food in foods if food.image != previous_image)
    return random.choice(candidates or tuple(foods))


def wish_tier(price: int) -> str:
    """Match the upstream wish animation tier to the dish price in thousands."""
    if price <= 65:
        return "blue"
    if price <= 130:
        return "purple"
    return "gold"


def format_price(price: int) -> str:
    """Format a source price in thousands as Vietnamese dong."""
    return f"{price * 1000:,}₫".replace(",", ".")
