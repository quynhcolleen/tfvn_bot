import random
import datetime
import asyncio


def get_daily_seed(user_id, command):
    """Stable daily seed per user + command name."""
    return f"{user_id}-{command}-{datetime.date.today()}"


def get_daily_percentage(user_id, command):
    """Deterministic 0-100 percentage for today (does not touch global random)."""
    rng = random.Random(get_daily_seed(user_id, command))
    return rng.randint(0, 100)


def get_daily_number(user_id, command, min_value=-100, max_value=100):
    """Deterministic integer in [min_value, max_value] for today."""
    rng = random.Random(get_daily_seed(user_id, command))
    return rng.randint(min_value, max_value)


def format_signed(n):
    """
    Format a signed integer for Discord embeds without markdown glitches.
    Uses ASCII '+' for positive; unicode minus (U+2212) for negative so
    Discord does not treat a line as a bullet list.
    Wrapped in backticks for stable monospace rendering.
    Never uses percent.
    """
    if n > 0:
        text = f"+{n}"
    elif n < 0:
        text = f"\u2212{abs(n)}"  # − not hyphen-minus
    else:
        text = "0"
    return f"`{text}`"


def create_signed_icon_bar(
    score,
    bar_length=10,
    pos_icon="✨",
    neg_icon="🌑",
    zero_icon="⚪",
    empty="·",
):
    """
    Icon bar that matches the integer count (not a percent map).

    filled = min(abs(score), bar_length)  — 1 icon per unit, capped at bar_length
    +score -> pos_icon
    -score -> neg_icon
     0     -> zero_icon * bar_length
    """
    if score == 0:
        return zero_icon * bar_length

    filled = min(abs(int(score)), bar_length)
    icon = pos_icon if score > 0 else neg_icon
    return icon * filled + empty * (bar_length - filled)


def count_polarity_icons(bar, polarity_icon):
    """Count leading polarity icons in a bar string (for tests / inspection)."""
    count = 0
    # emoji can be multi-codepoint; walk by matching the icon string
    while bar.startswith(polarity_icon):
        count += 1
        bar = bar[len(polarity_icon) :]
    return count


def create_progress_bar(percentage, bar_length=20):
    """Build a block progress bar like gay_meter (percent meters only)."""
    filled = int((percentage / 100) * bar_length)
    return "█" * filled + "░" * (bar_length - filled)


def get_meter_color(percentage, reverse=False):
    """
    Color the meter by severity (percent meters).
    reverse=False: high % = good (greener)
    reverse=True:  high % = bad  (redder)
    """
    import discord

    value = (100 - percentage) if reverse else percentage
    if value >= 75:
        return discord.Color.from_rgb(46, 204, 113)
    elif value >= 50:
        return discord.Color.from_rgb(241, 196, 15)
    elif value >= 25:
        return discord.Color.from_rgb(230, 126, 34)
    else:
        return discord.Color.from_rgb(231, 76, 60)


async def fake_loading(ctx, start_message, done_message=None, emoji="🔍"):
    """
    Fake dramatic loading like gay_meter:
    - send start_message
    - cycle 3 random FAKE_LOADING_SENTENCES
    - optionally edit to done_message
    Returns the loading message to edit with the final embed.
    """
    loading_message = await ctx.send(start_message)
    await asyncio.sleep(1)

    sentences = getattr(ctx.bot, "FAKE_LOADING_SENTENCES", [])
    if sentences:
        random_sentences = random.sample(sentences, min(3, len(sentences)))
        for sentence in random_sentences:
            await loading_message.edit(content=f"{sentence} ⏳")
            await asyncio.sleep(3)

    if done_message is None:
        done_message = f"Hoàn thành! 🎉 {emoji}"
    await loading_message.edit(content=done_message)
    return loading_message


def build_meter_embed(
    ctx,
    member,
    title,
    description,
    percentage,
    tease,
    footer,
    result_label="Kết quả:",
    color=None,
    reverse=False,
    extra_value=None,
    score_display=None,
    bar_percentage=None,
):
    """
    Build the final meter embed (same layout as gay_meter).

    score_display: override the main score text (e.g. "+42" instead of "42%").
    bar_percentage: override value used for the progress bar (0-100).
    """
    import discord

    bar_value = percentage if bar_percentage is None else bar_percentage
    bar = create_progress_bar(bar_value)

    if color is None:
        color = get_meter_color(bar_value, reverse=reverse)

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )
    embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
    embed.set_thumbnail(url=member.display_avatar.url)

    score_text = score_display if score_display is not None else f"{percentage}%"
    value = f"{bar} **{score_text}**"
    if extra_value:
        value += f"\n{extra_value}"
    value += f"\n```{tease}```"

    embed.add_field(name=result_label, value=value, inline=False)
    embed.set_footer(text=footer)
    return embed


def pick_tease(percentage, teases):
    """
    Pick a tease string from threshold list.
    teases: list of (upper_bound_or_None, text)
      e.g. [(10, "..."), (30, "..."), (None, "else text")]
    """
    for upper, text in teases:
        if upper is None or percentage < upper:
            return text
    return teases[-1][1]
