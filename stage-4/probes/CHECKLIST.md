# Tablekeeper stage 3 - contract checklist (auditor)

Part A keeps every stage-1 item and Part B every stage-2 item (both still in force). Part C adds the stage-3 items.

# Part A - stage 1

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

# Part B - stage 2 additions

Stage-2 fixture used by the new groups: restaurant `r_combo` (Europe/Berlin, slot 30, duration 90,
cutoff 120, Thu-Sun 18:00-23:00) with tables `c_a` Alcove (2), `c_b` Bay (2), `c_c` Corner (4),
`c_d` Den (6) and `combinable: [["c_b","c_a"],["c_b","c_c"]]`. Pair Bay+Alcove seats 4, Bay+Corner
seats 6; Alcove+Corner and Alcove+Den are not combinable whatever their capacities. The stage-1
restaurants are kept unchanged (no `combinable`) so every stage-1 group still runs as written.
In stage 2 every reservation body must carry `table_ids` and must carry `table_id` exactly when
the set has one member; the shared shape validator enforces this in every stage-1 group too.

## CFX - fixture (stage-2 Model)

152. **CFX-1** A fixture with `combinable` pairs is accepted (204); a stage-1-shaped fixture (no `combinable`) is still accepted; a restaurant without `combinable` lists singles only in `available_options`.
153. **CFX-2** A `combinable` entry that is a triple, a singleton, an unknown table id, a pair with a duplicated id, or a table of another restaurant is rejected 422 `validation_failed` and the previous state (tokens, bookings) is unchanged.
154. **CFX-3** A seeded reservation with `table_ids` (pair) is visible with `table_ids` and no `table_id`, and blocks both members and every pair containing either.
155. **CFX-4** A seeded reservation with `table_id` is visible with one-element `table_ids` and `table_id`.
156. **CFX-5** A seeded reservation with `status: "cancelled"` is visible as `cancelled` and does not block availability.
157. **CFX-6** `GET /restaurants/{id}` after a stage-2 fixture still returns the stage-1 fields equal to the fixture; whether `combinable` is echoed is **not asserted** (the enumerated field list in §8 does not name it).

## CAV - availability with options (stage-2 API)

158. **CAV-1** `available_table_ids` contains single tables only, exactly as in stage 1.
159. **CAV-2** `available_options` lists singles first in fixture order, then pairs in `combinable` order, each pair's `table_ids` in `combinable` order (`["c_b","c_a"]`).
160. **CAV-3** Option `capacity` is the table capacity for singles and the sum for pairs: party 4 yields `[c_c 4, c_d 6, c_b+c_a 4, c_b+c_c 6]`, party 5 yields `[c_d 6, c_b+c_c 6]`, party 7 yields `[]` while the slot still appears.
161. **CAV-4** A pair disappears when any member is taken: a single booking on `c_a` removes `c_b+c_a` but not `c_b+c_c`.
162. **CAV-5** A pair booking blocks each single member and every pair sharing a member; the half-open rule applies to the pair.
163. **CAV-6** Combining is not transitive: `c_a+c_c` never appears; unlisted pairs never appear whatever the capacities.
164. **CAV-7** A party that fits only a pair (party 5 with `c_d` taken) sees exactly that pair.
165. **CAV-8** `available_options` is present on every slot, including slots with no option (`[]`).

## CRS - create with table sets (stage-2 `POST /reservations`)

166. **CRS-1** `table_ids` pair is 201 with `table_ids` equal to the pair as a set and no `table_id`.
167. **CRS-2** Legacy `table_id` is 201 with one-element `table_ids` and `table_id`; one-element `table_ids` also yields `table_id`.
168. **CRS-3** Both `table_id` and `table_ids` present is 422 `validation_failed`, even when they agree.
169. **CRS-4** Duplicate id in `table_ids` is 422 `validation_failed`; empty `table_ids` is 422 `validation_failed`.
170. **CRS-5** Three ids are 422 `combination_not_allowed`.
171. **CRS-6** An unlisted pair is 422 `combination_not_allowed` whatever the capacities (`c_a+c_c`, `c_a+c_d`, `c_c+c_d`).
172. **CRS-7** Party above the summed capacity is 422 `party_exceeds_capacity`; equal to the sum is 201.
173. **CRS-8** Any member taken is 409 `table_unavailable`: a single on `c_a` blocks pair `c_b+c_a`; pair `c_b+c_c` blocks singles `c_c` and `c_b` and pair `c_b+c_a`, while `c_a` stays bookable.
174. **CRS-9** `table_ids` of the wrong JSON type (string) is 400 `malformed_request`.
175. **CRS-10** Every response shape carries `table_ids` and `table_id` only for one-member sets: create, list, get, cancel, PATCH, moves.
176. **CRS-11** Cancelling a pair booking frees every member: both singles and the pair reappear.
177. **CRS-12** Overlap on a pair booking follows the half-open rule (the adjacent slot is 201).

## CID - idempotency with table sets (§7)

