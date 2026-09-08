# Codebase Map

This document maps the maintained repository files and explains where each behavior lives. It intentionally omits secrets and generated workstation artifacts such as `.env`, `.env.prod`, `.git/`, `.agents/`, `venv/`, `__pycache__/`, `.VSCodeCounter/`, and `bot.log`.

## Runtime Flow

1. `main.py` loads `.env`, creates the prefix-based `commands.Bot`, enables member and message-content intents, attaches the MongoDB database from `db.py`, and owns graceful SIGINT/SIGTERM command draining.
2. `DataLoader` loads shared lists from `data/` onto the bot instance.
3. In production, every public Python module below `cogs/` is discovered recursively. Development uses the ignored `dev_cogs.txt`. Both use the database selected by `DB_NAME`.
4. `cogs.settings.variable_setting` is loaded first when selected, populating `bot.global_vars` from MongoDB.
5. Each extension registers commands, listeners, views, or scheduled tasks through `async def setup(bot)`.

## Repository Tree and Responsibilities

```text
tfvn_bot/
├── main.py                         Bot construction, events, data loading, cog discovery
├── db.py                           MongoDB client and selected database
├── dataloader.py                   UTF-8 JSON/text/line/CSV loading helpers
├── requirements.txt                Pinned Python runtime dependencies
├── Dockerfile                      Python 3.11 multi-stage image
├── docker-compose.yml              Bot service and environment wiring; no Mongo service
├── .dockerignore                   Excludes secrets, tests, logs, and local artifacts
├── .gitattributes                  Normalizes tracked text files to LF line endings
├── .gitignore                      Excludes local configuration, logs, and Python artifacts
├── README.md                       Project overview, setup, configuration, and operation
├── AGENTS.md                       Short contributor and agent entry guide
├── CODEBASE.md                     This ownership and architecture map
├── FUNCTIONS.md                    Full user-facing command and feature catalog
├── CULTIVATE_GAME_PLAN.md           Tiên Lộ gameplay, economy, and acceptance specification
├── CODING_CONVENSION.md            Detailed implementation conventions
├── sample.dev_cogs.txt             Legacy development-cog sample; review paths before use
│
├── .github/workflows/
│   ├── build_and_push.yml          Builds and publishes images to GHCR
│   └── notificate_to_discord.yml   Sends tag notifications to Discord
│
├── assets/
│   ├── gifs.py                     Welcome and general interaction media URLs
│   └── nsfw_gifs.py                Legacy NSFW media lists used by migration tooling
│
├── fonts/
│   ├── NotoSans-Variable.ttf        Primary Vietnamese-capable quote-card font
│   ├── NotoEmoji-Variable.ttf       Offline fallback for Unicode emoji glyphs
│   ├── NotoSansSymbols-Variable.ttf Common symbol and music-glyph fallback
│   ├── NotoSansSymbols2-Regular.ttf Decorative symbol and dingbat fallback
│   ├── *-OFL.txt / OFL.txt          SIL Open Font licenses for bundled fonts
│   └── README.md                    Font sources, hashes, and bundling notes
│
├── data/
│   ├── banned_word_list.txt        Discipline filter terms
│   ├── bot_activity_funny_status.json
│   │                                 Random Discord custom/action definitions
│   ├── fake_loading_sentences.txt  Random progress text for fun commands
│   ├── femboy_role.txt             Role names used by the femboy card command
│   ├── nsfw_channel.json           Verification-managed NSFW channel definitions
│   ├── role_exam.json              Role-exam questions, pass percentage, and reward role ID
│   ├── vietnamese_king_data.json   Generated Vua Tiếng Việt puzzle dataset
│   └── word_connect_valid_list.txt Valid Vietnamese word-chain entries
│
├── scripts/
│   ├── migrate_nsfw_gifs.py        Moves legacy GIF lists into Mongo global variables
│   ├── vietnamese_king_data_prepare.py
│   │                                 Normalizes/filter source words and generates game data
│   └── words.txt                   Source records for Vietnamese data preparation
│
├── test/
│   ├── test_bedtime_reminder.py    Bedtime time rules, persistence, commands, and listeners
│   ├── test_bedtime_ui.py          Bedtime admin panel, selects, modal, and permission checks
│   ├── test_card_games.py          Blackjack, Poker, deck, and payout rules
│   ├── test_card_game_economy.py   Atomic card-game wager and refund helpers
│   ├── test_crocodile_dentist.py   Crocodile rules, persistence, commands, and UI behavior
│   ├── test_community_features.py  Pure validation/time/helper regression tests
│   ├── test_cultivation.py         Tiên Lộ calculations, state, UI, and persistence tests
│   ├── test_help_menu.py           Help catalog completeness, limits, gates, and UI tests
│   ├── test_highlight.py           Highlight listener, spacing, media download, and posting tests
│   ├── test_highlight_card.py      Discord-chat highlight PNG, embed, and gallery tests
│   ├── test_highlight_font.py      Highlight meter symbols and composite emoji rendering tests
│   ├── test_highlight_media.py     Embed extraction, media limits, and Discord proxy URL tests
│   ├── test_highlight_text.py      Embed Markdown parsing, styled wrapping, and text drawing tests
│   ├── test_hash_verification.py    Signed proof, forgery, tamper, producer, and privacy tests
│   ├── test_meter_number_bars.py   unittest coverage for signed meter formatting
│   ├── test_role_exam.py           Role-exam invitation, UI, safety, and role-grant tests
│   ├── test_role_exam_helpers.py   Role-exam JSON validation, shuffling, and scoring tests
│   └── word_stardardlize.py        Manual normalization utility; not auto-discovered as a test
│
└── cogs/
    ├── __init__.py                 Root extension package marker
    ├── _beta_function.py           Multi-role Beta command access guard
    ├── _feature_flags.py           DISABLED_COGS pattern parsing
    ├── _hash_verification.py       HMAC proof issuance and snapshot-integrity checks
    ├── general.py                  hello, invite, and verification-channel pointers
    ├── help.py                     Full-catalog dropdown help UI with an NSFW channel gate
    ├── afk_remind/
    │   ├── afk_set.py              Timed/dynamic AFK setup, clearing, and ping review
    │   └── afk_monitor.py          AFK mention capture and return detection
    ├── bedtime_remind/
    │   ├── _bedtime_helpers.py     Pure UTC+7 parsing, sleep-window, and deadline rules
    │   ├── _bedtime_ui.py          Admin panel, member/channel selects, and time modal
    │   └── bedtime_remind.py       Admin schedules, minute mentions, and chat reminders
    ├── announcement/
    │   ├── __init__.py             Announcement package marker
    │   ├── welcome.py              Member-join announcement
    │   └── goodbye.py              Unified leave/kick/ban departure announcement
    ├── booster/
    │   ├── _custom_resource_ui.py Guided booster role/room views, selects, and modals
    │   ├── _role_colors.py         Solid/gradient role-color parsing helper
    │   ├── create_custom_role.py   Booster-owned custom role creation
    │   ├── update_custom_role.py   Booster custom role edits
    │   ├── create_custom_room.py   Booster private voice-room creation
    │   └── janitor_unboosted.py    Scheduled cleanup after boosts expire
    ├── cotd/random_femboy.py       Random saved image and social metadata lookup
    ├── cultivation/
    │   ├── __init__.py             Cultivation package marker
    │   ├── cultivation.py          Tiên Lộ commands, dashboard, and atomic persistence
    │   └── _cultivation_helpers.py Pure realms, rewards, market, PvE, and exchange rules
    ├── daily_reward/
    │   ├── daily_action.py         Daily Trap Coin grant and claim tracking
    │   └── user_account.py         Balance, badge, and transaction-history lookup
    ├── economy/
    │   ├── _shop_helpers.py        Catalog ID, price, and display validation
    │   └── shop.py                 Guild catalog, purchases, inventory, roles, and badges
    ├── discipline/discipline.py    Banned-word listener, logging, warning, and deletion
    ├── funny_things/
    │   ├── _meter_helper.py        Deterministic scores, bars, loading, and embed helpers
    │   ├── _birthday_ui.py         Owner-only month and day picker view
    │   ├── aura.py                 Signed aura score and icon bar
    │   ├── redflag.py              Signed red/green flag score and icon bar
    │   ├── birthday.py             Birthday registration and announcement task
    │   ├── femboy_card.py          Member card based on configured role names
    │   ├── gay_meter.py            Daily member meter with staged loading
    │   ├── penisize.py             Daily member meter with staged loading
    │   ├── titansize.py            Daily fictional centimeter-size and cup meter
    │   ├── ship_meter.py           Two-member compatibility meter
    │   └── based.py, brainrot.py, clown.py, cope.py, cringe.py, delulu.py,
    │       gyatt.py, ick.py, les_meter.py, mainchar.py, npc.py, ohio.py,
    │       rizz.py, simp.py, skillissue.py, touchgrass.py, yapper.py
    │                                 Shared-helper-based daily meter commands
    ├── happy_new_year/
    │   └── happy_lunar_new_year_2026.py
    │                                     Time-limited one-time Lunar New Year greeting
    ├── interaction/
    │   ├── cat.py, dog.py             External animal-image API commands
    │   ├── meme_interaction.py        Static meme response command
    │   ├── user_interaction.py        Social actions, avatar display, and rankings
    │   ├── marriage.py                Propose/divorce/status, couple XP ranks
    │   ├── _marriage_helpers.py       Pure level/rank/XP helpers for marriage
    │   ├── triggered_reply.py          Persistent phrase-triggered replies
    │   ├── _trigger_reply_helpers.py   Rule parsing and matching helpers
    │   ├── nsfw_interaction.py        Age-gated interactions and rankings
    │   └── nsfw_super_user.py         Role-controlled NSFW lock/unlock workflow
    ├── job_remind/job_remind.py       Persistent timed DM reminders
    ├── minigames/
    │   ├── _playing_cards.py           Shared validated deck and card formatting
    │   ├── _card_game_economy.py       Atomic TC wagers, payouts, refunds, and audit logs
    │   ├── blackjack/
    │   │   ├── _blackjack_helpers.py   Pure Blackjack scoring and round state
    │   │   └── blackjack.py            Button-driven solo Blackjack against the dealer
    │   ├── poker/
    │   │   ├── _poker_helpers.py       Five-card hand ranking, dealer draw, round state
    │   │   └── poker.py                Button-driven solo five-card draw against the dealer
    │   ├── flip_coin/flip_coin.py     Coin betting against user balances
    │   ├── slot_machine/slot_machine.py
    │   │                                 Slot betting, payouts, and transaction logs
    │   ├── sicbo/sicbo.py             Reaction-based Sic Bo rounds
    │   ├── crocodile_dentist/
    │   │   ├── _crocodile_helpers.py  Pure challenge parsing and game-state transitions
    │   │   └── crocodile.py           Persistent invitations, tooth UI, expiry, and commands
    │   ├── word_connect/word_connect.py
    │   │                                 Persistent Vietnamese word-chain game
    │   └── vietnamese_king/vietnamese_king.py
    │                                     Persistent letter-scramble game
    ├── mod/
    │   ├── _case_helpers.py         Safe shared case recording and validation
    │   ├── _interaction_ui.py       Shared forms, reason choices, and confirmation guard
    │   ├── _reply_target.py         Strict same-channel reply-member resolution
    │   ├── _cleanup_state.py        Cross-cog channel-cleanup execution lock
    │   ├── _member_state.py         Cross-cog member-role mutation guard
    │   ├── _ban_ui.py              Staged reply/mention ban UI, reasons, and confirmation
    │   ├── _unban_ui.py            Staged reinvite, reason, and confirmation UI
    │   ├── cases.py                 Numbered moderation audit trail and log config
    │   ├── ban.py                   Reply/mention ban command orchestration
    │   ├── kick.py                  Reply-aware guarded member removal
    │   ├── mute.py, timeout.py      Guarded temporary restriction controls
    │   ├── softban.py               Guarded soft-ban and role restoration data
    │   ├── purge.py, janitor.py     Confirmed, invocation-anchored message cleanup
    │   ├── nickname.py, role.py     Confirmed nickname and role workflows
    │   ├── slowmode.py              Slow-mode inspection and guarded overrides
    │   ├── unban.py                     Reply/user-ID unban and reinvite orchestration
    │   ├── warn.py                      Warning commands
    │   ├── verified.py                  Verified role grant/revoke and member self-unverify confirmation
    │   └── area_51_guard.py             Honeypot channel, cancel view, bans, and reminders
    ├── nsfw/
    │   ├── __init__.py             NSFW extension package marker
    │   ├── r34.py                       Age-gated Rule34 API search
    │   └── gelbooru.py                  Age-gated Gelbooru API search
    ├── onboarding/
    │   ├── _role_exam_helpers.py       Pure role-exam configuration, validation, and scoring
    │   └── role_exam.py                Staff invitation, private exam UI, and safe role grant
    ├── operation/
    │   ├── bot_status.py                Random Discord activity and timing rotation
    │   ├── _graceful_shutdown.py        Command admission, drain tracking, and signal helpers
    │   ├── _lifecycle.py                Append-only process/gateway lifecycle event recorder
    │   ├── _operation_helpers.py        Audit ranges, sanitization, and safe CSV generation
    │   ├── _setup_helpers.py           Pure setup-check result and ID helpers
    │   ├── heartbeat.py                 Latency/health command
    │   ├── operation_dashboard.py       Health/audit UI plus private Bot owner guild/lifecycle panels
    │   ├── server_stats.py              In-memory uptime and command/error counts
    │   ├── setup_check.py               Database, permission, ID, and cog diagnostics
    │   └── leave.py                     Administrator-controlled guild departure
    ├── settings/variable_setting.py     Mongo-backed runtime variable commands
    └── utils/
        ├── giveaway.py                  Persistent views, entries, scheduling, and rerolls
        ├── vote.py                      Persistent reaction polls and result scheduling
        ├── highlight.py                 Requirements command, 💀 listener, media downloads, chat PNG, TV congrats reply
        ├── _highlight_helpers.py        Skull/interval knobs, NSFW skip, channel helpers
        ├── _highlight_card.py           Discord dark-theme chat PNG, image gallery, and embed rendering
        ├── _highlight_font.py           Portable meter-block and rainbow-flag drawing with bundled fonts
        ├── _highlight_media.py          Bounded embed snapshots, mention names, and Discord media URLs
        ├── _highlight_text.py           Embed Markdown styles, code blocks, and measured text wrapping
        ├── quote.py                     Text-embed and PNG message quote modes
        ├── _quote_card.py               Quote text wrapping and PNG card rendering
        ├── hash_verify.py               Signature-first femboy-card/quote proof verification
        ├── big_speaker.py               Paid TC big-text re-speak in current channel
        ├── _big_speaker_helpers.py      Size 1–6 → TC cost, mention sanitize, format helpers
        ├── random_member.py             Random guild member selection
        └── save_image.py                Discord attachment metadata persistence
```

