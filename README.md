# TFVN Bot

TFVN Bot is a Vietnamese-first Discord community bot built for **Trap & Femboy VN**. It brings moderation, member onboarding, reminders, booster perks, persistent giveaways, social commands, an in-server economy, and Vietnamese word games into one modular `discord.py` application.

The bot uses prefix commands (the default prefix is `!tf `), stores persistent state in MongoDB, and organizes features as independently loaded cogs.

For a complete list of commands and automatic features, see [FUNCTIONS.md](FUNCTIONS.md).

> [!WARNING]
> This repository includes optional adult/NSFW cogs and third-party booru integrations. Only enable them for an adult community, keep them restricted to age-gated Discord channels, and follow Discord's rules and the upstream services' terms.

## Highlights

- **Community management:** welcome and differentiated leave/kick/ban announcements, verification, AFK tracking, birthdays, scheduled bedtime reminders, votes, and giveaways.
- **Moderation:** kick, ban/unban, soft-ban, mute, timeout, warnings, numbered audit cases, message cleanup, slow mode, nickname/role tools, and the Area 51 guard workflow.
- **Booster perks:** custom roles and voice rooms, with automatic cleanup after a member stops boosting.
- **Games and economy:** the global, persistent Tiên Lộ AFK cultivation game, daily Trap Coins, a configurable role/badge shop, transaction history, interactive Blackjack and five-card-draw Poker, persistent multiplayer Crocodile Dentist, slots, coin flips, Sic Bo, Vietnamese word chaining (`noitu`), and Vua Tiếng Việt (`vtv`).
- **Social and fun commands:** member interactions, rankings, avatars, random members, community-themed cards, and a collection of playful “meter” commands.
- **Operations:** an Administrator dashboard for bot/server health, private Doctor diagnostics, guild command auditing, CSV export, and guarded log pruning, with private Bot owner panels for joined-server management and recent lifecycle history.
- **Optional age-restricted features:** NSFW interactions and Rule34/Gelbooru searches, guarded by Discord's NSFW channel setting.
- **Persistent state:** MongoDB-backed balances, cultivation profiles, interactions, Crocodile Dentist games, game context, reminders, settings, giveaways, booster resources, moderation data, signed content proofs, guild command audit logs, and append-only bot lifecycle events.

## How it works

`main.py` creates the Discord bot, enables the member and message-content intents, loads static datasets from `data/`, and connects the cogs to the MongoDB database created in `db.py`.

Cog loading depends on `ENVIRONMENT`:

- `production` scans `cogs/` recursively and attempts to load every Python module whose filename does not start with `_`.
- `development` loads only the dotted module paths listed in the ignored `dev_cogs.txt` file. Wildcards such as `cogs.mod.*` are supported.

`DISABLED_COGS` accepts comma-separated dotted paths or wildcard patterns and
skips matching extensions before import.

The settings cog is always prioritized when present. It loads the MongoDB `global_variables` collection into `bot.global_vars` before feature cogs initialize. If an individual cog is missing its required configuration, the loader reports that failure and continues loading the remaining cogs.

## Requirements

- Python 3.11 (the version used by the Docker image)
- A Discord application and bot token
- A reachable, credentialed MongoDB deployment
- Optional Rule34 and Gelbooru API credentials if those cogs are enabled

In the Discord Developer Portal, enable these privileged gateway intents for the bot:

- **Server Members Intent**
- **Message Content Intent**

The bot also needs the Discord permissions used by the cogs you enable. For example, moderation and booster features require permissions such as Manage Messages, Manage Roles, Manage Channels, Create Invite, Moderate Members, Kick Members, Ban Members, or View Audit Log. View Audit Log lets departure announcements distinguish moderator kicks from members leaving voluntarily. Optional unban reinvites use a public rules, welcome, system, or command channel and require both the moderator and bot to have Create Invite there.

## Local development

### 1. Clone and install

