# Retention reference scan cost

The read-only retirement planner must keep its existing decisions and evidence
scope while avoiding containment checks for paths that cannot reference a
candidate. Production profiling found substantial Python path-comparison cost;
a 300-second attempt did not finish.

Keep substring basename matching, self-reference exclusion, external evidence
validation, symlink overlap protection, and fail-closed scan errors unchanged.
Do not change inventory rules, deletion policy, published data, or schedulers.
Use predicate ordering and one compiled literal-name prefilter per reference root.
Validate external evidence before filtering; keep the original per-candidate
substring logic for matching text, including overlapping names.
Verify with real filesystem fixtures, a deterministic operation-count regression,
the repository quality gate, and a bounded read-only host comparison before
considering production activation.
