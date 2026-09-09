# TFVN Bot — Function Catalog

This document lists every user-facing command and background feature the bot currently implements. Command availability depends on which cogs are loaded (`ENVIRONMENT=production` loads all public modules under `cogs/`; development uses `dev_cogs.txt`) and on `DISABLED_COGS`.

**Default prefix:** `!tf ` (space after the token; configurable via `COMMAND_PREFIX`).

Examples below use that default. Replace with your configured prefix if different.

**Access notes:**

| Label | Meaning |
| --- | --- |
| Everyone | Any member who can use bot commands in the channel |
| Booster | Server booster (has premium subscription status) |
| Moderator | Requires the listed Discord permission(s) |
| Administrator | Requires Administrator (and any listed extra permission) |
| Bot owner | Discord application owner recognized by the bot |
| NSFW channel | Must be used in a Discord NSFW-marked channel |
| Beta | Requires a role listed in Mongo `BETA_ROLE_IDS` |
| Channel-bound | Only works in a configured game/feature channel |

---

## Help & discovery

| Command | Access | Description |
| --- | --- | --- |
| `help [topic]` | Everyone | Replies to the invoking message with the full bot catalog, covering commands and automatic features even in partial development profiles. Individual command availability still depends on loaded cogs/configuration; only the NSFW topic is channel-gated |
| `mod` | Everyone | Opens the same menu focused on moderation; each listed command enforces its own Discord permission |
| `nsfw` | NSFW channel | Opens the same help menu focused on the NSFW topic; outside NSFW channels the bot warns and deletes the prompt |

---

## General

| Command | Access | Description |
| --- | --- | --- |
| `hello` | Everyone | Greets the invoking user |
| `invite` | Everyone | Replies with the bot invite link (`INVITE_LINK` env) |
| `verify` | Everyone | Points the user to the verification channel (`VERIFY_CHANNEL` env) |
| `role_exam @user` | Manage Roles | Invite the target to a private 20-question exam loaded from `data/role_exam.json`. Meeting the configured percentage grants its configured non-privileged role; failure or timeout requires staff to start a new attempt |
| `self_unverified` | Everyone (guild) | Confirm Yes/No to drop your own verified/NSFW-access role. Cancel or timeout requires running the command again. After confirmation, staff must grant the role again |
| `ping` | Everyone | Heartbeat / liveness reply |
| `beta_preview` | Beta | Confirms the member has a configured Beta role |
| `server_stats` | Administrator | In-memory uptime, command, and error counts since process start; 10s per-guild cooldown |
| `operation_dashboard` | Administrator | Opens the bot/server health dashboard with refresh, private Doctor diagnostics, guild command audit, CSV export, and guarded log-pruning controls; the joined-server manager and lifecycle history are private Bot owner controls |
| `bot_status` | Administrator (guild) | Opens the activity setup panel with a type dropdown, text/duration form, random reset, refresh, and close controls |
| `bot_status set <type> <duration> <text>` | Administrator (guild) | Replaces the bot-wide activity temporarily, pausing random rotation |
| `bot_status show` | Administrator (guild) | Shows the current activity and override expiration |
| `bot_status reset` | Administrator (guild) | Ends the temporary override and immediately resumes random activity rotation |
| `leave` | Administrator | Makes the bot leave the current guild |
| `setup` / `diagnose` | Manage Guild (subcommands) | Setup diagnostics group |
| `setup check` | Manage Guild | Uses shared Doctor diagnostics to check environment/configuration, database, permissions, IDs, and enabled-feature health |

**Module:** `cogs.general`, `cogs.onboarding.role_exam`, `cogs.operation.*`

`bot_status` shows the current activity, rotation mode, and override expiration.
Select an activity type to open a form for its text and duration (default `1h`).
**Đổi ngẫu nhiên** immediately ends the override and selects a random activity;
**Làm mới** refreshes the display; **Đóng** disables the panel. Only its opening
Administrator can use a panel, with current guild and Administrator permission
checked on every action and form submission. Controls expire after three minutes;
open a fresh panel by running `bot_status` again. Panel close or timeout does not
cancel the bot's temporary status.

The prefix shortcut `bot_status set PLAYING 2h Fortnite` displays a temporary
activity for two hours; `bot_status show` sends a text summary and usage.
Supported types are `PLAYING`, `WATCHING`, `LISTENING`, `STREAMING`, `COMPETING`,
and `CUSTOM`; streaming retains the existing fixed stream URL. Duration is a
positive integer followed by `m`, `h`, or `d`, from `1m` through `24h` (`1d`).
Text must be a single nonempty line of 1–128 characters. Any joined server's
Administrator can replace or reset the same global override; panel submissions,
random-reset buttons, `set`, and `reset` share a 10-second bot-wide cooldown.
Expiration or reset immediately selects a
random activity and resumes the usual random 5–15 minute rotation. Overrides
are held only in memory and also end on bot restart or status-cog reload.

`operation_dashboard` shows readiness, environment, uptime, Discord latency, guild/member/channel totals, MongoDB health, retained log count, and recent command outcomes. Audit browsing and CSV/prune responses are private to the administrator. Audit and export ranges are 7, 30, or 90 days, or all retained records; exports above 100,000 rows must use a narrower range. Pruning can remove records older than 30, 90, or 180 days, or clear the guild's retained history after an additional confirmation. Recognized guild prefix commands and dashboard export/prune actions are stored in the guild-scoped `operation_logs` collection; direct messages and unknown commands are not logged.