178. **CID-1** Replay of a pair booking is 200 with the identical body; one reservation exists.
179. **CID-2** Same key with a different pair is 409 `idempotency_key_reuse`.
180. **CID-3** `{"table_id": "c_d"}` and `{"table_ids": ["c_d"]}` are different JSON bodies: same key gives 409 `idempotency_key_reuse`.
181. **CID-4** Replay after the pair booking was cancelled is 200 with the original (confirmed) body.
182. **CID-5** A key that failed with 422 `combination_not_allowed` is a first use next time.

## CPT - PATCH with table sets

183. **CPT-1** PATCH `table_ids` from a single to a pair is 200; the response has the pair and no `table_id`; the old table is freed and both members taken.
184. **CPT-2** PATCH back to a single via `table_id` is 200 with `table_id`; via one-element `table_ids` likewise.
185. **CPT-3** PATCH with both `table_id` and `table_ids` is 422 `validation_failed`.
186. **CPT-4** PATCH to an unlisted pair or to three tables is 422 `combination_not_allowed`.
187. **CPT-5** PATCH to a pair with a taken member is 409 `table_unavailable` and the booking is unchanged.
188. **CPT-6** PATCH party above the pair's summed capacity is 422 `party_exceeds_capacity`.
189. **CPT-7** A pair booking's own occupancy does not block moving to another pair sharing a member (`c_b+c_a` to `c_b+c_c`, same slot).
190. **CPT-8** Moving a pair booking to another slot frees both members at the old slot.

## CMV - moves with table sets (§11)

191. **CMV-1** A move item with `table_ids` is 201 and the entry carries the pair.
192. **CMV-2** A swap involving a pair (A `c_b+c_a` to `c_b+c_c`, B `c_c` to `c_a`, same slot) is 201 in input order.
193. **CMV-3** Overlap among resulting bookings through a shared member is 409 `table_unavailable` and nothing changes.
194. **CMV-4** An unlisted pair in a move is 422 `combination_not_allowed` and nothing changes.
195. **CMV-5** Both `table_id` and `table_ids` in one item is 422 `validation_failed`.
196. **CMV-6** Replay of a pair batch is 200 with the original body.
197. **CMV-7** Overlap with an unlisted pair booking through one member is 409 `table_unavailable`.

## CCN - concurrency with table sets (stage-2 "Concurrent bookings and amendments")

198. **CCN-1** 24 distinct-key requests racing for one pair and slot: exactly one 201, the rest 409 `table_unavailable`, zero 5xx, one reservation.
199. **CCN-2** 24 mixed requests racing for the pair, each member single and the overlapping pair: zero 5xx, at least one 201, every refusal 409 `table_unavailable`, and afterwards no table appears in two overlapping confirmed bookings.
200. **CCN-3** 20 concurrent bookings, pair bookings and PATCHes onto one table and slot: zero 5xx, exactly one success, every refusal 409 `table_unavailable`, and the final state has no table in two overlapping confirmed bookings.
201. **CCN-4** 24 identical pair requests with one unused key: exactly one 201, the rest 200 identical, one reservation.

## UPG - upgrade from a stage-1 export (stage-2 "Existing clients after an upgrade")

The probes import `fixtures/stage1_export.json`, a genuine export captured from the accepted
stage-1 image (revision 797afdd) after seeding `r_combo` without `combinable` plus one seeded
booking, logging in, signing up a user, booking twice, running a batch, cancelling once and
failing one key. `fixtures/stage1_export_expected.json` holds the tokens, keys, bodies and
original responses. Both files contain only throwaway test credentials for fixture users.

202. **UPG-1** Importing the stage-1 export (format_version 1, `table_id`-only reservations, no `combinable`) is 204.
203. **UPG-2** The stage-1 bearer tokens (login and signup) still work; the seeded and signed-up logins still work; a stage-2 token issued before the import is 401 (import removes previous credentials).
204. **UPG-3** The caller's list equals the stage-1 list: ids, references, statuses and timestamps identical, each entry gaining `table_ids` equal to `[table_id]`.
205. **UPG-4** Lookup by the seeded and the created references works; the cancelled one is still `cancelled`; the batch-amended party size is retained.
206. **UPG-5** The original idempotent replays hold on both paths: 200 with the original body, either identical or identical plus `table_ids == [table_id]`.
207. **UPG-6** The failed stage-1 key is a first use (201); a stage-1 key with a different body is 409 `idempotency_key_reuse`.
208. **UPG-7** Imported reservations occupy their tables in availability; with no `combinable` in the imported state a pair booking is 422 `combination_not_allowed`.
209. **UPG-8** Reset after the upgrade import clears everything (stage-1 token 401).
210. **UPG-9** A stage-2 export containing a pair booking round-trips: import restores it with `table_ids` and its replay.

## UI - browser (stage-2 screens; Playwright, headless Chromium)