```powershell
git clone https://github.com/quynhcolleen/tfvn_bot.git
cd tfvn_bot
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On macOS or Linux, activate the environment with `source venv/bin/activate` instead.

### 2. Configure the environment

Create a `.env` file in the repository root:

```dotenv
DISCORD_TOKEN=replace_with_your_bot_token
ENVIRONMENT=development
COMMAND_PREFIX="!tf "

DB_METHOD=mongodb
DB_USERNAME=tfvn_bot
DB_PASSWORD=replace_with_a_strong_password
DB_HOST=localhost:27017
DB_NAME=tfvn_bot

# Required by femboy-card and quote proofs
CONTENT_VERIFICATION_ACTIVE_KEY_ID=2026-08
CONTENT_VERIFICATION_KEYS_JSON={"2026-08":"replace_with_32_byte_base64url_key"}

# Optional feature controls
DISABLED_COGS=

# Used by cogs.general when that cog is enabled
INVITE_LINK=https://discord.com/oauth2/authorize?...
VERIFY_CHANNEL=123456789012345678
```

`db.py` constructs the connection as:

```text
DB_METHOD://DB_USERNAME:DB_PASSWORD@DB_HOST/?retryWrites=true&w=majority
```

Include the port in `DB_HOST`; the current connection builder does not read `DB_PORT`. Keep `.env` and `.env.prod` private—they are already ignored by Git.

Generate each content-verification key with:

```powershell
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('='))"
```

Use that output only as the JSON value; never reuse `DISCORD_TOKEN` or a database
password. New cards and quotes fail closed when the keyring is missing or invalid.
For rotation, add a new key ID, keep old key IDs in the JSON so existing proofs
remain verifiable, then change `CONTENT_VERIFICATION_ACTIVE_KEY_ID` to the new ID.
Use a different keyring for every bot deployment (for example development and
production); instances that share an HMAC key can validate and mint each other's
proofs.

Internally, proofs use a `tfv1.<key-id>.<claims>.<signature>` token. New cards and
quotes display only a 31-character `tfp1_<reference>` code; the signed token remains
hidden in MongoDB. `hash_verify` resolves that exact reference, authenticates the
hidden token, requires its signed ID to match the record ID, and then compares the
saved snapshot with the signed digest. Full `tfv1` tokens remain accepted for older
messages. A short reference is intentionally not a self-contained proof and needs
MongoDB, but a database-only writer still cannot mint, redirect, or alter a valid
result without the HMAC key. The signed claims bind the proof type, guild, issuer,
source IDs, issue times, salt, and digest of the complete private snapshot; quote
text stays out of the readable token. This is bot-verifiable HMAC—not an
independently verifiable public-key signature—and it authenticates recorded
member/role facts or quote text used by the bot rather than screenshot/PNG pixels,
avatars, attachments, or embeds.

Feature controls:

| Variable | Behavior |
| --- | --- |
| `DISABLED_COGS` | Comma-separated module patterns, such as `cogs.nsfw.*` |

### Role-gated Beta command decorator

Import `BetaFunction` and place it directly above a command callback:

```python
from discord.ext import commands

from cogs._beta_function import BetaFunction


@commands.command(name="new_preview")
@BetaFunction
async def new_preview(ctx: commands.Context) -> None:
    await ctx.send("This Beta function is enabled.")