The **🩺 Doctor** button opens a private, read-only Vietnamese report for the
clicking administrator, with error/warning totals, scan time, suggested fixes,
and five findings per page. Previous, Next, and Refresh controls remain available
for 180 seconds and recheck the opening user, server, and current Administrator
permission. Healthy scans say explicitly that no problems were found. Doctor
checks enabled features in the current server, including selected extensions
that failed to load, while skipping disabled features and known other-server
targets. It checks required environment and feature settings, channel permission
overrides, guild permissions, hierarchy for roles the bot manages, runtime intents, cached
settings needing reload, and MongoDB availability. Reports contain no secret
values or raw exceptions, suppress mentions, and are not saved. The same collector
backs `setup check`; Doctor does not require that command's cog to be loaded.

When the Administrator is also the Bot owner, the dashboard adds two private controls. The joined-server manager lists every guild currently connected to the bot and can leave a selected guild only after confirmation; it cannot leave the guild where `operation_dashboard` was invoked. The standalone `!tf leave` command remains unchanged and still leaves its current guild immediately. The lifecycle panel shows the 10 newest `initial_ready`, `reidentified`, or `resumed` events for the current environment. These append-only global events are retained indefinitely in `bot_lifecycle_events`; they are separate from `operation_logs` and are never included in guild audit browsing, CSV exports, or pruning.

On SIGINT or SIGTERM, the process enters graceful drain mode. Commands admitted before the signal finish normally; later prefix commands receive a shutdown notice and do not execute. Discord closes after the active command set is empty. A second signal forces immediate closure.

---

## Triggered replies

Guild administrators can configure persistent, case-insensitive automatic replies. `contains` matches a phrase anywhere in a message; `exact` requires the entire normalized message to match. Replies cannot generate mentions.

Rules are stored in plaintext and shown to administrators by `triggerreply list`; an `exact` phrase can behave like a secret code but should not be used as an authentication secret.

| Command | Access | Description |
| --- | --- | --- |
| `triggerreply add contains <phrase> \| <reply>` | Administrator | Reply when a message includes the phrase |
| `triggerreply add exact <phrase> \| <reply>` | Administrator | Reply only when the whole message matches |
| `triggerreply update <ID> <contains\|exact> <phrase> \| <reply>` | Administrator | Replace a rule while preserving its ID |
| `triggerreply list` | Administrator | List configured rules and their numeric IDs |
| `triggerreply remove <ID>` | Administrator | Delete a configured rule |

Examples:

```text
!tf triggerreply add contains dit me vnpt | vnpt nhu con cac
!tf triggerreply add exact [A SECRET CODE] | something
!tf triggerreply update 2 exact [NEW SECRET CODE] | new response
```

**Module:** `cogs.interaction.triggered_reply`

---

## AFK reminders

| Command | Access | Description |
| --- | --- | --- |
| `afk` | Everyone | Shows AFK subcommand guide |
| `afk dynamic [reason]` | Everyone | AFK until the user posts a message again |
| `afk time` | Everyone | Interactive timed AFK setup |
| `afk clear` | Everyone | Clear active AFK early |
| `afk check` | Everyone | List unread pings received while AFK |

**Background (`afk_monitor`):** detects mentions of AFK members, stores pings, and clears dynamic AFK when the user returns.

**Module:** `cogs.afk_remind.*`

---

## Announcements (automatic)

No user commands. Event listeners only:

| Event | Behavior |
| --- | --- |
| Member join | Welcome embed in `JOIN_CHANNEL` (rules / role channel pointers) |
| Member leaves voluntarily | Leave announcement in `BYE_CHANNEL` |
| Member kick | Kick announcement in `BYE_CHANNEL` (requires View Audit Log for reliable detection) |
| Member ban | Ban announcement in `BYE_CHANNEL` |

**Module:** `cogs.announcement.*`

---

## Content of the day

| Command | Access | Description |
| --- | --- | --- |
| `random_femboy` | Everyone | Random image from the `images` collection (`image_collection: femboy`) with optional social metadata |

**Module:** `cogs.cotd.random_femboy`

---

## Economy — Trap Coins

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `daily` | — | Everyone | Once per UTC day; grants **10** Trap Coins |
| `user_balance` | `balance` | Everyone | Show current Trap Coin balance |
| `user_transactions` | `transactions` | Everyone | Last 10 transaction log entries |
| `add_tc @member <amount> [reason]` | `give_tc`, `grant_tc` | Administrator | Credit a non-bot member 1–1,000,000,000 Trap Coins; logs `admin_add_tc` |
| `remove_tc @member <amount> [reason]` | `sub_tc`, `subtract_tc`, `take_tc` | Administrator | Debit a non-bot member 1–1,000,000,000 Trap Coins if the balance is sufficient; logs `admin_remove_tc` |
| `set_tc @member <amount> [reason]` | `set_balance` | Administrator | Set a non-bot member’s balance to 0–1,000,000,000; logs delta as `admin_set_tc` |
| `check_tc [@member]` | `tc_balance` | Administrator | Inspect a non-bot member’s Trap Coin balance (default: author) |

### Shop

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `shop` | `store` | Everyone | List enabled catalog items |
| `shop buy <item_id>` | — | Everyone | Purchase a catalog item; 2 calls per 5 seconds per user |
| `shop inventory [@member]` | `inv` | Everyone | View owned shop items |
| `shop use <item_id>` | — | Everyone | Equip badge or apply purchased role |
| `shop unequip` | — | Everyone | Clear active badge |
| `shop add_role <id> <price> @role [description]` | — | Manage Guild | Add/update a sellable role priced 1–1,000,000,000 TC |
| `shop add_badge <id> <price> <display name>` | — | Manage Guild | Add/update a badge item priced 1–1,000,000,000 TC |
| `shop remove <item_id>` | `disable` | Manage Guild | Hide an item from the shop |

