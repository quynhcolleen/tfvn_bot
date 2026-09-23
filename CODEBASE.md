# Codebase Map

This document maps the maintained repository files and explains where each behavior lives. It intentionally omits secrets and generated workstation artifacts such as `.env`, `.env.prod`, `.git/`, `.agents/`, `venv/`, `__pycache__/`, `.VSCodeCounter/`, and `bot.log`.

## Runtime Flow

1. `main.py` loads `.env`, creates the prefix-based `commands.Bot`, enables member and message-content intents, attaches the MongoDB database from `db.py`, and owns graceful SIGINT/SIGTERM command draining.
2. `DataLoader` loads shared lists from `data/` onto the bot instance.
3. In production, every public Python module below `cogs/` is discovered recursively. Development uses the ignored `dev_cogs.txt`. Both use the database selected by `DB_NAME`. Selected extensions and safe startup failure types are retained in memory for diagnostics; disabled modules are excluded and current loaded extensions take precedence over stale failure records.
4. `cogs.settings.variable_setting` is loaded first when selected, populating `bot.global_vars` from MongoDB.
5. Each extension registers commands, listeners, views, or scheduled tasks through `async def setup(bot)`.

## Repository Tree and Responsibilities

```text
tfvn_bot/
├── main.py                         Bot construction, events, data loading, cog discovery
├── db.py                           MongoDB client and selected database
├── dataloader.py                   UTF-8 JSON/text/line/CSV loading helpers
├── requirements.txt                Pinned Python runtime dependencies
├── requirements-dev.txt            Runtime dependencies plus the pinned pytest runner
├── pytest.ini                      Test discovery under test/test_*.py
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
├── HOW_TO_IMPLEMENT_FEATURE.md     Feature workflow, focused development tests, final full-suite check
├── sample.dev_cogs.txt             Legacy development-cog sample; review paths before use
│
├── .github/workflows/
│   ├── build_and_push.yml          Builds and publishes images to GHCR
│   ├── tests.yml                   Full pytest suite for PRs, deploy branches, and merge queues
│   └── notificate_to_discord.yml   Sends tag notifications to Discord
│
├── assets/
│   ├── gifs.py                     Welcome and general interaction media URLs
│   ├── lunch/                      Bundled food images, Genshin wish GIFs, and source notes
│   ├── tarot/                      Public-domain Rider-Waite-Smith card scans and source notes
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
│   ├── lunch_foods.json            Bundled lunch catalog with prices, diet tags, and media mapping
│   ├── lunch_foods_extra.json      Local lunch additions with reserved image IDs from 1000
│   ├── nsfw_channel.json           Verification-managed NSFW channel definitions
│   ├── role_exam.json              Role-exam questions, pass percentage, and reward role ID
│   ├── vietnamese_king_data.json   Generated Vua Tiếng Việt puzzle dataset
│   └── word_connect_valid_list.txt Valid Vietnamese word-chain entries
│
├── scripts/
│   ├── migrate_nsfw_gifs.py        Moves legacy GIF lists into Mongo global variables
│   ├── prepare_lunch_assets.py     Prepares the upstream lunch catalog and food image sheets
│   ├── prepare_tarot_assets.py     Downloads and crops public-domain RWS scans into assets/tarot/
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
│   ├── test_shop.py                Shop store, catalog products, and interactive panel
│   ├── test_shop_custom_role.py    Paid custom-role product, designer, and leave cleanup
│   ├── test_shop_custom_room.py    Private room rentals, editor, conflicts, and cleanup
│   ├── test_shop_rentals.py        Renewal accounting, migration, expiry worker, and UI
│   ├── test_giveaway.py            Giveaway duration/prize parsing, role weights, and persistence
│   ├── test_giveaway_ui.py         Giveaway create panel, role settings, modal, and permission checks
│   ├── test_doctor.py              Environment, feature, permission, and runtime diagnostics
│   ├── test_extension_loading.py   Selected extensions and safe startup-failure diagnostics
│   ├── test_cultivation.py         Tiên Lộ calculations, dashboard panels, and persistence tests
│   ├── test_help_menu.py           Help catalog completeness, limits, gates, and UI tests
│   ├── test_interact_streak.py     Pair-streak date/credit rules, listeners, and commands
│   ├── test_legacy_case_slowmode.py Direct case updates and slowmode override regression tests
│   ├── test_lunch.py               Lunch filter UI, owner checks, animation, and lifecycle tests
│   ├── test_lunch_helpers.py       Lunch argument parsing, catalog validation, and selection tests
│   ├── test_lunch_media.py         Food atlas mapping, image crops, and bundled wish validation
│   ├── test_prepare_lunch_assets.py Local additions, collision checks, and offline catalog rebuilds
│   ├── test_highlight.py           Highlight listener, spacing, media download, and posting tests
│   ├── test_highlight_card.py      Discord-chat highlight PNG, embed, and gallery tests
│   ├── test_highlight_font.py      Highlight meter symbols and composite emoji rendering tests
│   ├── test_highlight_media.py     Embed extraction, media limits, and Discord proxy URL tests
│   ├── test_highlight_text.py      Embed Markdown parsing, styled wrapping, and text drawing tests
│   ├── test_hash_verification.py    Signed proof, forgery, tamper, producer, and privacy tests
│   ├── test_softotp.py              Soft OTP challenge codes, commands, UI, and permission tests
│   ├── test_meter_number_bars.py   unittest coverage for signed meter formatting
│   ├── test_operation_dashboard.py Health/audit, Doctor access/pagination, and owner UI tests
│   ├── test_role_exam.py           Role-exam invitation, UI, safety, and role-grant tests
│   ├── test_role_exam_helpers.py   Role-exam JSON validation, shuffling, and scoring tests
│   ├── test_word_game_leaderboard.py Vua Tiếng Việt / Nối Từ win ranking and top commands
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
    │   ├── _room_helpers.py        Shared category validation and private room overwrites
    │   ├── create_custom_role.py   Booster-owned custom role creation
    │   ├── update_custom_role.py   Booster custom role edits
    │   ├── create_custom_room.py   Booster private voice-room creation
    │   └── janitor_unboosted.py    Scheduled cleanup after boosts expire
    ├── cotd/random_femboy.py       Random saved image and social metadata lookup
    ├── cultivation/
    │   ├── __init__.py             Cultivation package marker
    │   ├── cultivation.py          Tiên Lộ commands, dashboard, and atomic persistence
    │   ├── _cultivation_ui.py      Owner-only dashboard panels, selects, and buttons
    │   └── _cultivation_helpers.py Pure realms, rewards, market, PvE, and exchange rules
    ├── daily_reward/
    │   ├── daily_action.py         Daily Trap Coin grant and claim tracking
    │   └── user_account.py         Balance, badge, and transaction-history lookup
    ├── economy/
    │   ├── _shop_helpers.py        Catalog ID, price, listing, and reserved-ID helpers
    │   ├── _shop_store.py          Atomic catalog, inventory, and Trap Coin purchases
    │   ├── _shop_rentals.py        UTC entitlement checks, grace migration, and expiry workers
    │   ├── _shop_products.py       Item-type registry for catalog and extra shop cogs
    │   ├── _shop_catalog.py        Built-in sellable Discord role and badge products
    │   ├── _shop_ui.py             Owner-locked interactive shop and inventory panel
    │   ├── shop.py                 Shop hub: interactive catalog, inventory, and admin listings
    │   ├── shop_custom_role.py     Renewable custom roles, designer, expiry and leave cleanup
    │   └── shop_custom_room.py     Renewable private rooms, editor, expiry and leave cleanup
    ├── roles/
    │   ├── _personal_roles.py      Shared resource locks and verified Discord role/room lookup
    │   └── _role_safety.py         Privileged-permission denylist for roles the bot may assign
    ├── discipline/discipline.py    Banned-word listener, logging, warning, and deletion
    ├── funny_things/
    │   ├── meters/
    │   │   ├── _meter_helper.py    Deterministic scores, bars, loading, and embed helpers
    │   │   ├── aura.py             Signed aura score and icon bar
    │   │   ├── redflag.py          Signed red/green flag score and icon bar
    │   │   ├── gay_meter.py        Daily member meter with staged loading
    │   │   ├── penisize.py         Daily member meter with staged loading
    │   │   ├── titansize.py        Daily fictional centimeter-size and cup meter
    │   │   ├── ship_meter.py       Two-member compatibility meter
    │   │   └── based.py, brainrot.py, clown.py, cope.py, cringe.py, delulu.py,
    │   │       gyatt.py, ick.py, les_meter.py, mainchar.py, npc.py, ohio.py,
    │   │       rizz.py, simp.py, skillissue.py, touchgrass.py, yapper.py
    │   │                             Shared-helper-based daily meter commands
    │   ├── birthday/
    │   │   ├── _birthday_ui.py     Owner-only month and day picker view
    │   │   └── birthday.py         Birthday registration and announcement task
    │   ├── cards/femboy_card.py    Member card based on configured role names and guild marriage status
    │   └── tarot/
    │       ├── tarot.py            Tarot command, spread picker, and reading session
    │       ├── _tarot_helpers.py   78-card deck, spreads, draw, and flip state
    │       ├── _tarot_ui.py        Owner-only spread select and per-card flip views
    │       └── _tarot_render.py    Spread cloth using bundled Rider-Waite-Smith card scans
    ├── happy_new_year/
    │   └── happy_lunar_new_year_2026.py
    │                                     Time-limited one-time Lunar New Year greeting
    ├── interaction/
    │   ├── cat.py, dog.py             External animal-image API commands
    │   ├── meme_interaction.py        Static meme response command
    │   ├── user_interaction.py        Social actions, avatar display, and rankings
    │   ├── marriage.py                Propose/divorce/status, couple XP ranks
    │   ├── _marriage_helpers.py       Pure level/rank/XP helpers for marriage
    │   ├── interact_streak.py         Pair streaks from mentions, replies, and shared voice
    │   ├── _interact_streak_helpers.py Pure Vietnam-date, pair, and credit rules for streaks
    │   ├── triggered_reply.py          Persistent phrase-triggered replies
    │   ├── _trigger_reply_helpers.py   Rule parsing and matching helpers
    │   ├── nsfw_interaction.py        Age-gated interactions and rankings
    │   └── nsfw_super_user.py         Role-controlled NSFW lock/unlock workflow
    ├── job_remind/job_remind.py       Persistent timed DM reminders
    ├── minigames/
    │   ├── _playing_cards.py           Shared validated deck and card formatting
    │   ├── _card_game_economy.py       Atomic TC wagers, payouts, refunds, and audit logs
    │   ├── _casino_ui.py               PNG felt tables for Blackjack, Poker, slots, and Sic Bo
    │   ├── _word_game_leaderboard.py   All-time vtv/noitu win ranks from transaction_logs
    │   ├── blackjack/
    │   │   ├── _blackjack_helpers.py   Pure Blackjack scoring and round state
    │   │   └── blackjack.py            Button-driven solo Blackjack against the dealer
    │   ├── poker/
    │   │   ├── _poker_helpers.py       Five-card hand ranking, dealer draw, round state
    │   │   └── poker.py                Button-driven solo five-card draw against the dealer
    │   ├── flip_coin/flip_coin.py     Coin betting against user balances
    │   ├── slot_machine/
    │   │   ├── _slot_helpers.py        Reel symbols and pair/jackpot payouts
    │   │   └── slot_machine.py        Button-driven slot cabinet and Trap Coin settlement
    │   ├── sicbo/
    │   │   ├── _sicbo_helpers.py       Tài/Xỉu/Bộ ba resolution and payouts
    │   │   └── sicbo.py               Button-driven solo Sic Bo against Trap Coin wagers
    │   ├── crocodile_dentist/
    │   │   ├── _crocodile_helpers.py  Pure challenge parsing and game-state transitions
    │   │   └── crocodile.py           Persistent invitations, tooth UI, expiry, and commands
    │   ├── word_connect/word_connect.py
    │   │                                 Persistent Vietnamese word-chain game with TC win rewards and `noitu top`
    │   └── vietnamese_king/vietnamese_king.py
    │                                     Persistent letter-scramble game with TC win rewards and `vtv top`
    ├── mod/
    │   ├── _case_helpers.py         Safe shared case recording and validation
    │   ├── _interaction_ui.py       Shared forms, confirmation guard, and legacy action dispatch
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
    │   ├── purge.py, janitor.py     Direct/form-based, invocation-anchored message cleanup
    │   ├── nickname.py, role.py     Direct argument and confirmed nickname/role workflows
    │   ├── slowmode.py              Slow-mode inspection and guarded overrides
    │   ├── unban.py                     Reply/user-ID unban and reinvite orchestration
    │   ├── warn.py                      Warning commands
    │   ├── verified.py                  Verified role grant/revoke and member self-unverify confirmation
    │   ├── area_51_guard.py             Honeypot channel, cancel view, bans, and reminders
    │   ├── _mrbeast_scam_helpers.py     Photo-dump candidate, caption scorer, 3rd/5th dump ladder
    │   └── mrbeast_scam.py              Cross-channel image-dump timeout and staff decision panel
    ├── nsfw/
    │   ├── __init__.py             NSFW extension package marker
    │   ├── r34.py                       Age-gated Rule34 API search
    │   └── gelbooru.py                  Age-gated Gelbooru API search
    ├── onboarding/
    │   ├── _role_exam_helpers.py       Pure role-exam configuration, validation, and scoring
    │   └── role_exam.py                Staff invitation, private exam UI, and safe role grant
    ├── operation/
    │   ├── bot_status.py                Random activity rotation and temporary Administrator overrides
    │   ├── _bot_status_ui.py            Administrator status panel, activity select, and text/duration modal
    │   ├── _doctor.py                   Shared read-only configuration, permission, and runtime diagnostics
    │   ├── _graceful_shutdown.py        Command admission, drain tracking, and signal helpers
    │   ├── _lifecycle.py                Append-only process/gateway lifecycle event recorder
    │   ├── _operation_helpers.py        Audit ranges, sanitization, and safe CSV generation
    │   ├── _setup_helpers.py           Pure setup-check result and ID helpers
    │   ├── heartbeat.py                 Latency/health command
    │   ├── operation_dashboard.py       Health/audit UI, private Doctor, and Bot owner guild/lifecycle panels
    │   ├── server_stats.py              In-memory uptime and command/error counts
    │   ├── setup_check.py               Manage Guild diagnostic summary using the shared Doctor collector
    │   └── leave.py                     Administrator-controlled guild departure
    ├── settings/variable_setting.py     Mongo-backed runtime variable commands
    └── utils/
        ├── giveaway.py                  Persistent views, entries, scheduling, rerolls, and guild role settings
        ├── _giveaway_helpers.py         Duration/prize parsing, blacklist/bonus weights, and weighted draws
        ├── _giveaway_ui.py              Create form, role selects, and guild settings panel
        ├── vote.py                      Persistent reaction polls and result scheduling
        ├── highlight.py                 Requirements button, replacing startup/post prompts, 💀 listener, chat PNG, TV reply
        ├── _highlight_helpers.py        Skull/interval/prompt-delay knobs, NSFW skip, channel helpers
        ├── _highlight_card.py           Discord dark-theme chat PNG, image gallery, and embed rendering
        ├── _highlight_font.py           Portable meter-block and rainbow-flag drawing with bundled fonts
        ├── _highlight_media.py          Bounded embed snapshots, mention names, and Discord media URLs
        ├── _highlight_text.py           Embed Markdown styles, code blocks, and measured text wrapping
        ├── quote.py                     Text-embed and PNG message quote modes
        ├── _quote_card.py               Quote text wrapping and PNG card rendering
        ├── hash_verify.py               Signature-first femboy-card/quote proof verification
        ├── softotp.py                   Challenge-bound Soft OTP commands and staff verification
        ├── _softotp_helpers.py          Opaque HMAC codes, issuance registry, and lookup
        ├── _softotp_ui.py               Soft OTP panel, get/verify modals, and reveal button
        ├── big_speaker.py               Paid TC big-text re-speak in current channel
        ├── _big_speaker_helpers.py      Size 1–6 → TC cost, mention sanitize, format helpers
        ├── random_member.py             Random guild member selection
        ├── lunch.py                     Owner-only budget/diet picker and animated lunch reveal
        ├── _lunch_helpers.py            Lunch catalog loading, filter parsing, and uniform selection
        ├── _lunch_media.py              Local food-image and Genshin wish attachment helpers
        └── save_image.py                Discord attachment metadata persistence
```

