# "Your songs tonight" shows prior-night songs — cross-night leakage

**Date:** 2026-06-11 (reported live, mid-show, by singer "Angelina S")
**Severity:** High (user-facing correctness bug at a live event)
**Status:** Root cause confirmed with production evidence; fix implemented, awaiting deploy approval.

## Symptom

After requesting a song, Angelina's singer page ("done" screen) listed **4** songs under
**"Your songs tonight"** — but she only added **one** tonight:

| Song (as shown) | Status shown |
|---|---|
| Ordinary — Train | Added to the queue. |
| Something In The Orange — Zach Bryan | Added to the queue. |
| Feeling Good — Nina Simone | Added to the queue. |
| Whatever Lola Wants — Sarah Vaughan | You're #10 — about 36–42 min |

## Root cause (confirmed against prod `~/kjdata/rotation.db`, read-only)

All four rows belong to **Angelina S herself**, all under event token **`2121`**:

| id | created_at | song | linked_entry_id |
|----|-----------|------|-----------------|
| 110 | **2026-05-28** | Ordinary — Train | 44 |
| 111 | **2026-05-28** | Something In The Orange | 45 |
| 116 | **2026-05-29** | Feeling Good — Nina Simone | 64 |
| 179 | **2026-06-11** (tonight) | Whatever Lola Wants | 82 |

Token `2121` has been the **same across ~11 nights** (Apr 30 → Jun 11). It is never rotated.

The leak is the product of two facts:

1. **The event token is never rotated between nights.** `RotationManager.archive_rotation()`
   (New Rotation) archives entries and advances `night_started_at`, but does **not** touch
   `request_token`. KJs also pin a memorable code via `set_token`, so in practice the token is
   effectively permanent.
2. **`/sing/status` and `/sing/my-requests` scope only by token — never by night.** Both endpoints
   return any request whose stored `token` equals the current token, with no `created_at` filter.

The singer page remembers her submitted request ids in `localStorage` keyed by token
(`sing_my_request_ids`). The client comment explicitly assumes *"we scope by token so that
yesterday's ids don't leak into tonight's event"* — but that assumption is false because the token
doesn't change between events. Angelina's device still held ids `110, 111, 116` from her May visits;
under the still-current token `2121` they resolve and render as "tonight." Their `linked_entry_id`
points to archived rotation entries (not in tonight's rotation) → no estimate → the
"Added to the queue." fallback text.

Note this is **not** a cross-*singer* leak: `sing_requests.id` is `AUTOINCREMENT` and is never
recycled (`archive()` deliberately preserves the counter — the "Connie" cross-night id-reuse fix),
and `localStorage` only ever holds ids the device itself submitted. So a singer can only ever see
**her own** old rows. Still a real correctness bug.

## Fix

Night-scope the two singer-facing read endpoints — exactly the defense-in-depth already applied to
phone/push resolution (`routes.py` / `app.py`: `created_at >= night_started_at`, failing CLOSED when
the marker is unset; `ensure_night_started()` guarantees it on boot).

- `sing.py`: add `_belongs_to_current_night(store, req)` and apply it after the token check in
  `status()` (→ 404) and inside the `my_requests()` loop (→ skip).
- `static-sing/sing.js`: cap the ids sent to `/sing/my-requests` to `MY_REQUESTS_MAX` (20) so a
  returning singer who has accumulated >20 ids under a persistent token never trips the 400 cap and
  loses tonight's list.

### Rejected alternative: rotate the token on New Rotation

Would also fix it, but it's a bigger behavior change — KJs print/announce a stable code, and rotating
mid-archive would invalidate codes singers are mid-using. Night-scoping is the smaller, established,
consistent fix and matches how phone resolution already handles the identical id/token-reuse class.

## Verification

Unit/integration tests assert prior-night rows are dropped from both endpoints even when the token
matches, and that the marker-unset case fails closed. Production confirmation: re-query after deploy
that `/sing/my-requests` for Angelina's ids returns only id 179.
