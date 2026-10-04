# Tablekeeper stage 1 - contract checklist (auditor)

Derived from the stage-1 requirements document only. Each id below is printed by
`run_probes.py` as `PASS <id>` or `FAIL <id>` (sub-checks add a letter or bracket suffix); ids with no probe are marked
**not automated** with the reason. Section references (§) point at the requirements.

Fixture conventions used by the probes (own fixtures, never the shipped samples):

| id | zone | slot | duration | cutoff | hours | tables (capacity) |
|---|---|---|---|---|---|---|
| `r_lumen` | Europe/Berlin | 30 | 90 | 120 | every day but Wed, 18:00-23:00 | `t_duo`(2) `t_quad`(4) `t_six`(6) |
| `r_pier` | Europe/Berlin | 30 | 60 | 60 | every day 18:15-22:15 | `p_1`(4) |
| `r_nacht` | Europe/Berlin | 30 | 90 | 30 | Sun 00:00-06:00 | `n_1`(4) |
| `r_gotham` | America/New_York | 30 | 90 | 30 | Sun 00:00-06:00, Thu 18:00-23:00 | `g_1`(4) |
| `r_zulu` | Etc/UTC | 5 | 5 | 60 | every day 00:00-23:59 | `z_1`(4) `z_2`(4) |

Users `u_hana`, `u_ravi`, `u_mira`. `THU` is the first Thursday at least 21 days
after the run date (far outside every cutoff), `WED` the day before it (closed at
`r_lumen`), `FRI` the day after, `THU2` a week later, `PAST` = 2026-01-15.

## HLT - health and conventions (§3.2, §3.4, §5)

1. **HLT-1** `GET /health` returns 200 with body exactly `{"status":"ok"}`.
2. **HLT-2** `/health` response is `application/json`.
3. **CONV-1** Every response with a JSON body seen during the run has a `Content-Type` starting with `application/json`.
4. **CONV-2** Every 4xx response seen during the run has body `{"error":{"code":<string>,"message":<string>}}`.
5. **CONV-3** No 5xx response is seen anywhere in the run, including under concurrency.
6. **CONV-4** Every reservation timestamp (`starts_at`, `ends_at`, `created_at`) seen is RFC 3339 with an explicit numeric offset (`±HH:MM`, never `Z`).

## RST - reset and seed (§3.3, §4)

7. **RST-1** `POST /_test/reset` with a valid fixture returns 204 with no body.
8. **RST-2** A second reset returns 204 again (repeated resets supported).
9. **RST-3** After reset, `GET /restaurants` lists exactly the fixture restaurants (nothing from the previous fixture).
10. **RST-4** A bearer token issued before a reset is 401 `unauthenticated` after it.
11. **RST-5** A seeded reservation is visible to its `user_id` under the seeded `id` and `reference`, with `status` `confirmed` and the seeded fields.
12. **RST-6** A seeded reservation removes its table from availability for the overlapping slots.
13. **RST-7** Fixture ids of exactly 64 characters (user, restaurant, table) are accepted: reset 204, `GET /restaurants/{id}` 200, login works, a booking on the table returns 201 echoing the ids.
14. **RST-8** Seeded users can log in with the fixture password immediately after reset and receive the fixture `id` and `display_name`.

## AUTH - signup, login, bearer (§6)

15. **AUTH-1** `POST /auth/signup` 201 with `{user_id, display_name, token}`; `display_name` echoed; token non-empty string.
16. **AUTH-2** `POST /auth/login` 200 with the same shape and the same `user_id` as signup.
17. **AUTH-3** Signup with an already registered email (signed-up or seeded) is 409 `email_taken`.
18. **AUTH-4** Password of 7 characters is 422 `validation_failed`; 8 characters is accepted (201).
19. **AUTH-5** Email not of the form `local@domain` (`nodomain`, `@probe.test`, `x@`) is 422 `validation_failed`.
20. **AUTH-6** Wrong password is 401 `unauthenticated`; unknown email is 401 `unauthenticated`.
21. **AUTH-7** `email` of the wrong JSON type (number) is 400 `malformed_request`; unparseable body is 400 `malformed_request`.
22. **AUTH-8** Missing `email` or `password` is 422 `validation_failed`.
23. **AUTH-9** Several tokens per account: two logins return tokens that both work alongside the signup token.
24. **AUTH-10** Every protected endpoint (`POST /reservations`, `GET /reservations`, `GET /reservations/{ref}`, `POST /reservations/{ref}/cancel`, `PATCH /reservations/{ref}`, `POST /reservation-moves`) is 401 `unauthenticated` with no header, with a non-bearer scheme, with a bare `Bearer`, and with an unknown token.
25. **AUTH-11** Public endpoints `GET /restaurants`, `GET /restaurants/{id}`, `GET /availability` work without a token.
26. **AUTH-12** Authentication precedes idempotency: `POST /reservations` with no token and no key is 401, not 400.
27. **AUTH-13** Signup token is usable immediately on a protected endpoint.