```

The callback is loaded with the normal bot, but runs only when the member has at
least one role configured by `BETA_ROLE_IDS`. Set it as an `ARRAY` with
`!tf setting set_variable BETA_ROLE_IDS`, one role ID per line.
This setting is read only from MongoDB's `global_variables`; `.env` role values
are ignored. Denied checks receive a safe message instead of running the
callback. The shipped `!tf beta_preview` command can verify the configuration.

### 3. Select development cogs

Create `dev_cogs.txt` with a small startup profile:

```text
cogs.settings.variable_setting
cogs.general
cogs.help
cogs.operation.heartbeat
```

Add one dotted module path per line as you work. For example, `cogs.mod.*` loads every public module under `cogs/mod/`.
The profile supports blank lines, comments, explicit modules, and `.*`
wildcards.

Include `cogs.settings.variable_setting` when enabling features that use MongoDB
global variables, including `cogs.utils.highlight`. The loader moves the settings
cog to the front only when it is listed in the profile. Without it, highlights
cannot read `HIGHLIGHT_CHANNEL` and qualified messages remain pending; startup and
posting warnings in `bot.log` identify the missing configuration.

For Tiên Lộ development, also load `cogs.cultivation.cultivation` together with
the account cogs used to view and earn Trap Coin.

### 4. Run the bot

```powershell
python main.py
```

When startup succeeds, the console prints `Bot is ready!`. Runtime output is also appended to `bot.log`.

## Server-specific settings

Most server IDs and feature assets used by the feature cogs are stored in MongoDB rather than `.env` (`INVITE_LINK` and `VERIFY_CHANNEL` are notable environment-based settings). With `cogs.settings.variable_setting` loaded, an administrator can set a value interactively:

```text
!tf setting set_variable JOIN_CHANNEL
```

The bot then asks whether the value is a `STRING` or `ARRAY` and prompts for its contents. Restart the bot after adding settings needed by cogs that previously failed to initialize.

Common settings include:

| Feature | MongoDB global variables | Type |
| --- | --- | --- |
| Join and leave/kick/ban announcements | `JOIN_CHANNEL`, `RULE_CHANNEL`, `ROLE_CHANNEL`, `BYE_CHANNEL` | `STRING` |
| Birthday announcements | `BIRTHDAY_CHANNEL` | `STRING` |
| Chat highlights | `HIGHLIGHT_CHANNEL` | `STRING` |
| Word games | `WORD_CONNECT_GAMES_CHANNELS`, `VIETNAMESE_KING_GAMES_CHANNELS` | `ARRAY` |
| Verification | `FALLEN_FEMBOY_ROLE_ID` | `STRING` |
| Booster placement | `BOOSTER_CUSTOM_ROLE_ANCHOR_ID`, `BOOSTER_CUSTOM_VOICE_CATEGORY_ID` | `STRING` |
| Area 51 guard | `AREA_51_CHANNEL_ID`, `AREA_51_PRUNE_HOURS` | `STRING` |
| Beta command access | `BETA_ROLE_IDS` | `ARRAY` |
| NSFW role controls | `KING_ROLE_ID`, `QUEEN_ROLE_ID` | `STRING` |
| NSFW interaction media | `BLOWJOB_GIFS`, `HANDJOB_GIFS`, `FOOTJOB_GIFS`, `ASSJOB_GIFS`, `THIGHJOB_GIFS`, `SPANK_GIFS`, `RIMJOB_GIFS`, `FROTTING_GIFS`, `FUCKING_GIFS`, `CREAMPIE_GIFS`, `THREESOME_GIFS`, `ORGY_GIFS` | `ARRAY` |

The booster role anchor is optional; without it, Discord keeps the custom role at its default position. `BOOSTER_CUSTOM_VOICE_CATEGORY_ID` is required for custom rooms so their private category placement and permission overwrites are deterministic. The word-chain move icons also have built-in emoji defaults.

Voluntary-leave, kick, and ban announcements all use `BYE_CHANNEL`. Give the bot
View Audit Log permission so it can reliably identify kicks; without that permission,
an unverified removal falls back to a generic departure announcement.

The shop and moderation cases keep guild-specific configuration in their own
MongoDB collections. Configure them with their admin
commands rather than `setting set_variable`:

| System | Initial configuration |
| --- | --- |
| Moderation cases | `!tf case log_channel #mod-log` |
| Shop | Add a role or badge item; no separate setup command is required |

The role exam uses the repository file `data/role_exam.json` instead of MongoDB.
Use JSON `null` for `role_id` to leave the reward unconfigured. After replacing
all 20 placeholder questions and answers, set it to the reward role's decimal ID
as a JSON string (for example, `"123456789012345678"`). Set `required_percent` to an integer from
1 through 100. The reward role must not grant privileged permissions and must be
below both the invoking staff member and the bot in the Discord role hierarchy.
Restart the bot or reload the role-exam cog after every file change.

