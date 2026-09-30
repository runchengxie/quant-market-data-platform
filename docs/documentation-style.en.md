# Documentation writing and lifecycle policy

[中文页面](documentation-style.md)

## Document status

Actively maintained documents record this metadata at the beginning:

```text
status: active | migration-only | historical | archived | superseded
owner: current project or team
audience: human | agent | human and agent
last_verified: YYYY-MM-DD
source_of_truth: yes | no
superseded_by: replacement document path or n/a
```

| Status | Use |
| --- | --- |
| `active` | Current capability, command, or governance rule. |
| `migration-only` | Entry point still needed for migration, recovery, or historical handoff. |
| `historical` | Dated research, audit, or decision record. |
| `archived` | Completed archival material retained for historical reproduction. |
| `superseded` | A replacement page exists; the body only points readers to it. |

Use `superseded` only when a specific replacement exists. Preserve the facts, dates, and conclusions of historical documents; do not rewrite them as current state.

## Chinese writing style

- State the conclusion first, then necessary context and steps.
- Use natural, direct Chinese and short sentences.
- Use Chinese punctuation in Chinese prose.
- Keep commands, paths, configuration keys, package names, API names, and fields in inline code.
- Put technical detail in `docs/`; use the root README for orientation, boundaries, quick start, and navigation.
- Keep one main point per paragraph and avoid repeating a rule.
- State the current conclusion directly and avoid unnecessary negation or contrast.

## Historical and migration notes

Migration notes state the current status, target entry point, reason for retention, and removal condition. Base claims about old commands, paths, and compatibility entries on evidence, not filename inference.

After moving a document, keep a short pointer at the old path with `status: superseded` and `superseded_by`. If the old document still carries historical facts, use `historical` or `archived` and retain its body.