Local `dev_cogs.txt` selects extensions during development. `DISABLED_COGS` can
filter loaded extensions with exact dotted modules or wildcard patterns.
`draft.txt` is a local scratch file.

## Persistence Boundaries

MongoDB collections are created lazily. Major groups are:

- Configuration: `global_variables`, `moderation_config`
- Economy: `user_accounts` (including versioned `cultivation` state), `daily_rewards_logs`, `transaction_logs`, `shop_items`, `shop_inventory`
- Cultivation audit: append-only `cultivation_events`; TC exchanges also write `transaction_logs`
- Social state: `interactions`, `nsfw_settings`, `images`, `marriages`, `marriage_proposals`, `triggered_replies`
- Content provenance: `hash_verifications` stores immutable, guild-scoped
  femboy-card and quote snapshots. New records use their signed 128-bit token ID
  as MongoDB `_id` and store the full HMAC token privately; legacy records remain
  keyed by a token fingerprint. Cards and quotes display only a short canonical
  `tfp1_<base32-token-id>` reference. Resolution requires the requested ID,
  record `_id`, and ID signed inside the hidden token to match before comparing
  the stored snapshot with its signed digest. Full
  `tfv1.<key-id>.<claims>.<signature>` tokens remain accepted. MongoDB access
  alone therefore cannot mint, redirect, or alter a valid result, though short
  references depend on registry availability and are not portable proofs.
  Signing keys come only from
  `CONTENT_VERIFICATION_KEYS_JSON`; the active ID comes from
  `CONTENT_VERIFICATION_ACTIVE_KEY_ID`, and missing/invalid keys fail closed.
  This is bot-verifiable HMAC rather than public non-repudiation and proves the
  recorded member/role facts or bot-used quote text, not screenshot/PNG pixels,
  avatars, attachments, or embeds. Deployments must not share keyrings. Quote
  text and names stay out of the readable token and are returned only inside the
  exact source channel/thread; PyMongo reads/writes run in worker threads