### Optional booru API configuration

The `r34` and `gbr` cogs read their credentials from `.env`:

| Integration | Variables |
| --- | --- |
| Rule34 | `RULE34_API_URL`, `RULE34_API_KEY`, `RULE34_USER_ID`, `SECOND_RULE34_API_KEY`, `RULE34_SECOND_USER_ID` |
| Gelbooru | `GELBOORU_API_URL`, `GELBOORU_API_KEY`, `GELBOORU_USER_ID`, `SECOND_GELBOORU_API_KEY`, `GELBOORU_SECOND_USER_ID` |

The current implementation alternates between its primary and secondary credential pairs, so configure both pairs for reliable requests.

## Command overview

Commands are invoked with `COMMAND_PREFIX`. With the default prefix, `!tf help [topic]`
opens the bot's full command catalog split into overview, community, economy,
Tiên Lộ, games, fun/social, utilities/booster, automatic features, and moderation topics.
The catalog remains complete in partial development profiles; individual commands
still depend on loaded cogs, configuration, and Discord permissions. Only the NSFW
topic is hidden outside NSFW-marked channels. `!tf mod` and `!tf nsfw` open the same
menu focused on their respective topics.

| Area | Representative commands |
| --- | --- |
| General | `help [topic]`, `hello`, `invite`, `verify`, `role_exam @user`, `self_unverified`, `ping`, `server_stats` |
| Community | `afk`, `jobremind add`, `bedtime`, `bedtime add @member <bedtime_HH:MM> <wake_HH:MM> #channel`, `birthday`, `vote`, `giveaway` |
| Economy and games | `daily`, `user_balance`, `user_transactions`, `shop`, `blackjack`, `poker`, `crocodile challenge`, `slot`, `flip_coin`, `sicbo_start`, `noitu`, `vtv` |
| Tiên Lộ | `tutien`, `tutien thucong`, `tutien dotpha`, `tutien bicanh`, `tutien thiluyen`, `tutien doido` |
| Moderation | `kick`, `ban`, `unban`, `softban`, `mute`, `timeout`, `warn`, `case`, `purge`, `slowmode`, `verified` |
| Operations | `ping`, `server_stats`, `operation_dashboard`, `bot_status`, `setup check` |
| Utilities | `quote`, `hash_verify`, `big_speaker`, `random_member` |
| Booster tools | `custom_role`, `update_custom_role`, `custom_room` |
| Social and fun | `kiss`, `hug`, `pat`, `avatar`, `quote`, `rank`, `ship`, `aura`, `redflag`, configurable `triggerreply`, and other meter commands |
| Automatic features | Welcome and leave/kick/ban announcements, AFK monitoring, job and bedtime reminders, bedtime chat replies, content filtering, scheduled cleanup, and persistent interaction handling |
| Optional NSFW | `nsfw`, `r34`, `gbr`, NSFW interactions, rankings, and role-based locks |

This table is only an overview. The in-Discord dropdown and [FUNCTIONS.md](FUNCTIONS.md)
provide the complete user-facing catalog; each module under `cogs/` remains the
implementation source of truth.

Moderation commands retain their direct argument forms alongside the UI:
`!tf purge 5` immediately deletes the five latest messages before the command,
while `!tf purge` opens the count form and confirmation. Other complete commands,
such as `!tf timeout @member 10` and `!tf nickchange @member New Name`, also run
directly. Omitting a required value or using an argument-free member reply keeps
the guided workflow. Both modes apply the same permission and validation checks.

## Community systems

### Bedtime reminders

Administrators can assign one recurring bedtime to each member in a guild.
`!tf bedtime` opens an interactive panel (member select, channel select, time
modal, and paginated list). Prefix subcommands remain available:

```text
!tf bedtime
!tf bedtime add @member 23:00 07:00 #general
!tf bedtime list
!tf bedtime remove @member
!tf bedtime remove 123456789012345678
```

