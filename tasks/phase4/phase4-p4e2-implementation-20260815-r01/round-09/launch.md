# Round 09 launch

Status: `HOLD_PREALLOCATION_MACOS_PREFLIGHT`

The complete controller replay supersedes the r11 first-failure-only diagnosis.
On macOS 26.5.2 arm64, CPython 3.12.11, and jsonschema 4.26.0, both games
completed independent replay with exact structure and identity checks retained.
The frozen r11 profiles produced 163 numeric bound failures:

- feature profile: 5,475 comparisons, 32 differing leaves; maximum absolute
  `3.3306690738754696e-16`, relative `2.637573631646515e-14`, ULP `151`;
- legacy tight profile: 113,405 comparisons, 11,180 differing leaves; its
  151-ULP maximum was a nested model-context F04 value, and a fitted
  coefficient example reached 15 ULP;
- Top-1000 display probability: 6,000 comparisons, 146 differing leaves;
  maximum absolute `3.441071348220595e-22`, relative
  `3.561621664915701e-15`, ULP `31`.

No exact identity mismatch was observed. Stable score keys, score/tie
identities, ticket membership/order/rank, lineage, hashes, and create-once
files therefore remain exact.

Commits `18b25e2115afbcc573101329a4e24cb47f53c987` and
`eb9c124d5c8cdbe276c25ae358024ea8bf0c4bb6` are preserved evidence of the
incomplete 17-ULP hypothesis. They are not release authority and are not
rewritten.

No successor release identity is allocated in this round.