Shop item IDs are 1–32 lowercase letters, digits, `_`, or `-`, and must start with a letter or digit.

**Module:** `cogs.daily_reward.*`, `cogs.economy.shop`

---

## Tiên Lộ — Tu Tiên AFK

Tiên Lộ is a global, persistent cultivation game. Rewards are calculated from
timestamps when they are collected, so Bế Quan continues through bot restarts
without a background reward task. A player may run either Bế Quan or Bí Cảnh,
never both at the same time.

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `tutien` | `cultivate` | Everyone | Open the owner-only Tiên Lộ dashboard |
| `tutien batdau` | — | Everyone | Create a cultivation profile and begin Bế Quan |
| `tutien thucong` | — | Everyone | Collect at least 10 minutes of AFK rewards, then automatically resume the selected focus |
| `tutien huong <canbang\|tinhtu\|khaikhoang>` | — | Everyone | Select balanced, Tu Vi-focused, or Linh Thạch-focused cultivation |
| `tutien dotpha` | — | Everyone | Attempt the next breakthrough when resource and tower requirements are met |
| `tutien phai [kiem\|the\|dan]` | — | Everyone | View the current class or choose Kiếm Tu, Thể Tu, or Đan Tu at Luyện Khí 1 |
| `tutien phai reset` | — | Everyone | Clear the class and refund all talent points for a realm-scaled fee; seven-day cooldown |
| `tutien thienphu` | — | Everyone | View talent IDs, effects, ranks, and unallocated points |
| `tutien thienphu tang <talent_id> [points]` | — | Everyone | Allocate one or more points to a talent belonging to the selected class |
| `tutien dongphu` | — | Everyone | View cave level, bonuses, capacity, and the next upgrade price |
| `tutien dongphu nangcap` | — | Everyone | Buy the next cave level when enough Linh Thạch is available |
| `tutien choden` | — | Everyone | View permanent stock and four deterministic offers for the current ICT date |
| `tutien mua <item_id>` | — | Everyone | Buy one market item with Linh Thạch |
| `tutien kho` | — | Everyone | View materials and owned equipment |
| `tutien trangbi <item_id>` | — | Everyone | Equip an owned item in its fixed slot |
| `tutien phanra <item_id>` | — | Everyone | Salvage one equipment item into crafting fragments |
| `tutien luyen [recipe_id]` | — | Everyone | View fixed recipes or craft one guaranteed item |
| `tutien thiluyen [tang]` | — | Everyone | Challenge the next uncleared floor of the 30-floor tower |
| `tutien bicanh` | — | Everyone | Show the expedition guide and current status |
| `tutien bicanh start <linhduoc\|cokhoang\|yeuthuson> <2\|4\|8>` | — | Everyone | Begin a timed expedition in the selected zone |
| `tutien bicanh claim` | — | Everyone | Collect a finished expedition |
| `tutien bicanh cancel` | — | Everyone | Cancel an active expedition without rewards and resume Bế Quan |
| `tutien doido` | — | Everyone | Show exchange rates and remaining weekly limits |
| `tutien doido mua <amount_tc>` | — | Everyone | Spend up to 50 TC/week at 1 TC = 10 Linh Thạch |
| `tutien doido ban <so_linh_thach>` | — | Everyone | Sell Linh Thạch at 20 per TC, receiving at most 20 TC/week |
| `tutien profile [@member]` | — | Everyone | View your profile or another member's public global profile |
| `tutien top` | — | Everyone | Rank public profiles among visible members of the current guild |
| `tutien riengtu [public\|private]` | — | Everyone | View or explicitly set profile visibility |

### Progression rules

- Available realms are Phàm Nhân, Luyện Khí 1–9, four Trúc Cơ stages, and four
  Kim Đan stages. Excess Tu Vi remains after a successful breakthrough.
- Base AFK storage is 24 hours. Each purchased Động Phủ level adds four hours and
  5% production; level seven reaches the cave's maximum upgrade bonus.
- Cân Bằng earns 100% Tu Vi / 100% Linh Thạch, Tĩnh Tu earns 125% / 60%, and
  Khai Khoáng earns 75% / 150%.
- Minor breakthroughs are guaranteed. Major breakthroughs begin at 70%, gain
  ten percentage points of pity after each failure, and succeed on the fourth
  attempt at the latest. Failure preserves Tu Vi, equipment, and realm progress,
  charges a base 25% of the Linh Thạch cost (reducible to 10% through Thể Tu's
  Hộ Mạch talent), and starts a one-hour retry cooldown.
- Each class has three five-rank talents. Talent/class resets refund allocated
  points, cost `1,000 × current realm index` Linh Thạch, and have a seven-day
  cooldown.
- Equipment has four fixed slots and visible deterministic stats. The shop has no
  paid reroll; crafting is guaranteed; boss equipment is guaranteed on the tenth
  eligible clear through a visible pity counter.
- Weekly exchange limits reset Monday at 00:00 `Asia/Ho_Chi_Minh`. The unequal
  buy/sell rates prevent exchange arbitrage.

The dashboard and its components are restricted to the invoking member. Replies
mention only that member; an unauthorized component click receives an ephemeral
denial. Profiles are global, but private profiles are absent from guild
leaderboards.

**Persistence:** versioned `user_accounts.cultivation` state, append-only
`cultivation_events`, and TC exchange records in `transaction_logs`. Trap Coin
remains in `user_accounts.balance` so an exchange updates both currencies in one
account write.