Times use fixed Vietnam time (UTC+7), accepting `H:MM` or `HH:MM`. At bedtime the
bot mentions the member once in the configured announcement channel. Until the
following wake time, every message that member sends anywhere in the guild gets a
mentioning bedtime reply in the same channel; there is no cooldown. Schedules
survive restarts in the guild-scoped `bedtime_reminders` MongoDB collection.
If a member leaves, administrators can remove the retained entry by the user ID
shown by `bedtime list`.

### Tiên Lộ cultivation game

Start a persistent profile and open its private dashboard with:

```text
!tf tutien batdau
!tf tutien
```

Tiên Lộ calculates Bế Quan rewards from timestamps, so AFK progress survives bot
restarts without a scheduler. Players choose Cân Bằng, Tĩnh Tu, or Khai Khoáng;
advance from Phàm Nhân through Kim Đan; select Kiếm Tu, Thể Tu, or Đan Tu; allocate
talents; and improve a four-slot equipment set through a deterministic market,
crafting, expeditions, and the 30-floor Tháp Thí Luyện.

Use `!tf tutien thucong` to collect AFK resources, `!tf tutien dotpha` to advance,
and `!tf tutien bicanh start <linhduoc|cokhoang|yeuthuson> <2|4|8>` for an
expedition. Only one Bế Quan/Bí Cảnh session can run at once. Major breakthroughs
use visible soft pity and never destroy Tu Vi or equipment on failure.

Trap Coin exchange is deliberately limited and uses a spread: members may spend
up to 50 TC per week at 1 TC = 10 Linh Thạch, or receive at most 20 TC per week at
20 Linh Thạch = 1 TC. Limits reset Monday at 00:00 Asia/Ho_Chi_Minh. PvP, player
trading, Tông Môn, theft, and Booster/paid power are not part of this release.

At startup, the cultivation cog ensures `user_accounts.user_id` is unique. If
legacy duplicate account documents prevent that index, the cog logs the duplicate
IDs and stays disabled; it does not guess how to merge Trap Coin balances.

See [CULTIVATE_GAME_PLAN.md](CULTIVATE_GAME_PLAN.md) for the complete design and
[FUNCTIONS.md](FUNCTIONS.md) for every command.

### Crocodile Dentist

Create a guild challenge for one to four other members, optionally choosing
between 2 and 25 teeth (13 by default):

```text
!tf crocodile challenge @user1 @user2
!tf crocodile challenge 20 @user1 @user2 @user3
```

The host is confirmed automatically, and the challenge waits for every invitee
to accept or decline for up to five minutes. Declined and unanswered members are
removed, and the game starts only when at least one invitee accepts. Players then
press one unselected tooth per turn in host-first order. Safe teeth advance the
turn, while the one hidden dangerous tooth immediately makes that player lose and
everyone else win. Members may participate in multiple open games.

Pending and active state is authoritative in MongoDB's `crocodile_games`
collection, including confirmations, turns, selected teeth, and the hidden tooth,
so play can continue after a bot restart. Use `!tf crocodile` to list your newest
10 open games in the current server and `!tf crocodile fire <game_id>` as the host
to recreate the current panel; it supersedes the old panel without changing game
state or deadlines. Active games cancel after seven days without a valid tooth
press; firing a panel does not extend that deadline.

### Trap Coin shop

Administrators can add permanent role or badge ownership to the guild catalog:

```text
!tf shop add_role pink 100 @Pink A cosmetic pink role
!tf shop add_badge helper 250 Community Helper
```

Members use `shop`, `shop buy <item_id>`, `shop inventory`, and
`shop use <item_id>`. Purchases deduct balances atomically, reject duplicate
ownership, and write to `transaction_logs`. A badge remains owned when it is
unequipped.

### Moderation cases

Successful ban, unban, kick, warn, timeout, mute, and soft-ban actions create a
guild-scoped numbered case. Moderators can use:

