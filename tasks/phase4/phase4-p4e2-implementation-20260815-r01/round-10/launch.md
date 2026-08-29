# Round 10 launch

Status: `HOLD_PREALLOCATION_MACOS_PREFLIGHT`

The c5a9 macOS arm64 CPython 3.12.11 preflight completed all 124,880
comparisons and supersedes the Linux-only round-09 sufficiency claim. It
reported 17 new bound failures while retaining exact structure and identity
checks and leaving r11 unchanged.

The already corrected profiles passed: fitted coefficient/objective gradient
had zero failures with maximum 15 ULP; the three formal, historical, and shadow
display-probability paths had zero failures with maxima 27, 26, and 31 ULP.

Only two source classes remained:

- `model.zones.*.context.number_features.F04.*`: one failure; maximum absolute
  `4.440892098500626e-16 = 4 * 2^-53`, relative
  `2.637573631646515e-14`, and 151 ULP;
- `model.zones.*.top_zone_rows.*.0`: 16 failures; maximum absolute
  `4.440892098500626e-16`, relative `1.1285989447903035e-14`, and 64 ULP.

No successor release is allocated. r11 and the commits through `c5a9b3a3`
remain immutable evidence.