**Module:** `cogs.cultivation.cultivation`,
`cogs.cultivation._cultivation_helpers`

---

## Minigames

| Command | Access | Description |
| --- | --- | --- |
| `blackjack [n]` | Everyone | Solo Blackjack against the dealer; wagers `n` TC (default 5, range 5–1,000,000), wins pay 1:1, natural profit pays 3:2 rounded down to whole TC, and pushes/timeouts return the stake |
| `poker [n]` | Everyone | Solo five-card draw against the dealer; wagers `n` TC (default 5, range 5–1,000,000), allows one draw of up to three cards, wins pay 1:1, and ties/timeouts return the stake |
| `slot` | Everyone | Slot machine; costs **5** Trap Coins; logs debit transaction |
| `flip_coin <head\|tail> <n>` | Everyone | Coin flip bet of `n` Trap Coins (needs ≥5 TC to play); win pays 2× stake |
| `sicbo_start` | Everyone | Reaction-based Sic Bo round (Big / Small / Triple); payout wiring is incomplete |
| `crocodile` | Everyone (guild only) | Show the caller's newest 10 pending or active Crocodile Dentist games in the current server |
| `crocodile challenge [teeth] @user1 [@user2 @user3 @user4]` | Everyone (guild only) | Create a 2–5 player challenge; `teeth` must precede the mentions, defaults to 13, and accepts 2–25 |
| `crocodile fire <game_id>` | Host (guild only) | Recreate the authoritative invitation or gameplay panel for an open game in the current channel without resetting its state or deadlines |
| `noitu` | Channel-bound | Word-chain rules embed |
| `noitu status` | Channel-bound | Current word and used-word list |
| `noitu hint` | Channel-bound | Hint for the chain |
| `noitu end` | Channel-bound | End the current game |
| `noitu analyze` | Channel-bound | Analyze connectivity of the current word |
| `vtv` | Channel-bound | Vua Tiếng Việt rules + current scramble |
| `vtv status` | Channel-bound | Current puzzle status |
| `vtv next` | Channel-bound | Start a new letter-scramble round |
| `vtv hint` | Channel-bound | Reveal a letter hint |

**Background:** Crocodile Dentist stores pending and active games in
`crocodile_games`, so invitation responses, turns, pressed teeth, and the hidden
dangerous tooth survive restarts. Invitees have five minutes to respond; declined
or unanswered invitees are removed, and the game starts only if at least one
invitee accepts. One hidden dangerous tooth ends the game immediately; otherwise
turns cycle in host-first order. Active games cancel after seven days without a
valid tooth press. `noitu` and `vtv` also accept plain messages in their configured
channels for gameplay.

**Module:** `cogs.minigames.*`; the interactive card-game cogs are
`cogs.minigames.blackjack.blackjack` and `cogs.minigames.poker.poker`; persistent
Crocodile Dentist lives in `cogs.minigames.crocodile_dentist.crocodile`.

---

## Fun meters & cards

Most meters accept an optional `@member` (default: author). Scores are deterministic/daily-style fun output with staged loading where implemented.

| Command | Aliases | Description |
| --- | --- | --- |
| `gay` | — | Gay meter |
| `les` | `les_meter`, `lesbian` | Lesbian meter |
| `ship @user1 @user2` | — | Compatibility meter (two members required) |
| `penisize` | `peni`, `peni_size`, `ppsize` | Size meter |
| `titansize` | — | Fictional centimeter-size and cup meter |
| `aura` | — | Signed aura score (−/+) |
| `redflag` | `flags` | Red/green flag score (−10 green → +10 red) |
| `based` | — | Based meter |
| `brainrot` | — | Brainrot meter |
| `clown` | — | Clown meter |
| `cope` | — | Cope / seethe / mald meter |
| `cringe` | — | Cringe meter |
| `delulu` | — | Delulu meter |
| `gyatt` | — | Gyatt meter |
| `ick` | — | Ick meter |
| `mainchar` | — | Main-character energy |
| `npc` | — | NPC meter |
| `ohio` | — | Ohio meter |
| `rizz` | — | Rizz meter |
| `simp` | — | Simp meter |
| `skillissue` | — | Skill-issue meter |
| `touchgrass` | — | Touch-grass meter |
| `yapper` | — | Yap meter |
| `femboycard` | — | Personal femboy card from configured role names (`data/femboy_role.txt`), issued with a signed TFVN proof that binds the member, role, guild, issuer, time, and saved snapshot; 10s per-user cooldown |
| `birthday` | — | Open the interactive month and day picker |
| `birthday set <day> <month>` | — | Register a birthday directly (announced by scheduled task) |

**Module:** `cogs.funny_things.*`

---