## PUB - restaurants and availability (§8)

28. **PUB-1** `GET /restaurants` returns `{"restaurants":[...]}` with exactly the fixture ids, each carrying `id`, `name`, `timezone`.
29. **PUB-2** `GET /restaurants/{id}` returns `slot_minutes`, `reservation_duration_minutes`, `cancellation_cutoff_minutes`, `opening_hours` and `tables` equal to the fixture, tables in fixture order.
30. **PUB-3** `GET /restaurants/{unknown}` is 404 `not_found`.
31. **PUB-4** Availability shape: `restaurant_id`, `date`, `timezone`, `slots`; for `r_lumen` on THU exactly the 8 slots 18:00..21:30 in order; each `starts_at_local` is `YYYY-MM-DDTHH:MM` and `starts_at` matches it with the correct offset.
32. **PUB-5** `available_table_ids` lists tables with `capacity >= party_size` in fixture order: party 4 gives `[t_quad, t_six]`, party 1 all three, party 7 gives `[]` on every slot while the slots still appear.
33. **PUB-6** A closed weekday (WED at `r_lumen`) gives `"slots": []` with the other fields present.
34. **PUB-7** Each of `restaurant_id`, `date`, `party_size` missing is 422 `validation_failed`.
35. **PUB-8** `party_size` query values `1e9`, `4.0`, `+4`, `abc`, `-1`, empty are 422 `validation_failed`.
36. **PUB-9** Invalid `date` (`2026-02-30`, `2026-13-01`, `20261029`, `2026-10-29T00:00`) is 422 `validation_failed`.
37. **PUB-10** Unknown query parameters are ignored (same 200 body).
38. **PUB-11** Slot grid is anchored at `opens`: `r_pier` (18:15 open, 30-min slots, 60-min duration, closes 22:15) yields exactly 18:15, 18:45, ..., 21:15.
39. **PUB-12** Half-open occupancy: a 90-min booking at 19:00 removes the table from 18:00, 18:30, 19:00, 19:30 and 20:00 but not from 20:30, 21:00, 21:30.
40. **PUB-13** Availability for a past date (2026-01-15) is served with the winter offset.
41. **PUB-14** Availability for an unknown `restaurant_id`: **not automated**, the requirements do not say whether this is 404 or 422.

## RES - create reservation (§8 `POST /reservations`, §5)

42. **RES-1** 201 body has every documented field; `status` `confirmed`; `party_size` echoed; `starts_at` equals `starts_at_local` plus the zone offset; `ends_at` is exactly `reservation_duration_minutes` after `starts_at` in absolute time; `reference` matches `^[A-Z0-9]{6,12}$`; `reservation_id` is a string of at most 64 chars.
43. **RES-2** `party_size` as string `"4"`, boolean `true`, float `4.5`, float `4.0`, `0`, `-2` are 422 `validation_failed`.
44. **RES-3** `party_size` one above capacity is 422 `party_exceeds_capacity`; equal to capacity is 201.
45. **RES-4** `starts_at_local` with an offset, with `Z`, with seconds, with a space separator, date only, or garbage is 422 `validation_failed`.
46. **RES-5** `starts_at_local` off the grid (19:10, 19:15) is 422 `not_on_slot_grid`.
47. **RES-6** Start before `opens` (17:30) or start + duration after `closes` (22:00, 23:00) is 422 `outside_opening_hours`; the last valid slot (21:30 + 90 = 23:00) is 201.
48. **RES-7** Booking on a closed weekday is 422 `outside_opening_hours`.
49. **RES-8** Unknown restaurant, unknown table, or a table of another restaurant is 404 `not_found`.
50. **RES-9** `restaurant_id` or `table_id` of the wrong JSON type (number) is 400 `malformed_request`.
51. **RES-10** Unparseable body and a JSON array body are 400 `malformed_request`.
52. **RES-11** Each required field missing is 422 `validation_failed`.
53. **RES-12** Unknown body fields are ignored (201).
54. **RES-13** Overlapping confirmed booking on the same table (same slot, or 19:30 against 19:00) is 409 `table_unavailable`; the first non-overlapping slot (20:30) is 201.
55. **RES-14** A past date is bookable (201) and carries the past date's offset.
56. **RES-15** References are unique across 20 bookings and all match `^[A-Z0-9]{6,12}$`.
57. **RES-16** `GET /reservations/{reference}` returns the identical JSON as the 201 body.
58. **RES-17** The same slot on a different table, or the same table on a different day, is 201 (no false overlap).
59. **RES-18** Malformed body with no `Idempotency-Key` is 400 `malformed_request` (body parsing precedes idempotency, §7).