211. **UI-1** `/`, `/signup`, `/login`, `/lookup` answer 200 with `text/html`, and loading each makes no request to any host other than the service; no browser flow in the run contacts another host.
212. **UI-2** Signup through the screen signs in: `current-user` text contains the display name.
213. **UI-3** `current-user` is visible on every route while signed in; `logout-button` hides it.
214. **UI-4** `auth-error` is absent before any error; login with a wrong password shows `auth-error`; a correct login shows `current-user`.
215. **UI-5** After a search the grid has exactly one cell `slot-{table_id}-{HH:MM}` per table per slot, and `data-available` equals membership in `available_table_ids` for the searched party size.
216. **UI-6** Combination cells `slot-{t_a}+{t_b}-{HH:MM}` use `combinable` order and are `data-available="true"` exactly for pairs in `available_options`; a pair not available is absent or `false`.
217. **UI-7** A closed day shows `no-slots` and no `availability-grid`.
218. **UI-8** Clicking an unavailable cell opens no `booking-form` (a disabled cell is compliant; the probe clicks without actionability checks); clicking an available cell while signed out shows `auth-error` or navigates to `/login`.
219. **UI-9** Clicking an available cell while signed in opens `booking-form` with `booking-party-size` equal to the searched size and `booking-summary` naming the table label and the local start time.
220. **UI-10** Submitting shows `confirmation` with `confirmation-reference` exactly the reference known to the API and `confirmation-details` containing restaurant name, table label and local start.
221. **UI-11** A combination booking: `booking-summary` names both labels, `confirmation-tables` contains both labels, the API reservation holds both ids.
222. **UI-12** Out-of-order: search A is held until after search B completes; the grid and the open form describe B; A's late response does not restore A.
223. **UI-13** 409 path: the table is taken through the API after the form opened; submit shows `booking-error`, the cell is refreshed to `false`, `booking-form` and `booking-party-size` are preserved, no `confirmation`, no `booking-uncertain`.
224. **UI-14** Lost response after commit: `booking-uncertain` nonempty, no `booking-error`, no `confirmation`; resubmitting sends the same `Idempotency-Key` and body; success clears the uncertainty and shows the original reference; exactly one reservation exists.
225. **UI-15** Lost request before reaching the server: same recovery, same key and body on retry, one reservation.
226. **UI-16** `booking-error` and `booking-uncertain` are never shown together (asserted on the 409 and lost-response paths).
227. **UI-17** Resubmitting an unchanged successful form shows the same `confirmation-reference`, no `booking-error`, no new reservation; after a field changes the next submission is a new request with a new key and, once the slot is free again, a new reference.
228. **UI-18** Lookup: found shows `reservation-detail`, `reservation-status` exactly `confirmed`, `reservation-tables` with every label for a pair; `reservation-cancel-button` cancels, status becomes exactly `cancelled`, the button disappears.
229. **UI-19** Lookup of an unknown or another user's reference shows `reservation-error`; a cancel refused by the cutoff shows `reservation-error` and the status stays `confirmed`.
230. **UI-20** Upgrade between browser requests: after export, mutation and import, the page without reload still shows `current-user`; a retry pending from a lost response taken before the export recovers the original `confirmation-reference`; lookup of a retained reference then works through the lookup screen.
231. **UI-21** At 375 px and 1280 px no route and no grid produces horizontal scrolling (`scrollWidth <= clientWidth`).
232. **UI-22** Every visible input and select has a visible associated label (`<label for>`, wrapping label, `aria-label` or `aria-labelledby`).
233. **UI-23** Keyboard focus is visible: tabbing changes the focused element's outline, box-shadow, border or background.
234. **UI-24** Available, unavailable and selected cells have distinguishable computed styles.
235. **UI-25** A loading indicator exists while a search is pending (accepted signals: `aria-busy`, a testid containing `loading`, `role=status|progressbar`, a disabled search button, or visible text matching loading/searching/looking).
236. **UI-26** Contrast and overall visual quality: **not automated** (judgement).
237. **UI-27** Recovery across a full page reload: **not automated** (not required by the documents).

# Part C - stage 3 additions

Stage-3 fixture (`s3_fixture`): the stage-2 fixture plus users `u_mgr` and `u_mgr2`, restaurant
`r_pol` (Europe/Berlin, slot 30, duration 90, cutoff 120, every day 18:00-23:00; tables `p_a`
Arbour 2, `p_b` Bench 4, `p_c` Cellar 6; `combinable [["p_b","p_a"]]`; `manager_user_ids
["u_mgr"]`), restaurant `r_pol2` (one table `q_1`, manager `u_mgr2`), and `u_mgr` as manager of
`r_zulu` for cutoff timing. Dates: `DAY(n)` = THU + n days. Restaurant revision is read from
`GET /restaurants/{id}` field `revision` per the coordinator's decision (starts at 1 at reset or
import; +1 per successful state-changing operation on that restaurant; never by failures or
replays).

Every reservation body in every group (stage 1 and 2 included) is checked by the shared validator:
it must carry a positive integer `revision` and `accepted_terms` with exactly the keys
`policy_version, slot_minutes, reservation_duration_minutes, cancellation_cutoff_minutes,
opening_hours, capacities` (no `effective_from`), and `ends_at - starts_at` must equal the accepted
duration. CXL-1 now expects cancel to return the booking with `revision + 1` (the one stage-2 check
that stage 3 legitimately changes).