## Social interactions

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `kiss @user` | — | Everyone | Kiss interaction + GIF; increments stats |
| `hug @user` | — | Everyone | Hug |
| `pat @user` | — | Everyone | Headpat |
| `slap @user` | — | Everyone | Slap |
| `punch @user` | — | Everyone | Punch |
| `hit @user` | — | Everyone | Hit |
| `poke @user` | — | Everyone | Poke |
| `cuddle @user` | — | Everyone | Cuddle |
| `snuggle @user` | — | Everyone | Snuggle |
| `boop @user` | — | Everyone | Boop nose |
| `handhold @user` | `holdhand` | Everyone | Hold hands |
| `bonk @user` | — | Everyone | Bonk |
| `bite @user` | `nom` | Everyone | Bite |
| `stare @user` | — | Everyone | Stare |
| `lick @user` | — | Everyone | Lick |
| `smack @user` | — | Everyone | Affectionate punch (đấm yêu) |
| `sniff @user` | — | Everyone | Sniff |
| `kidnap @user` | — | Everyone | Playful kidnap / carry |
| `avatar [@user]` | `av`, `global_avatar`, `globalav` | Everyone | Show Discord global avatar (default: author) |
| `server_avatar [@user]` | `sav`, `guild_avatar`, `serverav` | Everyone (guild) | Show server avatar, or global avatar if unset |
| `propose @user` | — | Everyone (guild) | Propose marriage; partner presses Yes/No (5m); expired proposals update UI) |
| `marriage [@user]` | `marry`, `marriage_status` | Everyone (guild) | Marriage status embed (rank, level, XP, anniversary) |
| `marriage help` | — | Everyone (guild) | Rules: XP, ranks, cooldowns |
| `marriage top` | `lb`, `leaderboard`, `rank` | Everyone (guild) | Top 10 couples by XP |
| `divorce` | — | Everyone (guild) | End active marriage after confirm buttons |
| `rank [r] [action]` | `ranking` | Everyone | All-time bot-wide interaction leaderboards (`r` = receivers); action can be kiss, hug, pat, slap, punch, hit, poke, cuddle, snuggle, boop, handhold, bonk, bite, stare, lick, smack, sniff, or kidnap |
| `cat` | — | Everyone | Random cat image (external API) |
| `dog` | — | Everyone | Random dog image (external API) |
| `36` | — | Everyone | Static meme GIF reply |

**Module:** `cogs.interaction.user_interaction`, `cat`, `dog`, `meme_interaction`

All 18 SFW interactions require a non-bot target and have a 3-second per-command, per-user cooldown. Self-target is allowed only for `pat`, `slap`, `punch`, `hit`, `poke`, `bonk`, and `smack`.

---

## NSFW features

> Adult content. Restrict to age-gated communities and NSFW channels. Follow Discord ToS and third-party API terms.

### Search

| Command | Access | Description |
| --- | --- | --- |
| `r34 <tags>` | NSFW channel | Rule34 image/video search |
| `gbr <tags>` | NSFW channel | Gelbooru image/video search |

### Interactions (NSFW channel, 3-second per-command, per-user cooldown)

| Command | Aliases | Description |
| --- | --- | --- |
| `nsfwrule` | — | Interaction rules embed |
| `bj @user` | — | Blowjob interaction |
| `rj @user` | — | Rimjob interaction |
| `hj @user` | — | Handjob (self allowed) |
| `fj @user` | — | Footjob interaction |
| `aj @user` | `assjob` | Assjob interaction |
| `tj @user` | `thighjob` | Thighjob interaction |
| `spank @user` | — | Ass spanking (self allowed) |
| `frot @user` | — | Frotting |
| `fuck @user` | — | Sex interaction |
| `cream @user` | — | Creampie |
| `3some @user1 @user2` | `threesome` | Threesome with two others |
| `orgy @user1 … @userN` | — | Orgy with 2–10 other members |
| `ranknsfw [r] [action]` | `nsfwrank` | Current-UTC-month bot-wide leaderboards; action can be bj, rj, hj, fj, aj, tj, spank, frot, fuck, cream, 3some, or orgy |
| `mrank <month> <year>` | — | Administrator monthly NSFW ranking |

### Super-user controls

| Command | Access | Description |
| --- | --- | --- |
| `locknsfw @user` | Configured Queen role | Lock a member's NSFW interaction commands for 24 hours; successful use has a 3-day cooldown |
| `unlocknsfw` | Configured Queen role | End an active interaction lock created by the same Queen |

### Verification helpers (moderation)

| Command | Access | Description |
| --- | --- | --- |
| `verified @user` | Manage Roles | Grant configured verified/NSFW-access role |
| `unverified @user` | Manage Roles | Remove verified role |
| `self_unverified` | Everyone (guild) | Member self-service: confirm Yes/No to remove their own verified role. There is no self-service restore; they must ask staff (`verified`) again |

**Module:** `cogs.nsfw.*`, `cogs.interaction.nsfw_*`, `cogs.mod.verified`

---

## Booster perks

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `custom_role <color> <name>` | `booster_role` | Booster | Create a booster custom role (`#RRGGBB` or gradient `#RRGGBB,#RRGGBB`; optional PNG icon). Run without arguments for preset/custom colors, preview, and confirmation UI; the existing argument form remains supported |
| `update_custom_role <color> <name>` | `customroleupdate`, `boosterroleupdate` | Booster | Update the existing booster role. Run without arguments for the same guided color editor; the existing argument form remains supported |
| `custom_room <name>` | `booster_room` | Booster | Create a private voice room under the configured category. Run without arguments for a guided name/user-limit preview; the existing name argument remains supported |

**Background (`janitor_unboosted`):** scheduled cleanup of custom roles/rooms after boost expires.

**Module:** `cogs.booster.*`

---

## Job reminders

| Command | Access | Description |
| --- | --- | --- |
| `jobremind` | Everyone | Subcommand guide |
| `jobremind add` | Everyone | Interactive timed DM reminder (duration then job name) |

**Background:** minute loop sends due DMs and removes completed task records.

**Module:** `cogs.job_remind.job_remind`

---

## Bedtime reminders

Guild administrators maintain one recurring daily sleep schedule per member. All
times use fixed Vietnam time (UTC+7); `H:MM` and `HH:MM` are accepted and shown as
`HH:MM`. The configured wake time is the next occurrence after bedtime.