## IDEM - idempotency (§7, §5)

60. **IDEM-1** Header absent is 400 `missing_idempotency_key`; header present but empty is 400 `missing_idempotency_key`.
61. **IDEM-2** 256-char key is 422 `validation_failed`; 1-char and 255-char keys are 201.
62. **IDEM-3** Replay (same key, same body) is 200 with a body JSON-identical to the original 201; only one reservation exists.
63. **IDEM-4** Replay with different key order and whitespace in the body is still a replay (200, identical).
64. **IDEM-5** Same key, different body is 409 `idempotency_key_reuse`.
65. **IDEM-6** Same key, different body that would itself be invalid (`party_size: "x"`, unknown restaurant) is still 409 `idempotency_key_reuse`.
66. **IDEM-7** Same key and literally the same body on `POST /reservation-moves` (unknown fields ignored on both paths) is a first use there: 201.
67. **IDEM-8** A key whose first use failed with 4xx (422 and 409 cases) is a first use next time: 201.
68. **IDEM-9** Replay after the reservation was PATCHed returns 200 with the original 201 body, and the PATCHed state stays.
69. **IDEM-10** Replay after the reservation was cancelled returns 200 with the original body (status `confirmed`) and the reservation stays cancelled.
70. **IDEM-11** Keys are scoped per user: another user reusing the same key string with a different body gets 201.
71. **IDEM-12** A replay creates no reservation (list count unchanged).
72. **IDEM-13** Idempotency precedes resource checks: replay of a 201 still returns 200 even when the slot is now taken by someone else.

## LIST - listing and reading (§8)

73. **LIST-1** `GET /reservations` for a user with none is exactly `{"reservations":[]}`.
74. **LIST-2** The list contains only the caller's reservations.
75. **LIST-3** The list is ordered by `starts_at` descending.
76. **LIST-4** Cancelled reservations are included with `status` `cancelled`.
77. **LIST-5** Each list entry has the create-response shape and equals `GET /reservations/{reference}`.
78. **LIST-6** Another user's reference and an unknown reference are 404 `not_found` on `GET /reservations/{reference}`.

## CXL - cancel (§8)

79. **CXL-1** Cancel returns 200 with `status` `cancelled`, `reference`, `reservation_id`, `created_at`, `starts_at`, `ends_at` unchanged.
80. **CXL-2** The slot is offered again in availability immediately after cancel.
81. **CXL-3** Cancelling twice is 200 with `status` `cancelled`.
82. **CXL-4** Another user's reference and an unknown reference are 404 `not_found`.
83. **CXL-5** Within the cutoff (start 46-50 min ahead, cutoff 60) cancel is 409 `cutoff_passed`; a booking that already started (past date) is also 409 `cutoff_passed`.
84. **CXL-6** Just outside the cutoff (start 66-70 min ahead, cutoff 60) cancel is 200.
85. **CXL-7** After a failed (cutoff) cancel the booking is still `confirmed`.
86. **CXL-8** After cancel another user can book the freed slot (201).
87. **CXL-9** Cancel exactly at `starts_at - cutoff`: **not automated**; the boundary is a single instant and cannot be hit deterministically from outside the service.

## PAT - amend (§8 `PATCH`)

The requirements do not name the success status for PATCH; the probes expect 200, the status the same section uses for cancel.