- Scheduling: `tasks`, `votes`, `giveaways`, `highlight_nominations`, `birthdays`, `birthday_announcements`,
  `bedtime_reminders`. Highlight rows are guild/source-message unique and created when a SFW message first reaches `HIGHLIGHT_THRESHOLD` unique non-bot 💀; they CAS `pending` → `posting` → `posted` before uploading a chat-theme PNG to `HIGHLIGHT_CHANNEL`, with at least `HIGHLIGHT_MIN_INTERVAL_SECONDS` between posts in a guild. NSFW source channels are ignored. Bedtime records are guild/member scoped and hold normalized
  sleep minutes, announcement channel, next UTC deadline, local-date deduplication,
  and audit timestamps; unique guild/member and due-time indexes enforce one schedule
  per member and support the minute scheduler
- AFK and moderation: `afk_reminders`, `afk_pings`, `discipline_logs`, `old_roles`, `warnings`, `moderation_cases`
- Operations audit: `operation_logs` stores guild-scoped recognized prefix-command outcomes and dashboard export/prune actions; records have no automatic TTL and are removed only through the Administrator dashboard
- Bot lifecycle: `bot_lifecycle_events` stores global, append-only `initial_ready`,
  `reidentified`, and `resumed` events indefinitely. The Bot owner dashboard reads
  the newest 10 events for the current environment. This collection is separate
  from `operation_logs` and is excluded from guild audit browsing, CSV export,
  and pruning