| Command | Access | Description |
| --- | --- | --- |
| `bedtime` | Administrator (guild) | Open an interactive panel to add, remove, and page through schedules |
| `bedtime add @member <bedtime_HH:MM> <wake_HH:MM> #channel` | Administrator (guild) | Add or update the member's schedule and announcement channel |
| `bedtime remove <@member\|user_id>` | Administrator (guild) | Remove the member's schedule immediately; a saved ID works after they leave |
| `bedtime list` | Administrator (guild) | List the guild's schedules in pages without mentioning their members |

The panel is owner-locked and re-checks Administrator on every click. Add uses a
member select, a text/announcement channel select, and a modal for `H:MM` /
`HH:MM` times; remove uses a select of saved schedules, including members who
already left. Text subcommands remain as a shortcut.

**Background:** at bedtime the bot mentions the member once in the configured
channel. From bedtime (inclusive) until wake time (exclusive), every non-bot
message that member sends anywhere in the guild—including commands and
attachment-only messages—receives a mentioning reply in the same channel, with
no cooldown. The minute scheduler catches up after a restart only while the
current sleep window remains active.

Schedules are guild-scoped and persisted in `bedtime_reminders`; the message
listener uses an in-memory cache and does not query MongoDB for each message.

**Module:** `cogs.bedtime_remind.bedtime_remind`,
`cogs.bedtime_remind._bedtime_helpers`,
`cogs.bedtime_remind._bedtime_ui`

---

## Community utilities

### Giveaways

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `giveaway <duration> [winners] <prize>` | `ga` | Administrator / Manage Guild / Manage Messages | Start giveaway (e.g. `1h30m`, `2d`); persistent join/leave buttons |
| `giveaway list` | `ls`, `active` | Everyone (guild) | List active giveaways |
| `giveaway entries [message_id]` | `entrants`, `joined`, `who` | Everyone (guild) | Who joined a giveaway |
| `giveaway end [message_id]` | — | Host or Administrator / Manage Guild / Manage Messages | End early and pick winners; accepts an ID or replied giveaway message |
| `giveaway reroll [message_id] [winner_count]` | `rr` | Host or Administrator / Manage Guild / Manage Messages | Reroll 1–20 winners; accepts an ID or replied ended giveaway message |

Duration range: 10 seconds–30 days; max 20 winners.

### Votes

| Command | Access | Description |
| --- | --- | --- |
| `vote [yesno\|multiple\|multiplechoice] [question]` | Everyone | Interactive poll (asks for duration; multiple-choice collects options). Ends on schedule with results |

### Other utils

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `highlight` | — | Everyone (guild) | Show the current unique non-bot 💀 threshold, eligible message content and SFW channel requirements, destination channel, and minimum interval between highlight posts in the server |
| `quote [image] [message_link\|message_id]` | `q`, `quotes` | Everyone (guild) | Quote a replied/current-channel message as a text embed by default. Add `image` before the optional link/ID to generate a PNG card with bundled offline emoji/symbol fallback fonts. Both modes use the author's server avatar when available, link to the original message, include a signed TFVN proof bound to the source snapshot, and have a 5s per-user cooldown |
| `hash_verify <proof_code>` | — | Everyone (guild) | Resolve a short `tfp1_…` code to its hidden signed token, require the requested/record/signed IDs to match, and verify the saved Femboy Card or quote snapshot against the signed digest. Full legacy `tfv1.…` tokens also work. Results are limited to the signed guild; private quote text is shown only in the exact source channel/thread with history access. Limited to 3 checks per 10 seconds per user |
| `big_speaker <size> <message>` | `loa`, `speaker` | Everyone (guild) | Re-speak a message in large Discord markdown. **`size` is 1–6**; TC cost by size: **1 / 2 / 5 / 10 / 20 / 50**. Sizes 5–6 add separators; 6 is bold H1. Mentions: user only; strips `@everyone`, `@here`, role pings. 30s cooldown |
| `random_member <@member\|@role>` | — | Everyone | Pick a random member (from role members if a role is given) |
| `save_image <collection> [key value ...]` | — | Manage Messages | Persist attached images + optional metadata pairs to Mongo (`images`) |

**Module:** `cogs.utils.*`

---

## Moderation

Complete legacy arguments execute immediately through the same permission,
hierarchy, validation, and audit checks as the UI. For example, `!tf purge 5`
deletes the five latest messages before the command; `!tf purge` opens the form.
Argument-free replies keep the guided member workflow. Commands requiring a
value (such as timeout minutes, a nickname, or a role) open the UI when it is
omitted. Reply workflows do not accept extra arguments.

### Member actions