## XPL - availability explanations

238. **XPL-0** `explain=true` is 200 and every slot carries an `explain` list.
239. **XPL-1** Every table of the restaurant appears exactly once per slot, in fixture order.
240. **XPL-2** Both rules are reported for every table, `capacity` then `no_overlap`, each `holds` a boolean; no rule omitted.
241. **XPL-3** `available` is a boolean and is true exactly when both rules hold.
242. **XPL-4** The table ids whose `available` is true equal `available_table_ids`, in the same order, on every slot.
243. **XPL-5** `policy_version` is the integer 0 for every table before any publication.
244. **XPL-6** Exact rule values: party 3 fails `capacity` on `p_a` (2); 19:00 bookings fail `no_overlap` at 18:00, 19:00 and 20:00 and hold at 20:30 (half-open); a table excluded by both reports both false.
245. **XPL-7** Without `explain` no slot carries an `explain` key and the response is otherwise identical to the explained one (stage 1/2 shape kept, including `available_options`).
246. **XPL-8** `explain` values `false`, `1`, empty, `TRUE`, `True`, `yes`, `0`, `true ` (trailing space) are each 422 `validation_failed`.
247. **XPL-9** A closed day with `explain=true` still returns `"slots": []`.
248. **XPL-10** A party no table seats (7): every slot still appears with `available_table_ids []` and a full explain whose capacity rule is false for every table.
249. **XPL-11** Under a published policy `explain` shows that policy's `policy_version` and its capacities decide the capacity rule (`p_a` raised to 8 seats party 3).
250. **XPL-12** The day before that policy's effective date still explains `policy_version 0`.
251. **XPL-13** Unknown query parameters next to `explain` are ignored.

## POL - publishing policies

252. **POL-1** Publish without a token is 401 `unauthenticated`.
253. **POL-2** Publish for an unknown restaurant is 404 `not_found`.
254. **POL-3a** An authenticated diner (non-manager) is 403 `forbidden`.
255. **POL-3b** The manager of another restaurant is 403 `forbidden` here.
256. **POL-3c** A restaurant without `manager_user_ids` (default `[]`) refuses everyone with 403.
257. **POL-4a/b/c** Missing key 400 `missing_idempotency_key`; empty key 400; 256-character key 422 `validation_failed`.
258. **POL-5** `GET /restaurants/{id}/policies` is public and exactly `{"policies": []}` before any publication; **POL-5b** unknown restaurant is 404.
259. **POL-6** First publication is 201 with every supplied field echoed plus integer `policy_version 1`.
260. **POL-7** Replay (same key, same body) is 200 with an identical body.
261. **POL-8a/b** Same key with a different body is 409 `idempotency_key_reuse`, also when the new body is itself invalid.
262. **POL-9** Each of these is 422 `validation_failed`: each of the six fields missing; `effective_from` `2026-02-30`, `2026-9-1`, a datetime, empty, free text, `20261231`, `2026-13-01`; `slot_minutes` 0, 1441, -30, 30.5, true; duration 0, 1441, -1, 90.5, true; cutoff -1, 10081, 0.5, true, false; opening hours with a duplicate weekday, crossing midnight, opens equal to closes, unknown weekday, hour 25, minute 60; capacities missing a table, naming an extra table, naming another restaurant's table, capacity 0, 101, -1, 2.5, true.
263. **POL-10** Wrong JSON types (`slot_minutes` as a string, `capacities` as a list, `opening_hours` as an object, `effective_from` as a number) are refused with 400 `malformed_request` or 422 `validation_failed` (the documents give both rules; either is accepted).
264. **POL-11** Failed publications and the replay allocate no version and change no state (one policy listed; availability unchanged).
265. **POL-12** The next successful publication is version 2.
266. **POL-13** Versions are per restaurant: the first `r_pol2` policy is version 1.
267. **POL-14** Unknown fields are ignored (201, version 3).
268. **POL-15** `GET` policies without a token lists versions 1, 2, 3 in publication order, each equal to its 201 body; policy 0 is omitted.
269. **POL-16** `r_pol2` lists only its own policy.
270. **POL-17** `GET /restaurants/r_pol` still returns the original fixture configuration.
271. **POL-18** Boundaries accepted with consecutive versions: `slot_minutes` 1 and 1440, duration 1 and 1440, cutoff 0 and 10080, capacities 1 and 100, hours 00:00-23:59, no opening hours at all, an effective date in the past.
272. **POL-19** Earlier policies are immutable: versions 1-3 still equal their 201 bodies after later publications.
273. **POL-20** A policy with no opening hours closes every day it selects.

## PSEL - policy selection