Local `dev_cogs.txt` selects extensions during development. `DISABLED_COGS` can
filter loaded extensions with exact dotted modules or wildcard patterns.
`draft.txt` is a local scratch file.

`!tf softotp` replies with a three-minute owner-only panel and the notice that
only the opener can use it. `softotp get <challenge>` DMs a deterministic
opaque `tfotp1.<key-id>.<unix>.<code>` bound to the current guild, member,
challenge, active key version, and issue time, and upserts that binding in
`softotp_issuances`. `softotp verify` is limited to Administrator or Manage
Server. It rejects tokens whose key ID is not the current active key, so
rotating `CONTENT_VERIFICATION_ACTIVE_KEY_ID` invalidates outstanding Soft
OTPs while old keys can still verify card/quote proofs. With `@user` it
HMAC-confirms the claimed member so another person's OTP is a mismatch;
without `@user` it resolves identity from the registry. It does not scan
members or trust a Discord ID inside the token. Codes reuse the
content-verification HMAC keyring with a separate domain so they cannot be
confused with card/quote proofs.

Lunch owns its catalog and local media loading; it does not use shared bot data,
MongoDB, or external APIs at runtime. `!tf lunch [budget] [chay]` opens a
three-minute owner-only budget/diet panel. Rolls uniformly select from matching
dishes, display the bundled wish GIF, then reveal the food image and catalog
details in the same message. Rerolls preserve filters and avoid the previous
dish when alternatives exist. Price-based animation colors are cosmetic.
The preparation script updates the upstream food snapshot and image sheets
together, merging the separately maintained local food additions. Its `--offline`
mode rebuilds the combined catalog from bundled records and the local additions
without downloading assets. Local image IDs start at 1000 and use `food-extra-*`
sheets; image-generation prompts and wish animations have separate source notes
under `assets/lunch/`.