| Command | Access | Description |
| --- | --- | --- |
| `kick [@user] [reason]` | Kick Members | Explicit member kicks immediately; argument-free reply opens reason and confirmation UI; records case |
| `ban [@user] [reason]` | Ban Members | Explicit member bans immediately with the legacy 24-hour message deletion default; argument-free reply opens the 0–168-hour deletion, reason, and confirmation UI; records case |
| `unban <user_id\|@user> [reason]` | Ban Members | Explicit ID/mention unbans immediately without a reinvite. Argument-free reply opens the optional unique one-use 7-day reinvite, reason, and confirmation UI. Reinviting requires moderator and bot Create Invite in a public rules/welcome/system/command channel; the invite is DMed or returned privately. Records case |
| `softban [@user] [reason]` | Ban Members | Explicit member immediately replaces eligible roles with Tù ngay; argument-free reply opens UI; stores previous roles and records case |
| `unsoftban [@user] [reason]` | Ban Members | Explicit member immediately restores saved roles; argument-free reply opens UI; records case |
| `mute [@user] [reason]` | Manage Roles | Explicit member immediately assigns Muted; argument-free reply opens UI; records case |
| `unmute [@user] [reason]` | Manage Roles | Explicit member immediately removes Muted; argument-free reply opens UI; records case |
| `timeout [@user] [minutes] [reason]` | Moderate Members | Member plus 1–40,320 minutes applies immediately; omitted minutes or argument-free reply opens UI; records case |
| `untimeout [@user] [reason]` | Moderate Members | Explicit member immediately clears timeout; argument-free reply opens UI; records case |
| `warn [@user] [reason]` | Manage Messages | Explicit member immediately stores the warning and case; argument-free reply opens UI |
| `check_warn [@user]` | Everyone (guild) | Recent warnings (default: self) |
| `nickchange [@user] [new_nick]` | Manage Nicknames | Member plus nickname applies immediately; omitted nickname or argument-free reply opens nickname, reason, and confirmation UI |
| `roleroll [@user] [role_name]` | Manage Roles | Member plus role name assigns immediately; omitted role or argument-free reply opens role, reason, and confirmation UI |
| `roleunroll [@user] [role_name]` | Manage Roles | Member plus role name removes immediately; omitted role or argument-free reply opens role, reason, and confirmation UI |
| `rolecopy [@source] [@target] [reason]` | Manage Roles | Both members copies eligible roles immediately. Argument-free reply to the destination opens source selection and confirmation of the frozen role table. Completed replies list roles actually copied |

### Messages & channel controls

| Command | Access | Description |
| --- | --- | --- |
| `purge [n]` | Manage Messages | Supplied count immediately deletes 1–1,000 latest messages before the invocation; omitted count opens the form and confirmation |
| `purge_user [@user] [n]` | Manage Messages | Member plus count immediately deletes 1–1,000 newest matching messages before the invocation, scanning at most 5,000 messages; omitted count or argument-free reply opens UI |
| `clean_before [days]` | Manage Messages | Supplied 1–3,650 days immediately deletes older messages before the invocation; omitted days opens the form and confirmation |
| `slowmode` | Everyone (guild) | Slowmode guide group |
| `slowmode check_bypass [@member]` | Everyone | Check channel permission overwrites |
| `slowmode immune [@member] [reason]` | Manage Roles | Explicit member immediately adds bypass, preserving unrelated overwrites; argument-free reply opens UI |
| `slowmode prominent [@member] [reason]` | Manage Roles | Explicit member immediately removes only the bypass overwrite; argument-free reply opens UI |

Successful `purge` and `purge_user` actions also delete the command message;
it does not count toward the requested number. Result notices disappear after
five seconds in both direct commands and UI confirmations.

### Cases

| Command | Access | Description |
| --- | --- | --- |
| `case` | Manage Messages | Usage guide |
| `case view <number>` | Manage Messages | View case by number |
| `case history @user [limit]` | Manage Messages | Last 1–10 cases for a member |
| `case edit <number> [reason]` | Manage Messages | Supplied reason updates immediately; omitted reason opens confirmation UI; refuses stale updates |
| `case status <number> [open\|resolved\|appealed\|void]` | Manage Messages | Supplied status updates immediately; omitted status opens confirmation UI; refuses stale updates |
| `case log_channel [#channel]` | Manage Guild | Explicit channel changes the mod-log destination immediately after checking bot send/embed access; omitted channel opens selection and confirmation UI |

### Area 51 guard

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `area51_fire` | `area51_bump_now`, `area51_remind_now` | Administrator | Preview the configured destination and confirm sending an Area 51 warning |

**Background:** honeypot channel monitoring, cancel-ban UI, auto-prune, weekly reminder task. Configured via `AREA_51_CHANNEL_ID` / prune variables in Mongo settings.

**Module:** `cogs.mod.*`

---

## Discipline (automatic)

No user command. On message, if content matches entries in `data/banned_word_list.txt`:

1. Log breach to `discipline_logs`
2. React ⚠️ and warn the author (violation count)
3. Delete the warning after a few seconds and delete the original message

**Module:** `cogs.discipline.discipline`

---

## Settings (runtime configuration)

| Command | Access | Description |
| --- | --- | --- |
| `setting` | Administrator | Settings group help |
| `setting set_variable <NAME>` | Administrator | Interactive set of a Mongo `global_variables` key (loaded into `bot.global_vars`) |
| `setting get_variable <NAME>` | Administrator | Read a stored variable |

Common variable examples (not exhaustive): channel IDs, booster category, Area 51 channel, media arrays, `BETA_ROLE_IDS`.

**Module:** `cogs.settings.variable_setting`

---

## Seasonal / one-shot

| Feature | Type | Description |
| --- | --- | --- |
| Lunar New Year 2026 | Message listener | During a fixed UTC window (2026-02-16–17), messages containing keywords like `năm mới`, `nmvv`, `2026`, or `new year` get a one-time personal greeting |

**Module:** `cogs.happy_new_year.happy_lunar_new_year_2026`

---

## Internal helpers (not loadable as cogs)

These modules support features but are not discovered as extensions (leading `_`):

| Module | Role |
| --- | --- |
| `cogs._beta_function` | `@BetaFunction` decorator and access checks |
| `cogs._feature_flags` | `DISABLED_COGS` pattern parsing |
| Underscore helpers in packages | Shared validation, case recording, meters, shop helpers, setup diagnostics, role colors |

---

## Quick index by prefix (flat list)

