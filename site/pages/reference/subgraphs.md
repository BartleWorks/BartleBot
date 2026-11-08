---
# Written by tools/build_site_reference.py, so an edit here is lost at the next render.
# Fix the text where it comes from (site/CLAUDE.md, Generated pages), then run
# uv run python tools/build_site_reference.py
generated: true
---

# Subgraphs

The Official subgraphs, which come with BartleBot and are listed on the node browser's **Subgraphs** tab.

## Sync players { #sync-players }

Brings every player to one zone, one realm and one area, confirmed by sight. Most bots should start with this subgraph.

**Categories:** Team

**Inputs**

- **Sync in zone**: every parameter of [Sync in Zone](teleportation.md#sync-in-zone)

**Ports**

- **Entries:** Start, Recover
- **Exits:** Exit

**Nodes it runs:** [Relog](utility.md#relog), [Sync in Zone](teleportation.md#sync-in-zone)
