# Singer session provenance + smarter Merge — plan

**Date:** 2026-08-13
**Branch:** `feat/singer-session-provenance`

## Motivation

Live-show investigation (the "two Chailas" case): the KJ Singers list can show the
same person twice when one name is a **self-registered singer** (own phone session)
and the other is a **duet-partner label** typed into someone else's submission, or a
**KJ-hand-added** entry. Today nothing in the UI distinguishes these, and Merge is a
bare name dropdown that doesn't say which singer is retained or what history moves.

## Goals

1. Show a **phone/device icon** beside singers who have a real device session
   (they personally submitted ≥1 request from the singer UI). Duet-partner labels and
   KJ-added singers get no icon.
2. Clicking the icon opens a **device-details popup** with whatever we captured
   (parsed browser/OS/device + raw User-Agent, phone on file, first submitted,
   request count, source types).
3. Replace the inline Merge dropdown with a proper **modal**: searchable/scrollable
   singer list, clear "who is retained" preview, a real-device warning + Swap
   direction, and an explicit confirm.

## Data model

- **New column** `sing_requests.user_agent TEXT` (additive migration in
  `SingStore.init_schema`, mirrors `sing_push_subscriptions.user_agent`).
- `SingStore.create_request(..., user_agent=None)` stores it.
- `sing.py` `/submit` and `/requests/<id>/change` pass
  `request.headers.get("User-Agent", "")[:500]`.
- Client Hints (Sec-CH-UA-Model) deferred — the singer PWA is usually a single
  visit, so `Accept-CH` wouldn't be honoured on the first load. UA parsing +
  raw string is enough for v1.

## Backend — session enrichment

- New `ua_parse.py`: pure `parse_user_agent(ua) -> {browser, os, device, is_mobile}`
  (heuristic, best-effort, always keeps raw). Unit-tested.
- `SingStore.get_requests_for_entries(entry_ids, night_started)` → linked requests
  (id, linked_entry_id, singer_name, phone, user_agent, created_at, source_type,
  additional_singers), night-scoped (mirrors the SMS phone lookup guard).
- `routes._add_singer_session_info(singer_stats, app)` decorator, called alongside
  `_add_last_sang_to_singer_stats` in `get_rotation` and `_singer_action_response`.
  Per singer it attaches:
  ```
  session: {
    origin: 'singer_ui' | 'duet_partner' | 'kj_added',
    has_device: bool,           # true only for singer_ui
    phone: str,                 # from newest own request (may be '')
    device: {browser, os, device, is_mobile, raw},  # newest own request UA
    request_count: int,
    first_request_at: str|null,
    sources: [str, ...],        # distinct source_types
  }
  ```
  Classification: match the singer's rotation-entry ids to linked requests.
  - own request whose `singer_name` == singer name → `singer_ui` (has_device).
  - else appears in a linked request's `additional_singers` → `duet_partner`.
  - else no linked request → `kj_added`.

## Frontend

- `buildSingerRow`: when `session.has_device`, insert a 📱 icon button after the name
  → `openSingerDeviceModal(singer)`.
- `openSingerDeviceModal`: reuses the songs-modal backdrop pattern; shows parsed
  device, phone, first-seen, request count, sources, and raw UA (monospace).
- Replace `showMergeDropdown` → `openMergeModal(singer)`:
  - search box + scrollable list of all other singers (self excluded); each option
    shows name + 📱 badge + sung/queued counts; done singers de-emphasised.
  - selecting a target reveals a **confirmation panel**: keeper badge on the retained
    name, combined-history sentence, real-device warning when merging a phone-linked
    singer into a non-linked one, **Swap direction**, and Confirm.
- CSS for icon, device modal, merge modal (dark theme, existing tokens).

## Tests

- `ua_parse` unit tests (iOS Safari, Android Chrome w/ model, desktop, junk).
- Migration idempotency + UA persisted on submit (endpoint test).
- Session classification: singer_ui / duet_partner / kj_added on a seeded rotation.

## Docs

- `docs/ARCHITECTURE.md` (session provenance flow), `kj-controller/docs/CHANGELOG.md`,
  version bump.
```