274. **PSEL-1** A policy published later with a past effective date selects for THU (greatest `effective_from` not later than the date): grid 30, `p_a` capacity 1 excludes party 2; explain shows version 2.
275. **PSEL-2** On its effective date version 1's grid (60), duration (120), hours (17-22) and capacities (`p_a` 3) decide the slot list and tables.
276. **PSEL-3** The day before an effective date still uses the older policy (publication order differs from date order; a future policy does not affect today).
277. **PSEL-4a/b/c** Under version 1: 19:30 is 422 `not_on_slot_grid`; 21:00 (+120 > 22:00) is 422 `outside_opening_hours`; 16:00 is 422 `outside_opening_hours`.
278. **PSEL-5** Party 3 on `p_a` at 17:00 under version 1 is 201, ends 120 minutes later, revision 1; **PSEL-5b** its `accepted_terms` equal version 1's rules without `effective_from`.
279. **PSEL-6a/b** Party 2 on `p_a` on THU (version 2 capacity 1) is 422 `party_exceeds_capacity`; a THU booking accepts version 2's terms.
280. **PSEL-7** Two policies with the same effective date: the greater `policy_version` decides (30-minute end); **PSEL-7b** explain shows it.
281. **PSEL-8a/b/c** A weekday absent from the selected policy's opening hours is closed (`slots []`, booking 422 `outside_opening_hours`); the listed weekday stays open.
282. **PSEL-9a/b/c** Publishing a shorter duration leaves an existing booking's body, `ends_at`, revision, terms, history and decision unchanged, and its own 90-minute occupancy still blocks under the 30-minute policy (free at 18:30 and 20:30).
283. **PSEL-10a/b/c** A booking under a 30-minute policy ends 30 minutes later; a later 120-minute policy keeps that end, and the new 120-minute slots overlap it correctly (blocked 19:00-20:30, free 18:00, 18:30, 21:00).
284. **PSEL-11** The restaurant detail still returns the fixture configuration after seven publications.

## TRM - accepted terms and revision

