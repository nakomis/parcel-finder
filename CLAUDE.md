# parcel-finder

Links a photo of a delivered parcel to the Claude Code session where it was bought, and wakes
that session with the photo. Runs on phi as a per-user LaunchAgent. The design is
`docs/superpowers/specs/2026-10-22-parcel-finder-design.md`; read it before changing behaviour.

Plane: project **HOME**, module **Parcel finder**. Branches and PR titles carry the ref
(e.g. `home-529-…`, `… (HOME-529)`).

## Repository layout

| Path | Contents |
|---|---|
| `docs/superpowers/specs/` | The design document |
| `docs/architecture/` | Architecture diagram source and generated SVG |
| `docs/logo-candidates/` | Logo candidates awaiting a decision |

## Secrets

All in the macOS keychain, never in files or logs:

- `aliexpress-open-platform` / `app-key`, `app-secret`, `access-token`, `refresh-token`
- Anthropic API key: the finder's own entry (to be created; see the design)

## Testing

Python, managed with `uv`. `uv run pytest`; coverage target 70% minimum.

## Architecture diagrams

Source: `docs/architecture/parcel-finder.drawio` — SVG auto-regenerated on commit by `.githooks/pre-commit`.

To activate the hook after cloning:
```bash
git config core.hooksPath .githooks
```