88. **PAT-1** Change `starts_at_local`: old slot freed, new slot taken, `ends_at` recomputed, `reference`, `reservation_id`, `created_at` unchanged.
89. **PAT-2** Change `table_id` only; **PAT-3** change `party_size` only; **PAT-4** all three at once.
90. **PAT-5** `party_size` above capacity is 422 `party_exceeds_capacity` and the booking and its occupancy are unchanged.
91. **PAT-6** Moving onto a table/slot held by another confirmed booking is 409 `table_unavailable` and the booking is unchanged.
92. **PAT-7** PATCH of a cancelled reservation is 409 `reservation_cancelled`.
93. **PAT-8** PATCH within the cutoff of the **current** start is 409 `cutoff_passed`, even when the new time is far in the future; a past booking is 409 `cutoff_passed`.
94. **PAT-9** A booking's own occupancy does not block moving it to an overlapping slot on the same table (19:00 -> 19:30).
95. **PAT-10** Another user's reference is 404 `not_found`; unknown table or a table of another restaurant is 404 `not_found`.
96. **PAT-11** Off-grid is 422 `not_on_slot_grid`; outside hours is 422 `outside_opening_hours`; a non-existent local time (spring gap) is 422 `invalid_local_time`.
97. **PAT-12** `party_size` as string is 422 `validation_failed`; `table_id` as number is 400 `malformed_request`.
98. **PAT-13** Unknown fields are ignored; no `Idempotency-Key` is needed.
99. **PAT-14** A no-op PATCH with an empty object: **not automated**, the requirements do not define it.

## TIME - zones and DST (§9)

100. **TIME-1** Europe/Berlin summer (2026-07-16 19:00) is `+02:00`; **TIME-2** winter (2026-01-15 19:00) is `+01:00`.
101. **TIME-3** America/New_York summer (2026-07-16 19:00) is `-04:00`; **TIME-4** winter (2026-01-15 19:00) is `-05:00`.
102. **TIME-5** Berlin spring forward 2026-03-29: availability at `r_nacht` is exactly 00:00, 00:30, 01:00, 01:30, 03:00, 03:30, 04:00, 04:30 (02:00 and 02:30 absent).
103. **TIME-6** Booking 2026-03-29T02:00 or T02:30 at `r_nacht` is 422 `invalid_local_time`.
104. **TIME-7** Booking 2026-03-29T01:30 at `r_nacht` gives `starts_at` `2026-03-29T01:30:00+01:00` and `ends_at` `2026-03-29T04:00:00+02:00` (90 absolute minutes across the gap).
105. **TIME-8** Berlin fall back 2026-10-25: availability is exactly 00:00 .. 04:30 (10 slots) with 02:00 and 02:30 appearing once; slots up to 02:30 carry `+02:00`, from 03:00 `+01:00`.
106. **TIME-9** Booking 2026-10-25T02:30 resolves to the first occurrence: `starts_at` `2026-10-25T02:30:00+02:00`, `ends_at` `2026-10-25T03:00:00+01:00`.
107. **TIME-10** Booking 2026-10-25T01:30 has `ends_at` `2026-10-25T02:00:00+01:00` (post-transition offset, 90 absolute minutes).
108. **TIME-11** Occupancy across the fall-back: after booking 02:30, `n_1` is absent from 01:30 and 02:00, present at 01:00 and 03:00.
109. **TIME-12** New York spring forward 2026-03-08: 02:00 and 02:30 absent from availability; booking 02:00 is 422 `invalid_local_time`; booking 01:30 gives `2026-03-08T01:30:00-05:00` / `2026-03-08T04:00:00-04:00`.
110. **TIME-13** New York fall back 2026-11-01: 01:00 and 01:30 appear once; booking 01:30 gives `2026-11-01T01:30:00-04:00` / `2026-11-01T02:00:00-05:00`; booking 01:00 gives `-04:00` / `2026-11-01T01:30:00-05:00`.
111. **TIME-14** The second occurrence is not bookable: a second booking of the same repeated local time on the same table is 409 `table_unavailable` (it resolves to the same first occurrence).
112. **TIME-15** Spring-gap PATCH target is 422 `invalid_local_time` (covered in PAT-11).

## MOV - atomic reservation moves (§11)