285. **TRM-1** A seeded booking has revision 1 and policy-0 terms equal to the fixture rules.
286. **TRM-2** A new booking has revision 1 and exactly the six policy-0 fields; **TRM-2b** list entries carry them.
287. **TRM-3** A publication changes no existing booking.
288. **TRM-4/4b** A real amendment to a version-1 date: 200, revision 2, 120-minute end, version 1 terms (the resulting date's policy).
289. **TRM-5/5b** Amending back to a policy-0 date: revision 3, 90-minute end, policy-0 terms.
290. **TRM-6** Replaying the creating key returns the original body (revision 1, original terms) after amendments.
291. **TRM-7** A no-op amendment (all three fields re-sent) is 200, retains body, revision and terms, and records no history.
292. **TRM-7b** A no-op amendment after a new policy for its date still retains the old terms and end.
293. **TRM-8/8b** A real amendment on the same date adopts the new same-date policy: revision 4, 60-minute end, version 2 terms.
294. **TRM-9a/b/c/d** Resulting fields are validated against the resulting date's policy (grid, closing, capacity: each 422 with its code) and failed amendments change nothing (body, history, availability).
295. **TRM-10a/b/c** A pair's capacity is the sum of the selected policy's capacities: party 3 on a 1+1 pair is 422, party 2 is 201 with version 3 terms, moving a party-5 pair onto a version-3 date is 422 `party_exceeds_capacity`.
296. **TRM-11** A reversed pair names the same set: 200 no-op, revision unchanged, no history.
297. **TRM-12/12b** Cancel increments the revision once; a repeated cancel returns the same body.
298. **TRM-13** A no-op amendment of a cancelled booking is 409 `reservation_cancelled`.
299. **TRM-14a/b/c/d** Cutoff uses accepted terms: a booking accepted under cutoff 60 refuses amendment, cancel and even a no-op amendment within 60 minutes (409 `cutoff_passed`) although the newest policy's cutoff is 0; a booking made under the cutoff-0 policy accepts cutoff 0.
300. **TRM-15a/b/c** A booking accepted under cutoff 60 can be amended 68 minutes ahead although the newest policy says 600; it then adopts cutoff 600 (version 2) and its cancel is refused.
301. **TRM-16** A booking accepted with cutoff 0 can be cancelled 45 minutes ahead although the newest policy says 600.
302. **TRM-17/17b** An amendment onto a taken table is 409 `table_unavailable` and changes nothing (body, revision, history, occupancy).

## XRV - expected_revision

303. **XRV-1** A matching `expected_revision` succeeds (200, revision 2).
304. **XRV-2** A differing positive value is 409 `stale_revision`.
305. **XRV-3/4/4b** Stale is reported before field validation (invalid party), grid validation and resource checks (unknown table).
306. **XRV-5** Stale attempts change nothing.
307. **XRV-6/6b** Stale is reported before the cutoff check; a matching revision within the cutoff is 409 `cutoff_passed`.
308. **XRV-7** `expected_revision` 0, -1, true, false, a string, 1.5, a list, an object are each 422 `validation_failed`; **XRV-7b** they change nothing.
309. **XRV-8** Omission keeps stage-1 semantics.
310. **XRV-9** A matching value on a no-op is 200 and the revision stays.
311. **XRV-10** Unrelated unknown fields remain ignored next to `expected_revision`.
312. **XRV-11** 12 concurrent real amendments with `expected_revision 1`: exactly one 200, eleven 409 `stale_revision`, final revision 2, two history entries.
313. **XRV-12/12b** 12 concurrent real amendments without it: all 200 with revisions exactly 2..13, 13 history entries with revisions 1..13, and the final state equals the last entry's result.

## HIS - history and decision

314. **HIS-1** A seeded booking has one `created` entry (seq 1, revision 1, policy-0 terms, three fields from null).
315. **HIS-2** History is `{reference, entries}`; `created` names `table_id`, `starts_at_local`, `party_size` in that order, each `from: null`.
316. **HIS-3** Each entry carries the resulting `revision` and complete `accepted_terms`.
317. **HIS-4** A party change records one `changed` entry naming only `party_size` with from and to.
318. **HIS-5** Changing all three records them in the order `table_id`, `starts_at_local`, `party_size`.
319. **HIS-6** Re-sending unchanged fields records only the changed one.
320. **HIS-7** A no-op PATCH records no entry; **HIS-8** failed PATCHes record none; **HIS-9** replaying the creating request records none; **HIS-10** a policy publication changes no history.
321. **HIS-11** An amendment under a new policy records the new terms; earlier entries keep the old terms.
322. **HIS-12** Cancel records `cancelled` with empty `changes` and the incremented revision.
323. **HIS-13** Nothing follows `cancelled` (repeated cancel and refused PATCH add nothing); a cancelled booking still has history.
324. **HIS-14** `seq` runs 1..n by 1; `at` is RFC 3339 with offset and non-decreasing; entry revisions run 1..n.
325. **HIS-15a/b/c/d** History is 404 `not_found` for another user, without a token (not 401), with an unknown token, and for an unknown reference.
326. **HIS-16** Decision returns exactly `reference`, `revision`, `accepted_terms` of the current booking, including after cancellation.
327. **HIS-17a/b/c** Decision is 404 for another user, without a token (not 401), and for an unknown reference.
328. **HIS-18** Decision of a seeded booking: revision 1, policy-0 terms.
329. **HIS-19** A batch records exactly one `changed` entry per changed booking and none for a no-op item.
330. **HIS-20** The last entry's revision equals the booking's current revision.

## CHS - combined-table history

331. **CHS-1** Pair creation records `table_ids` from null to the pair in declared order (sent reversed), instead of `table_id`.
332. **CHS-2** Single to pair records `table_ids` with complete before/after lists in declared order.
333. **CHS-3** Pair to pair records `table_ids`.
334. **CHS-4** Pair to single records `table_ids` (`["c_b","c_a"]` to `["c_d"]`).
335. **CHS-5** Single to single keeps `table_id`.
336. **CHS-6** Re-sending the pair in either order is a no-op (revision unchanged, no entry).
337. **CHS-7** A pair's time change sent with the reversed pair records only `starts_at_local`.
338. **CHS-8** A batch move from a single to a pair records `table_ids`.
339. **CHS-9** The combined-table histories are well formed.

## SER - recurring reservations: adoption

340. **SER-1** Adoption is 201 with `series_id`, `revision 1`, `interval_weeks` and all `count` occurrences in index order, `exception false`.
341. **SER-2** Occurrence 0 is the anchor: same reference and unchanged reservation (revision, `created_at`, terms).
342. **SER-3** The anchor's history is unchanged by adoption.
343. **SER-4** Occurrences have distinct references, start on anchor date + i x 7 days at the same clock time, use the anchor's table and party, are confirmed with revision 1 and their own date's (policy-0) terms.
344. **SER-5** They appear in `GET /reservations`; **SER-6** they occupy their table; **SER-7** each generated one has an ordinary history with one `created` entry.
345. **SER-8** `GET /series/{id}` returns the same shape with current states; **SER-8b/c/d** another user, no token and an unknown id are 404 `not_found`.
346. **SER-9** A replay after a later change returns 200 with the original series response; **SER-9b** it changes no counter (series and restaurant revision); **SER-9c** `GET /series` shows the current state.
347. **SER-10a/b** Adopting the same anchor again with a new key, or adopting a generated occurrence, is 409 `already_in_series`.
348. **SER-11** `count` 1, 13, 0, -2, true, a string, 2.5 and `interval_weeks` 0, 5, true, a string, 1.5, and each missing field, are 422 `validation_failed`.
349. **SER-12a/b/c/d** No token 401; missing key 400; 256-character key 422; unparseable body 400.
350. **SER-13a/b** Unknown anchor and another owner's anchor are 404 `not_found`.
351. **SER-14** A cancelled anchor is 409 `reservation_cancelled`.
352. **SER-15** An anchor within its accepted cutoff is 409 `cutoff_passed`.
353. **SER-16** After all refusals the anchor was never claimed; unknown fields are ignored (201).
354. **SER-17/18** `count 12` with `interval_weeks 4`: 12 occurrences 28 days apart, distinct references.

## SERX - recurring reservations: policies, failures, DST

355. **SERX-1/1b** Each occurrence selects its own date's policy (versions 0, 0, 1, 1; durations 90, 90, 60, 60; terms).
356. **SERX-2** An occurrence on a weekday its policy closes fails the whole adoption with 422 `outside_opening_hours`.
357. **SERX-3** Nothing survives a failed adoption (anchor, its history, reservation list, restaurant revision).
358. **SERX-4** The failed key is reusable with another body and the anchor stays adoptable.
359. **SERX-5/6** The first failing occurrence in index order decides the code (409 conflict at index 1 before a closed day at index 2; 422 closed day at index 1 before a conflict at index 2).
360. **SERX-7/8/9** An occupancy conflict on occurrence 3 is 409 `table_unavailable`; no reservation, occupancy, history or counter survives; the same key with a different body is then a first use.
361. **SERX-10** An occurrence ending after its policy's closing is 422 `outside_opening_hours`.
362. **SERX-11** An occurrence off its policy's grid is 422 `not_on_slot_grid`.
363. **SERX-12** An occurrence above its policy's capacity is 422 `party_exceeds_capacity`.
364. **SERX-13/14** Weekly occurrences across the autumn and spring transitions keep the local clock time and take the new offset.
365. **SERX-15/15b** An occurrence at a nonexistent local time rejects the whole adoption with 422 `invalid_local_time`, leaving nothing behind.
366. **SERX-16** An occurrence at a repeated local time resolves to the first occurrence and ends 90 absolute minutes later.

## SRV - series revision and exceptions

367. **SRV-1** A real PATCH on an occurrence marks it `exception true` and increments the series revision once.
368. **SRV-2** Patching it back keeps `exception true` and increments again.
369. **SRV-3/4** A no-op or failed PATCH changes neither.
370. **SRV-5** Cancelling an occurrence increments the series revision once, keeps it in the series as cancelled, without marking an exception.
371. **SRV-6** A repeated cancel changes nothing.
372. **SRV-7** Cancelling the anchor does not cancel its siblings.
373. **SRV-8** A batch changing two occurrences increments the series revision once and marks both.
374. **SRV-9** A failed batch changes no series revision, flag or restaurant revision.
375. **SRV-10** Moving an occurrence to another date keeps its index and reference.
376. **SRV-11** The adoption replay returns the original response and changes no counter.

## CMV3 - collective moves under policies and agreements

377. **CMV3-1** A real batch change adopts the resulting date's policy (terms, end, revision, one history entry).
378. **CMV3-2** A batch move off the resulting policy's grid is 422 `not_on_slot_grid`.
379. **CMV3-3** A batch item within its accepted cutoff is 409 `cutoff_passed`.
380. **CMV3-4/4b** A stale per-move `expected_revision` is 409 `stale_revision`, before that item's validation.
381. **CMV3-5** Per-move `expected_revision` 0, true, a string, -1, 1.5 are 422 `validation_failed`.
382. **CMV3-6/7** A batch with one failing item is refused with that item's code and changes no booking, revision, history, exception flag, series revision or restaurant revision.
383. **CMV3-8** The failed batch's key is reusable.
384. **CMV3-9/10** In a successful batch each changed booking gains exactly one revision and one `changed` entry; the no-op item keeps body, terms and history.
385. **CMV3-11** The restaurant revision increases exactly once for the batch.
386. **CMV3-12** The affected series revision increases once and the changed occurrence becomes a permanent exception.
387. **CMV3-13** The replay is 200 with the original body and changes nothing.

## RREV - restaurant revision (coordinator's definition)

388. **RREV-1** After reset every restaurant detail carries integer `revision 1`.
389. **RREV-2..18** +1 for a booking, a real amendment, a policy, an adoption of 4 occurrences (once), a batch of three changes (once) and a cancel; +0 for a booking replay, a refused booking, a no-op amendment, a failed amendment, a policy replay, a failed policy, an adoption replay, a failed adoption, a batch replay, a failed batch and a repeated cancel.
390. **RREV-19** Other restaurants are unaffected.

## UPG3 - upgrade from stage-1 and stage-2 exports

Fixtures: `stage1_export.json` (captured from the accepted stage-1 image 797afdd) and
`stage2_export.json` (captured from the accepted stage-2 image cbafad2: seeded pair, seeded
cancelled single, a signed-up user, a pair, a single, a cancelled booking, a lost-response key, a
failed key and a batch receipt), each with an `_expected.json` holding tokens, keys, bodies and
original responses. Throwaway probe credentials only.

391. **UPG3-1** Each genuine export imports with 204.
392. **UPG3-2** The old session token still works.
393. **UPG3-3** Every imported booking has revision 1 and policy-0 terms equal to the imported restaurant's rules.
394. **UPG3-4** Identities, references, statuses and timestamps are unchanged.
395. **UPG3-5** Every imported booking has a well-formed history starting with `created`.
396. **UPG3-6** Decision works for every imported booking.
397. **UPG3-7** The policies list is empty and the restaurant revision is 1.
398. **UPG3-8** An imported restaurant has no managers: publication is 403.
399. **UPG3-9/9b** Password login and the old signed-up session work.
400. **UPG3-10/11** Stage-1 receipts (booking and batch) replay with exactly their original bodies; **UPG3-11b** a failed stage-1 key is a first use.
401. **UPG3-12** An imported stage-1 booking can be adopted.
402. **UPG3-13a-e** Stage-2 receipts (pair, single, lost-response, later-cancelled booking, batch) replay with exactly their original bodies.
403. **UPG3-14/15** A failed stage-2 key is a first use; a stage-2 key with a different body is 409.
404. **UPG3-16/16b** An imported seeded pair can be adopted; the generated occurrence holds the pair and its `created` entry records `table_ids` in declared order.
405. **UPG3-17/17b** An imported cancelled seed stays cancelled and cannot be adopted (409).
406. **UPG3-18** Reset after an upgrade import clears it.

## RT3 - stage-3 export and import

407. **RT3-1** Export after policies, amendments, a cancel, a series with an exception, a batch and a failed key is 200 with the required envelope.
408. **RT3-2** Import of that export after further mutations is 204.
409. **RT3-3** Reservation list, every history, every decision, the series and the policies list are identical to the pre-export state.
410. **RT3-4a-d** Booking, policy, series and batch replays return their original bodies.
411. **RT3-5/5b** Post-export bookings and accounts are gone.
412. **RT3-6** A key that failed before export is a first use.
413. **RT3-7** Importing again restores the identical state without duplicates.
414. **RT3-8** The next publication after import is version 3 (the discarded post-export policy leaked no version).
415. **RT3-9** Reset clears imported stage-3 state.

## CON3 - concurrency

416. **CON3-1** 50 in-flight mixed requests (6 publications, 15 racing bookings, 10 guarded amendments, 8 adoptions, 3 batches, 8 reads): no exception, no 5xx.
417. **CON3-2** Every concurrent read is consistent (200, well-formed entries, no table in two overlapping confirmed bookings).
418. **CON3-3** Six concurrent publications get versions exactly 1..6, listed contiguously.
419. **CON3-4** Five racing bookings per table: exactly one 201, the rest 409 `table_unavailable`.
420. **CON3-5** Two concurrent amendments with `expected_revision 1` per booking: exactly one succeeds, the other is 409 `stale_revision`; revision equals history length.
421. **CON3-6** Stale amendments are 409 `stale_revision` under load.
422. **CON3-7** Three concurrent adoptions of one anchor with different keys: exactly one 201, two 409 `already_in_series`.
423. **CON3-8/9** The other concurrent adoptions and batches all succeed.
424. **CON3-10** Afterwards no table is in two overlapping confirmed bookings.
425. **CON3-11** Every booking's revision equals its history length and its last entry's revision.
426. **CON3-12** 12 identical concurrent adoptions with one key: exactly one 201, eleven 200 identical.

## UI-POL - stage-2 screens with stage-3 policies (browser)

427. **UI-P1** With a published policy the grid has one cell per table per policy slot (17:00, 18:00, 19:00, 20:00) and `data-available` matches `GET /availability`.
428. **UI-P2** No cell exists for a time the policy's grid does not offer.
429. **UI-P3/3b** The policy's raised capacity offers `p_a` to a party of 3; another diner's booking makes the overlapping policy slots unavailable.
430. **UI-P4** The declared pair cell uses combinable order and is available under the policy's capacities.
431. **UI-P5** The booking form names the label and the policy slot time; party size is pre-filled.
432. **UI-P6/6b** The confirmation reference is exactly the API reference; the booking accepted version 1 with a 120-minute end.
433. **UI-P7** Lookup shows `confirmed` and the table label.
434. **UI-P8** No horizontal scrolling at 375 px with the policy grid.
435. **UI-P9/9b** A lost response under the policy shows `booking-uncertain`; the retry reuses key and body, recovers the original reference, and exactly one booking exists.
436. **UI-P10** A day the selected policy closes shows `no-slots` instead of the grid.

## Not automated (stage 3)

437. **N3-1** The exact cutoff instant (`now == starts_at - accepted cutoff`) is a single instant; probes test about 12 minutes either side.
438. **N3-2** Restaurant revision after a stage-3 export/import round trip: the coordinator's definition says it starts at 1 at import, but §10 also says state is preserved; not asserted.
439. **N3-3** Whether an all-no-op batch counts as a restaurant revision change: not stated; not asserted.
440. **N3-4** Ordering between `stale_revision` and `reservation_cancelled` on a cancelled booking: not stated; not asserted.
441. **N3-5** A huge positive `expected_revision` (for example 2^63): the documents give no upper range, so neither 409 nor 422 is asserted.
442. **N3-6** Wrong JSON types inside a policy body: accepted as 400 or 422 (POL-10) because §5 and stage 3 give different rules.
443. **N3-7** The order of entries in a policy's `opening_hours` echo and in `accepted_terms`: compared order-insensitively by weekday.
