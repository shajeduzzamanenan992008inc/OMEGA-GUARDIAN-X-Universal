# guard/policies

`../../shield.toml` is the **active** project policy. `default.toml` here is a pristine copy to restore from.

* The policy is *restrict-only*: it can switch capabilities off, never on (`ogx policy PATH` shows the effective result).
* The engine enforces `[security]`; `[governance]` / `[review]` are guidance for agents and reviewers.
* Per-operation confirmation (`--confirm`, email approval) is always required, whatever the policy says.
* A policy that is a symlink, not a regular file, or invalid TOML is rejected (never silently ignored).