Tarot composites bundled Rider-Waite-Smith scans from `assets/tarot/` onto the
spread cloth at runtime and does not download card art while handling commands.
`scripts/prepare_tarot_assets.py` refreshes those WebP files from Wikimedia Commons
and crops each scan to the printed card frame. `--from-existing` recrops bundled
files without downloading.

## Persistence Boundaries

MongoDB collections are created lazily. Major groups are:

- Configuration: `global_variables`, `moderation_config`
- Economy: `user_accounts` (including versioned `cultivation` state), `daily_rewards_logs`, `transaction_logs`, `shop_items`, `shop_inventory`, `shop_custom_roles`, `shop_custom_rooms`, `shop_migrations`
- Cultivation audit: append-only `cultivation_events`; TC exchanges also write `transaction_logs`
- Social state: `interactions`, `nsfw_settings`, `images`, `marriages`, `marriage_proposals`, `triggered_replies`, `interaction_streaks`. Pair streaks are unique per guild `(user_a, user_b)` with Vietnam calendar dates (UTC+7); a chain stays live if `last_active_date` is today or yesterday. Current counts of 3, 7, 30, or 100 ping both members in the channel that credited the day
- Soft OTP issuances: `softotp_issuances` stores guild/member/challenge bindings for `tfotp1.<key-id>.<unix>.<code>` tokens. Lookup `_id` is a SHA-256 of guild, challenge, key ID, issue time, and code; the user ID stays out of the token. Verify authenticates only the active HMAC key and does not scan the guild. Unique `(guild_id, user_id, challenge)` prevents duplicate live codes per member
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
- Highlight notice state: `highlight_prompts` uses channel ID as `_id`, with
  `guild_id` and `message_id` for the latest requirements prompt. Prompt replacement
  is serialized within the cog; it deletes the saved notice and any matching bot
  notices in the latest 100 channel messages before posting and saving a replacement.
  Message ownership and the stable requirements button are checked before deletion.