```
help, mod, nsfw
hello, invite, verify, role_exam, self_unverified, ping, beta_preview, server_stats, operation_dashboard, leave
bot_status, bot_status set, bot_status show, bot_status reset
setup, setup check
triggerreply, triggerreply add, triggerreply update, triggerreply list, triggerreply remove
afk, afk dynamic, afk time, afk clear, afk check
random_femboy
daily, user_balance, user_transactions, add_tc, remove_tc, set_tc, check_tc
shop, shop buy, shop inventory, shop use, shop unequip, shop add_role, shop add_badge, shop remove
tutien, tutien batdau, tutien thucong, tutien huong, tutien dotpha,
tutien phai, tutien phai reset, tutien thienphu, tutien thienphu tang,
tutien dongphu, tutien dongphu nangcap, tutien choden, tutien mua, tutien kho,
tutien trangbi, tutien phanra, tutien luyen, tutien thiluyen,
tutien bicanh, tutien bicanh start, tutien bicanh claim, tutien bicanh cancel,
tutien doido, tutien doido mua, tutien doido ban,
tutien profile, tutien top, tutien riengtu
blackjack, poker, slot, flip_coin, sicbo_start
crocodile, crocodile challenge, crocodile fire
noitu, noitu status, noitu hint, noitu end, noitu analyze
vtv, vtv status, vtv next, vtv hint
gay, les, ship, penisize, titansize, aura, redflag, based, brainrot, clown, cope, cringe, delulu,
gyatt, ick, mainchar, npc, ohio, rizz, simp, skillissue, touchgrass, yapper,
femboycard, birthday, birthday set
kiss, hug, pat, slap, punch, hit, poke, cuddle, snuggle, boop, handhold, bonk, bite, stare, lick, smack, sniff, kidnap,
avatar, server_avatar, propose, marriage, marriage help, marriage top, divorce, rank, cat, dog, 36
r34, gbr, nsfwrule, bj, rj, hj, fj, aj, tj, spank, frot, fuck, cream, 3some, orgy, ranknsfw, mrank
locknsfw, unlocknsfw, verified, unverified
custom_role, update_custom_role, custom_room
jobremind, jobremind add
bedtime, bedtime add, bedtime remove, bedtime list
giveaway, giveaway list, giveaway entries, giveaway end, giveaway reroll
vote, highlight, quote, hash_verify, big_speaker, random_member, save_image
kick, ban, unban, softban, unsoftban, mute, unmute, timeout, untimeout, warn, check_warn
nickchange, roleroll, roleunroll, rolecopy
purge, purge_user, clean_before
slowmode, slowmode check_bypass, slowmode immune, slowmode prominent
case, case view, case history, case edit, case status, case log_channel
area51_fire
setting, setting set_variable, setting get_variable
```

**Automatic features:** random bot activity rotation at random 5–15 minute intervals, paused while an Administrator's temporary `bot_status` override is active; welcome and differentiated leave/kick/ban announcements, AFK monitoring, banned-word discipline, booster unboost janitor, birthday announcements, job-reminder and bedtime-reminder loops, bedtime chat replies, giveaway/vote end scheduling, highlight posting when a SFW message reaches `HIGHLIGHT_THRESHOLD` unique non-bot 💀 (Discord-chat PNG to `HIGHLIGHT_CHANNEL`, at most one post per guild every `HIGHLIGHT_MIN_INTERVAL_SECONDS`; Vietnamese congrats reply on the source message; NSFW channels are ignored), Area 51 honeypot, Lunar New Year greeting, word-game message handling. All departure variants use `BYE_CHANNEL`; View Audit Log permission is required to reliably distinguish kicks from voluntary leaves.

Use `highlight` with the configured bot prefix (for example, `!tfd highlight`)
to view the current requirements and destination channel, or a notice when no
channel is configured. The command reads the threshold and spacing used by the
automatic feature. Qualified messages wait for the guild's posting interval and
must still have enough reactions when posted; each source message is posted once.

Highlight cards include message text, up to four gallery images, and up to four
embeds with their titles, descriptions, authors, fields, footers, images, and
thumbnails. Embed mentions resolve to cached member, role, and channel names;
missing references use an unknown-name label. Embed text supports bold, italic,
underline, strikethrough, inline/fenced code, and link labels. Code keeps its literal
contents and spacing. Meter blocks and rainbow flags render
without relying on system fonts. Messages containing only an image or embed are
supported. Link previews
use the image Discord supplies; videos and GIFs appear as static image previews
when available. The gallery selects from the first four image attachments and
image-only embeds. Uploaded or pasted images appear below the source message's
caption, or on their own when it has no text. Extensionless uploads can use
Discord's image dimensions for detection. Long content is truncated to fit the
card. Image downloads are limited to 8 MiB each; embed images use Discord's CDN or
media proxy. If the original attachment cannot be downloaded or exceeds the
limit, its cached Discord preview is tried with the same download limit. Unavailable,
oversized, or unreadable media is skipped while remaining text and media still
render. No post is sent if nothing renderable remains. Threshold and spacing knobs
live in `cogs/utils/_highlight_helpers.py`.

Bot status data uses `type` + `think` for `CUSTOM` entries. All other activity types use `type` + `text` so their action-card text remains prominent.
Temporary Administrator overrides use the same activity rendering without modifying
the status data file or creating MongoDB records.

---

## Keeping this file up to date

When you add or remove a command:

1. Update the relevant section and the flat index.
2. If structure or ownership of modules changes, also update `CODEBASE.md`.
3. Prefer documenting the public `name=` / aliases / permission checks from the cog, not private helper functions.
)