```text
!tf case view 12
!tf case history @member
!tf case edit 12 Updated reason
!tf case status 12 resolved
```

Reason and status edits retain an edit history. Status can be `open`,
`resolved`, `appealed`, or `void`.

Every state-changing moderation command except the intentionally unchanged
`verified` / `unverified` staff workflow now ends with an explicit Yes/No guard.
Members can drop their own verified/NSFW-access role with `self_unverified`;
that command requires Yes/No confirmation, and cancel or timeout means they
must run it again. After they confirm, they have to ask staff (`verified`) to
get the role back.
Single-member actions can target a mention or the author of a same-channel reply;
reply mode is argument-free so malformed input cannot silently change the target.
Discord and database mutations happen only after the moderator completes the
action-specific form and confirms, at which point permissions, hierarchy, target
state, and selected resources are checked again. Message cleanup is anchored
before the invocation message so later UI/chat traffic does not change its scope.

The unban UI can optionally create a unique one-use invite valid for seven days.
The bot tries to DM it to the unbanned user and otherwise shows it privately to
the moderator for manual delivery. Reinviting is best-effort after the unban and
case are complete, so an invite-service failure never repeats the moderation action.

Run `!tf setup check` after configuration to inspect required environment settings,
enabled features, MongoDB connectivity, channel/role IDs, bot permissions, and role
hierarchy. It uses the same read-only diagnostics as the dashboard's Doctor panel.

Administrators can run `!tf operation_dashboard` for an interactive health dashboard without
changing the existing `!tf server_stats` report. The dashboard can browse recognized
guild command outcomes, export retained records as CSV, and prune old records after
confirmation. These records are guild-scoped in MongoDB's `operation_logs` collection;
direct messages and unknown commands are not retained.

Click **🩺 Doctor** for a private Vietnamese report of current configuration and
permission problems, with suggested fixes. The report shows error/warning totals,
scan time, and five findings per page; Previous/Next and Refresh controls let the
opening administrator inspect and rerun the scan. Access is checked on every click
and the panel expires after 180 seconds. Doctor works without loading `setup_check`.
Checks apply to enabled features in the current server, including selected
extensions that failed to load; disabled features and known other-server targets
are skipped. Channel permission overrides are checked separately from server
permissions, and role hierarchy is checked only for roles the bot manages.
Missing optional settings do not produce warnings. The scan uses current process
environment and runtime configuration, flags settings that need a cog reload,
and does not reload `.env`, change settings, or store reports. Secrets and raw
exception messages are never included in diagnostics. MongoDB checks run in a
worker thread with a five-second deadline, so a database failure still leaves
other findings available.

If the invoking Administrator is also the Bot owner, `operation_dashboard` adds private
panels for the bot's joined servers and lifecycle history. The server manager can
inspect every connected guild and confirm leaving a selected guild, but it cannot
leave the guild where the dashboard was opened. This restriction applies only to
the manager; the standalone `!tf leave` command is unchanged. The lifecycle panel
shows the latest 10 `initial_ready`, `reidentified`, and `resumed` events for the
current environment. Lifecycle events are append-only, retained indefinitely in
the global `bot_lifecycle_events` collection, and excluded from guild audit
browsing, CSV export, and pruning.

Administrators can open the bot-status setup panel with `!tf bot_status`. Choose
an activity type from the dropdown, then enter its text and duration in the form
(the duration starts at `1h`). The panel shows the current activity, rotation mode,
and temporary-status expiration. **Đổi ngẫu nhiên** ends the override immediately;
**Làm mới** updates the display; **Đóng** closes the controls.

Only the Administrator who opened a panel can use it, and every action rechecks
their current Administrator permission in that server. Controls expire after
three minutes; run the command again for a fresh panel. Closing or expiring the
panel does not cancel a temporary status. Prefix shortcuts remain available:

```text
!tf bot_status
!tf bot_status set PLAYING 2h Fortnite
!tf bot_status show
!tf bot_status reset
```