- Shared sequence counters: `feature_counters`
- Games and boosters: card-game wagers use `user_accounts` plus `transaction_logs`;
  Crocodile Dentist uses `crocodile_games` plus guild-scoped IDs from
  `feature_counters` keys named `crocodile_game:<guild_id>`; other state uses
  `context`, `sicbo_active_games`, `booster_custom_roles`, and `booster_custom_rooms`

Discord tokens, database credentials, and external API credentials belong in
environment variables. Runtime database selection uses `DB_NAME`.
Process-level extension controls use `DISABLED_COGS`; guild-specific IDs and
media arrays generally belong in `global_variables`.

Tiên Lộ stores its authoritative profile below `user_accounts.cultivation` and
keeps Trap Coin in `user_accounts.balance`, allowing an exchange to update both
balances atomically. Writes use a cultivation revision for compare-and-swap and
idempotent request IDs, with at most three retries. The cog requires a unique
`user_accounts.user_id` index; if duplicate account documents prevent the index,
it logs the problem and remains disabled rather than merging balances.

Crocodile Dentist treats `crocodile_games` as authoritative for both pending
invitations and active turns. Persistent Discord views dispatch stable invitation
and tooth custom IDs after restart, while revision and canonical-message guards
prevent duplicate responses, concurrent tooth presses, and stale replacement
panels from changing state. A background sweep and command/interaction reads settle
five-minute invitation deadlines and seven-day active-game inactivity expiry.

