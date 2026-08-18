# Incomplete-hypothesis evidence

The initial r11 controller report stopped at the first failure. Based on that
partial observation, commits `18b25e21` and `eb9c124d` proposed retaining the
17-ULP/absolute display-probability limits and deriving only a relative ceiling.
The full controller matrix proves that hypothesis incomplete: the shadow scope
reaches 31 ULP and a larger absolute error, while the legacy tight container
mixes nested 151-ULP derived feature values with coefficient propagation.

These commits remain in branch history unchanged. The active authority now
classifies numeric leaves by computation source rather than applying one broad
container profile.

Two pre-allocation engineering attempts are also retained:

1. The first standalone audit dry-run failed because its expected tight path
   count was 42; the frozen contract actually had 41. No release existed and
   the assertion was corrected before the full audit.
2. The first corrected focused test used a 32-ULP synthetic probability just
   above a binary binade boundary. Its relative axis correctly failed. The
   vector was moved below the boundary so the ULP boundary test isolates the
   intended axis; the conjunctive policy was not bypassed.

Neither attempt allocated or mutated a release.