`bot_status show` sends a text summary and usage. Supported types are `PLAYING`,
`WATCHING`, `LISTENING`, `STREAMING`, `COMPETING`, and `CUSTOM`; `STREAMING` uses
the existing fixed stream URL. Durations are positive integers followed by `m`,
`h`, or `d`, from one minute through 24 hours (`30m`, `2h`, `1d`). Text must be
one nonempty line of up to 128 characters. The activity applies to the entire bot;
an Administrator in any joined server can replace or reset it. Setting and
resetting through the panel or prefix commands share a 10-second cooldown across
the bot. Random rotation pauses until
the timer ends or an Administrator resets it, then changes immediately and resumes
its usual 5–15 minute intervals. Overrides live only in memory and end on restart
or reload of `cogs.operation.bot_status`; no database configuration is needed.

## Docker

The Compose service builds the bot, reads `.env`, and runs it with a restart policy:

```powershell
docker compose up --build -d
docker compose logs -f bot
```

Stop it with:

```powershell
docker compose down
```

SIGINT and SIGTERM trigger graceful command draining. The bot immediately stops
admitting new prefix commands, tells later callers that shutdown is in progress,
waits for commands already running, and then closes Discord and flushes its
lifecycle recorder. Sending a second shutdown signal forces closure. Compose's
five-minute stop grace period gives active commands time to finish before Docker
terminates the container.

The Compose file does **not** start MongoDB, so `DB_HOST` must point to an existing deployment reachable from the container. The bot opens no inbound port; it connects outbound to Discord, MongoDB, and any enabled external APIs.

The GitHub Actions workflow also builds and publishes container images to GitHub Container Registry for configured branches and releases.

## Tests

Run the unit-test suite from the repository root:

```powershell
python -m unittest discover -s test -p "test_*.py"
```

The automated tests cover cultivation calculations and state transitions, card-game
rules and wagers, persistent Crocodile Dentist and bedtime-reminder behavior, the
categorized help menu, meter formatting, quote-card rendering, cog flags, and
validation helpers used by the shop, cases, and setup diagnostics.

## Project structure

```text
tfvn_bot/
├── main.py                 # Bot startup, intents, data loading, and cog discovery
├── db.py                   # MongoDB client and database selection
├── dataloader.py           # JSON, text, line, and CSV data helpers
├── CULTIVATE_GAME_PLAN.md  # Tiên Lộ design and acceptance specification
├── cogs/                   # Discord commands, listeners, tasks, and UI views
│   ├── _beta_function.py   # Database-role guard for experimental commands
│   ├── _feature_flags.py   # Feature and cog disable flag parsing
│   ├── _hash_verification.py # Signed content-proof issuance and validation
│   ├── bedtime_remind/     # Persistent UTC+7 bedtime schedules, admin UI, and chat reminders
│   ├── settings/           # Mongo-backed runtime variables
│   ├── economy/            # Trap Coin shop, inventory, badges, and role items
│   ├── cultivation/        # Tiên Lộ progression, AFK calculations, PvE, and economy
│   ├── mod/                # Moderation and verification
│   ├── operation/          # Health/audit UI, owner controls, and lifecycle events
│   ├── booster/            # Booster custom roles/rooms and cleanup
│   ├── minigames/          # Economy/card, persistent multiplayer, and Vietnamese word games
│   ├── funny_things/       # Fun meters, cards, and birthday features
│   ├── interaction/        # Social and optional NSFW interactions
│   └── ...
├── data/                   # Word lists, filters, and game datasets
├── assets/                 # GIF and media constants
├── fonts/                  # Bundled quote-card fonts, licenses, and source notes
├── scripts/                # One-off data preparation/migration utilities
├── test/                   # Unit tests and development utilities
├── Dockerfile
└── docker-compose.yml
```

To add a cog, create a module containing an asynchronous `setup(bot)` function
and call `await bot.add_cog(...)`. Production discovers it automatically;
development uses its local cog profile. Prefix helper filenames with `_` when
they should not be loaded as extensions.