- Scheduling: `tasks`, `votes`, `giveaways`, `giveaway_settings`, `highlight_nominations`, `birthdays`, `birthday_announcements`,
  `bedtime_reminders`. Highlight rows are guild/source-message unique and created when a SFW message first reaches `HIGHLIGHT_THRESHOLD` unique non-bot 💀; they CAS `pending` → `posting` → `posted` before uploading a chat-theme PNG to `HIGHLIGHT_CHANNEL`, with at least `HIGHLIGHT_MIN_INTERVAL_SECONDS` between posts in a guild. NSFW source channels are ignored. Bedtime records are guild/member scoped and hold normalized
  sleep minutes, announcement channel, next UTC deadline, local-date deduplication,
  and audit timestamps; unique guild/member and due-time indexes enforce one schedule
  per member and support the minute scheduler
- AFK and moderation: `afk_reminders`, `afk_pings`, `discipline_logs`, `old_roles`, `warnings`, `moderation_cases`, `mrbeast_scam_logs`, `mrbeast_scam_incidents`. Photo-dump raids count the same author's 1–4 image dumps in a two-minute window: the 3rd dump sends a 30-second confirm button (missed click → 24h timeout), the 5th dump timeouts immediately and opens a persistent staff panel; Ban / Gỡ timeout / Giữ timeout CAS `pending` incidents. Alert channel is `MRBEAST_SCAM_ALERT_CHANNEL` with fallback to `moderation_config.log_channel_id`
- Operations audit: `operation_logs` stores guild-scoped recognized prefix-command outcomes and dashboard export/prune actions; records have no automatic TTL and are removed only through the Administrator dashboard
- Bot lifecycle: `bot_lifecycle_events` stores global, append-only `initial_ready`,
  `reidentified`, and `resumed` events indefinitely. The Bot owner dashboard reads
  the newest 10 events for the current environment. This collection is separate
  from `operation_logs` and is excluded from guild audit browsing, CSV export,
  and pruning