Bedtime reminders use fixed Vietnam time (UTC+7). The cog validates MongoDB
records into a `(guild_id, user_id)` memory cache, sends one configured-channel
mention per active sleep window, and catches up after restarts only before that
window's wake time. `!tf bedtime` opens an owner-locked Discord panel with
member/channel selects and a time modal; prefix subcommands remain as shortcuts.
Its guild-message listener reads only that cache and replies
with a target-only mention to every enrolled member message during the window.
Missing Discord targets or permanent permission failures skip that day's notice;
transient HTTP failures remain eligible for retry while the window is active.

Commands decorated with `@BetaFunction` are registered normally in any runtime,
but the callback requires the invoking member to hold at least one role in
`BETA_ROLE_IDS`. The role list is read only from `bot.global_vars`, populated by
the MongoDB `global_variables` collection; environment role values are ignored.

`main.py` registers a one-time global command admission check. The first SIGINT
or SIGTERM rejects later prefix commands, waits for every previously admitted
invocation to return, and then closes Discord and the lifecycle recorder. A
second signal forces closure. Compose grants the process up to five minutes
before container termination.

## Where to Make a Change

- Add or change a command/listener in its domain under `cogs/`.
- Keep Tiên Lộ Discord/Mongo behavior in `cogs/cultivation/cultivation.py` and
  deterministic tables/calculations in `_cultivation_helpers.py`.
- Put reusable feature helpers in a leading-underscore module beside their consumers.
- Put shared static media in `assets/`; put runtime-editable media in Mongo settings.
- Treat large game datasets as generated outputs and update their preparation script with them.
- Add deterministic regression tests under `test/test_*.py`.
- Update this map whenever files move, a new subsystem appears, or ownership changes.
