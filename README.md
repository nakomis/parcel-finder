# parcel-finder — from a photo of a delivered parcel to the Claude session that bought it

## Support

If you find this useful, please consider buying me a coffee:

[![Donate with PayPal](https://www.paypalobjects.com/en_GB/i/btn/btn_donate_SM.gif)](https://www.paypal.com/donate?hosted_button_id=Q3BESC73EWVNN&custom=parcel-finder)

## Table of Contents

<!-- toc -->

- [What it does](#what-it-does)
  * [From the iPhone](#from-the-iphone)
- [Architecture Diagram](#architecture-diagram)
- [Repository Layout](#repository-layout)
- [Architecture Diagrams](#architecture-diagrams)
- [Support](#support)

<!-- tocstop -->

## What it does

Hold a newly arrived part up to the [ScreenEye](https://github.com/nakomis/mcps/tree/main/screeneye)
camera, press **Open in Claude** (on the iPhone, or in the Mac app), and parcel-finder:

1. identifies the item (text read by the phone's Vision framework, or local GLM-OCR for runs
   started on the Mac; a few rules; then Claude Haiku);
2. matches it against recent orders (Amazon and eBay order emails, the AliExpress Open
   Platform API);
3. finds the Claude Code session where the purchase was decided, via conversation-memory;
4. brings that session to the front, or resumes it, and tells it: *"This just arrived! It's
   been identified as a … What do I do with it?"*

The receiving session takes over from there.

### From the iPhone

Runs started on the phone go to a second listener on the home network (port 8776, advertised
over Bonjour as `_parcel-finder._tcp`). The phone polls the run for progress and shows the
pick-list when the choice of session isn't clear. Pair the phone once:

```bash
parcel-finder pair    # shows a QR code: scan it in ScreenEye's settings
launchctl kickstart -k gui/$(id -u)/com.nakomis.parcel-finder
```

Every request carries the paired token. It's plain HTTP on the home LAN, so anyone on the
network who sniffs the token could start a run (which only messages your own Claude sessions).

## Architecture Diagram

![Architecture](docs/architecture/parcel-finder.svg)

*(Diagram to come.)*

## Repository Layout

| Path | Contents |
|---|---|
| `docs/superpowers/specs/` | The design document |
| `docs/architecture/` | Architecture diagram source (`.drawio`) and generated SVG |
| `docs/logo-candidates/` | Logo candidates awaiting a decision |

## Architecture Diagrams

`docs/architecture/parcel-finder.drawio` is the source for the diagram above.
The SVG is auto-regenerated on commit by the pre-commit hook in `.githooks/pre-commit`.

To activate the hook after cloning:

```bash
git config core.hooksPath .githooks
```

## Support

If you find this useful, please consider buying me a coffee:

[![Donate with PayPal](https://www.paypalobjects.com/en_GB/i/btn/btn_donate_SM.gif)](https://www.paypal.com/donate?hosted_button_id=Q3BESC73EWVNN&custom=parcel-finder)