- Shared sequence counters: `feature_counters`
- Games and boosters: card-game wagers, slot spins, Sic Bo bets, and word-game win
  rewards use `user_accounts` plus `transaction_logs` through
  `_card_game_economy.CardGameBank`. Blackjack, Poker, slots, and Sic Bo attach a
  PNG felt table from `_casino_ui.py` to the live Discord panel.
  `vtv top` and `noitu top` rank `vietnamese_king_win` / `word_connect_win` credits
  in that same audit collection. Crocodile Dentist uses `crocodile_games` plus
  guild-scoped IDs from
  `feature_counters` keys named `crocodile_game:<guild_id>`; other state uses
  `context`, `booster_custom_roles`, and `booster_custom_rooms`

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

Bot status uses one interruptible rotation task and an in-memory override timer.
`!tf bot_status` opens an Administrator's panel with an activity-type dropdown,
text/duration modal, random reset, refresh, and close controls. The helper
`cogs/operation/_bot_status_ui.py` owns the UI and rechecks the opening user's
guild and current Administrator permission on every interaction. UI submissions
and prefix shortcuts share the cog's serialized presence updates and global
cooldown. Closing the panel or its three-minute timeout disables controls without
changing the override deadline; bot restart or cog reload ends the override.

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
- Keep the Trap Coin shop hub in `cogs/economy/shop.py`, catalog products in
  `_shop_catalog.py`, and extra paid products such as custom roles in their own
  shop cogs that register through `_shop_products.py`.
- Rental expiry lives in `shop_inventory.expires_at`; the indexed
  `expiry_cleanup_pending` flag tracks deletions needing retry. Each rental cog
  runs a readiness-gated 60-second worker. `_shop_rentals.py` owns the idempotent
  `monthly_custom_roles_v1` grace-period migration and UTC normalization.
- Put reusable feature helpers in a leading-underscore module beside their consumers.
- Put shared static media in `assets/`; put runtime-editable media in Mongo settings.
- Treat large game datasets as generated outputs and update their preparation script with them.
- Add deterministic regression tests under `test/test_*.py`.
- Update this map whenever files move, a new subsystem appears, or ownership changes.