113. **MOV-1** One item with no change fields is 201 with one entry equal to the current booking (no-op retains all values).
114. **MOV-2** `party_size` change via moves is 201 with identity, owner-visible fields and `created_at` unchanged.
115. **MOV-3** Swapping two tables between two bookings is 201, entries in input order with the swapped tables; GET confirms.
116. **MOV-4** Moving a booking to an overlapping slot on its own table is 201.
117. **MOV-5** Overlap with an unlisted booking is 409 `table_unavailable`; the listed booking is unchanged.
118. **MOV-6** Overlap among resulting bookings (an unchanged listed booking retains its occupancy) is 409 `table_unavailable`.
119. **MOV-7** 0 items is 422 `validation_failed`; **MOV-8** 9 items is 422 `validation_failed`; **MOV-9** 8 items is 201 with 8 entries in input order.
120. **MOV-10** Duplicate references are 422 `validation_failed`.
121. **MOV-11** Bookings from two restaurants in one batch are 422 `validation_failed`.
122. **MOV-12** Another user's reference is 404 `not_found` and nothing changes; **MOV-13** unknown reference is 404 `not_found`.
123. **MOV-14** A cancelled booking in the batch is 409 `reservation_cancelled`.
124. **MOV-15** Invalid shape (`moves` missing, not an array, non-object item, non-string reference, item without reference) is 422 `validation_failed`.
125. **MOV-16** No token is 401; missing key is 400 `missing_idempotency_key`; 256-char key is 422 `validation_failed`.
126. **MOV-17** Atomicity: one failing item leaves every listed booking, availability and the idempotency key untouched; the key is reusable afterwards (201).
127. **MOV-18** Error precedence is input order: `[party_exceeds_capacity, not_on_slot_grid]` reports the first; reversed reports `not_on_slot_grid`.
128. **MOV-19** Non-occupancy errors precede occupancy errors regardless of position.
129. **MOV-20** Cutoff precedes other errors for the same booking (409 `cutoff_passed` for a within-cutoff booking with an invalid party); for an earlier listed booking, its own error wins; a within-cutoff booking with a valid change is still 409 `cutoff_passed`.
130. **MOV-21** Replay is 200 with the original 201 body even after the bookings changed again.
131. **MOV-22** Same key, different body is 409 `idempotency_key_reuse`.
132. **MOV-23** `starts_at_local` change via moves recomputes `starts_at`/`ends_at` and frees the old slot.

## CONC - concurrency (§1, §5, §7)

133. **CONC-1** 24 simultaneous bookings for one table and slot with distinct keys: exactly one 201, the rest 409 `table_unavailable`, no 5xx, one reservation in the list.
134. **CONC-2** 24 simultaneous identical requests with one unused key: exactly one 201, the rest 200 with the identical body, one reservation created.
135. **CONC-3** A reservation-move racing 16 direct bookings for the same target table/slot: exactly one success, every other attempt 409 `table_unavailable`, the table never double-booked.

## RT - export and import (§10)

136. **RT-0** Setup guard: the post-export mutations (cancel, PATCH, booking, signup, batch) all succeeded before import.
136. **RT-1** `GET /_test/export` is 200 JSON with `track` `"tablekeeper"`, `format_version` `1` and `state` an object.
137. **RT-2** Export is read-only: the caller's list is identical before and after export.
138. **RT-3** `POST /_test/import` of an unchanged export is 204.
139. **RT-4** After mutations (cancel, PATCH, new booking, new signup, new batch) and import: the original token still works and the caller's list equals the pre-export list exactly (ids, references, statuses, timestamps).
140. **RT-5** Original logins (seeded and signed-up) still work after import.
141. **RT-6** Post-export mutations are gone: cancelled booking is `confirmed` again, PATCHed fields restored, new booking is 404, new user's token is 401 and login 401.
142. **RT-7** Original idempotent replays return 200 with the original bodies after import, on both `/reservations` and `/reservation-moves`.
143. **RT-8** A key whose request failed before export is still a first use after import (201).
144. **RT-9** A key first used after export is a first use again after import (201).
145. **RT-10** Imported reservations occupy their slots in availability.
146. **RT-11** Importing the same export twice does not duplicate: list identical to the pre-export list.
147. **RT-12** Wrong `track`, wrong `format_version`, missing `state`, non-object `state` are 422 `validation_failed` and the destination is unchanged.
148. **RT-13** Invalid JSON on import is 400 `malformed_request` and the destination is unchanged.
149. **RT-14** An export with an extra unknown top-level field still imports (unknown fields ignored).
150. **RT-15** Reset after import clears everything: imported token 401, imported login 401, restaurants replaced.
151. **RT-16** Export is a snapshot: a booking made after the export is absent once the export is imported (covered by RT-6).
