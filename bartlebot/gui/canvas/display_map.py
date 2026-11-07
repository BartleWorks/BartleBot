"""What each state is drawn as: a node of its own, or the folded node of the region it is in.

Two kinds of region are drawn as one node (``gui/CLAUDE.md``, "A folded region is drawn
as one node"): a subgraph instance, and a collapsed plain region (#67). ``DisplayMap``
holds which members are drawn as which folded node, the ports each folded node shows,
the regions opened up (an instance's body in Run mode, or a collapsed region stepped
into), and Run mode's collapses, a view over the saved flags that is never written.

``EditScene`` owns one, rebuilds it at the top of every ``setup`` and keeps the items;
this half needs no Qt. Nothing here caches across a rebuild of the scene.
"""

from __future__ import annotations

from dataclasses import dataclass

from bartlebot.bot.schema import SUBGRAPH_ENTRY, SUBGRAPH_EXIT, BotConfig, RegionModel, TransitionModel
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.content.subgraphs.subgraphs import (
    boundary_ports,
    collapse_blocker,
    folded_regions,
    is_collapsed,
    member_id,
    port_name,
)


@dataclass(frozen=True)
class FoldPorts:
    """A folded node's ports, in bot ids: what its edges attach to and the flat states behind them.

    One shape for both kinds of folded node. An instance's comes from its snapshot's
    port map through the derived member ids, named by its definition; a collapsed
    plain region's is inferred from the edges crossing its boundary
    (:func:`~bartlebot.content.subgraphs.subgraphs.boundary_ports`). Nothing that reads one needs to
    know which it has.
    """

    #: ``(port id, name, member)``: the member an entry leads into, None if it leads nowhere.
    entries: tuple[tuple[str, str, str | None], ...] = ()
    #: ``(port id, name, feeds)``: the ``(member, outcome)`` pairs an exit is fed by.
    exits: tuple[tuple[str, str, tuple[tuple[str, str | None], ...]], ...] = ()
    #: Port id -> the one destination an inferred exit stands for. An instance's exits
    #: have none: which way they lead is the bot's edge, not the port.
    exit_dests: tuple[tuple[str, str], ...] = ()

    def entry_port(self, state_id: str) -> str | None:
        return next((pid for pid, _n, m in self.entries if m == state_id), None)

    def exit_port(self, state_id: str, when: str | None, dest: str | None = None) -> str | None:
        """The exit ``(state_id, when)`` feeds -- toward ``dest``, when exits are per destination."""
        dests = dict(self.exit_dests)
        return next(
            (
                pid
                for pid, _n, feeds in self.exits
                if (state_id, when) in feeds and (pid not in dests or dest is None or dests[pid] == dest)
            ),
            None,
        )

    def entry_member(self, port_id: str | None) -> str | None:
        return next((m for pid, _n, m in self.entries if pid == port_id), None)

    def exit_feeds(self, port_id: str | None) -> list[tuple[str, str | None]]:
        return next((list(feeds) for pid, _n, feeds in self.exits if pid == port_id), [])


class DisplayMap:
    """Members to the folded node they are drawn as, its ports, the opened regions and Run mode's collapses."""

    def __init__(self) -> None:
        self._config: BotConfig | None = None
        self._library: SubgraphLibrary | None = None
        # Member id -> the region id of the folded node it is drawn as: an instance's
        # call node, or a collapsed region's node (#67).
        self._call_of: dict[str, str] = {}
        # Region id -> the ports its folded node shows, for every folded node drawn.
        self._fold_ports: dict[str, FoldPorts] = {}
        # Run mode's collapses and expands, region id -> collapsed: a view over the
        # saved flags, never written to the file. Kept across rebuild(); cleared by the
        # canvas on entering Edit mode, where the saved state is decided.
        self._collapse_overrides: dict[str, bool] = {}
        # (region id, members) of each folded region as last drawn: see folded_changed.
        self._folded_sig: tuple = ()
        # Folded regions opened up, outermost first: an instance whose body is on show
        # in Run mode, or a collapsed region stepped into. Their members are drawn as
        # nodes of their own, and a folded region inside the innermost one as its
        # folded node. Kept across rebuild(); see set_expanded.
        self._expanded: list[str] = []

    # ------------------------------------------------------------------
    # Building it
    # ------------------------------------------------------------------

    def set_library(self, library: SubgraphLibrary | None) -> None:
        """The library a call node's ports and name are read from. May be None."""
        self._library = library

    def definition(self, region: RegionModel):
        """The library definition an instance is linked to, or None (a plain region, or missing from the library)."""
        if self._library is None or region.source is None:
            return None
        return self._library.get(region.source.subgraph_id)

    def rebuild(self, config: BotConfig) -> list[RegionModel]:
        """Work out what *config* draws folded, and return those regions.

        Run mode's collapses and the opened regions are kept across it, less any naming
        a region *config* no longer folds.
        """
        self._config = config
        region_ids = {r.id for r in config.regions}
        self._collapse_overrides = {rid: v for rid, v in self._collapse_overrides.items() if rid in region_ids}
        foldable = {r.id for r in config.regions if r.source is not None or is_collapsed(r, self._collapse_overrides)}
        self._expanded = [rid for rid in self._expanded if rid in foldable]
        folded = folded_regions(config, self._expanded, self._collapse_overrides)
        self._folded_sig = tuple((r.id, frozenset(r.members)) for r in folded)
        self._call_of = {m: region.id for region in folded for m in region.members}
        self._fold_ports = {region.id: self.ports_for(region) for region in folded}
        return folded

    def clear(self) -> None:
        """Nothing drawn: the scene was torn down. The opened regions and collapses are kept."""
        self._config = None
        self._call_of = {}
        self._fold_ports = {}

    def ports_for(self, region: RegionModel) -> FoldPorts:
        """What a folded region's node shows as ports, and the member each one reaches, in bot ids."""
        assert self._config is not None
        if region.source is None:
            entries, exits = boundary_ports(self._config, region.members)
            return FoldPorts(
                tuple((e.port_id, e.name, e.member) for e in entries),
                tuple((x.port_id, x.name, x.feeds) for x in exits),
                tuple((x.port_id, x.dest) for x in exits),
            )
        source = region.source
        defn = self.definition(region)
        if defn is not None:
            entry_ids = [(s.id, port_name(s)) for s in defn.states if s.node_type == SUBGRAPH_ENTRY]
            exit_ids = [(s.id, port_name(s)) for s in defn.states if s.node_type == SUBGRAPH_EXIT]
        else:
            # Missing from the library: the snapshot's own port map still says what the
            # ports are, which is what keeps the bot's edges drawable.
            entry_ids = [(pid, pid) for pid, p in source.ports.items() if p.member is not None]
            exit_ids = [(pid, pid) for pid, p in source.ports.items() if p.from_ is not None]
        rid = region.id

        def member_of(pid: str) -> str | None:
            port = source.ports.get(pid)
            return member_id(rid, port.member) if port is not None and port.member is not None else None

        def feeds_of(pid: str) -> tuple[tuple[str, str | None], ...]:
            port = source.ports.get(pid)
            return tuple((member_id(rid, m), w) for m, w in (port.from_ or ())) if port is not None else ()

        return FoldPorts(
            tuple((pid, name, member_of(pid)) for pid, name in entry_ids),
            tuple((pid, name, feeds_of(pid)) for pid, name in exit_ids),
        )

    def ports_changed(self) -> bool:
        """Whether a folded node's ports would now differ from those drawn: an edge across it moved."""
        ports = {rid: self.ports_for(r) for rid in self._fold_ports if (r := self.region_by_id(rid)) is not None}
        return ports != self._fold_ports

    def folded_changed(self) -> bool:
        """Whether the config now folds different regions, or different members, from those drawn."""
        if self._config is None:
            return False
        return self._folded_signature() != self._folded_sig

    def _folded_signature(self) -> tuple:
        if self._config is None:
            return ()
        return tuple((r.id, frozenset(r.members)) for r in self.folded_regions())

    def folded_regions(self) -> list[RegionModel]:
        """The regions the config folds, as the opened regions and Run mode's collapses stand now."""
        if self._config is None:
            return []
        return folded_regions(self._config, self._expanded, self._collapse_overrides)

    def region_by_id(self, region_id: str) -> RegionModel | None:
        if self._config is None:
            return None
        return next((r for r in self._config.regions if r.id == region_id), None)

    # ------------------------------------------------------------------
    # Display ids: what an instance's members are drawn as
    # ------------------------------------------------------------------

    def display_id(self, state_id: str) -> str:
        """The id of the item ``state_id`` is drawn as: its own, or its folded node's region id."""
        return self._call_of.get(state_id, state_id)

    def folded_region_of(self, state_id: str) -> str | None:
        """The region id of the folded node ``state_id`` is drawn inside, or None when it has a node of its own."""
        return self._call_of.get(state_id)

    def display_key(self, t: TransitionModel) -> tuple[str, str | None, str, str | None] | None:
        """``(source, when, dest, entry port)`` a transition is drawn as, or None when it is inside an instance.

        An edge out of an instance leaves from the exit port its ``(member, outcome)``
        feeds, and one into it lands on the entry port its member is reached by. A
        crossing on no port (a hand-edited file) is drawn from the node itself.
        """
        s, d = self.display_id(t.source), self.display_id(t.dest)
        if s == d and t.source in self._call_of:
            return None
        w = t.when
        if t.source in self._call_of and s in self._fold_ports:
            pid = self._fold_ports[s].exit_port(t.source, t.when, t.dest)
            w = pid if pid is not None else t.when
        port = None
        if t.dest in self._call_of and d in self._fold_ports:
            port = self._fold_ports[d].entry_port(t.dest)
        return s, w, d, port

    def ports_of(self, region_id: str) -> FoldPorts | None:
        """The ports a folded region's node shows, or None when it is not drawn folded."""
        return self._fold_ports.get(region_id)

    @property
    def folded_ids(self) -> list[str]:
        """The region id of every folded node drawn."""
        return list(self._fold_ports)

    # ------------------------------------------------------------------
    # Opened regions: a subgraph node's body in Run mode, a collapsed region stepped into
    # ------------------------------------------------------------------

    @property
    def expanded(self) -> list[str]:
        """The folded regions whose members are drawn as nodes, outermost first."""
        return list(self._expanded)

    def set_expanded(self, region_ids: list[str]) -> bool:
        """Open these folded regions, outermost first. Whether that changed anything."""
        region_ids = list(region_ids)
        if region_ids == self._expanded:
            return False
        self._expanded = region_ids
        return True

    def close(self, region_ids) -> None:
        """Stop holding these regions open: they were expanded, so there is nothing to step into."""
        gone = set(region_ids)
        self._expanded = [rid for rid in self._expanded if rid not in gone]

    def expanded_members(self) -> set[str]:
        return {m for rid in self._expanded if (r := self.region_by_id(rid)) is not None for m in r.members}

    def expanded_instance_members(self) -> set[str]:
        """Members of opened instances: a collapsed region stepped into is the bot's own graph."""
        return {
            m
            for rid in self._expanded
            if (r := self.region_by_id(rid)) is not None and r.source is not None
            for m in r.members
        }

    # ------------------------------------------------------------------
    # Collapsing: the saved flag, and Run mode's view over it
    # ------------------------------------------------------------------

    def collapse_blocker(self, region_id: str) -> str | None:
        """Why ``region_id`` cannot be collapsed, or None if it can."""
        if self._config is None:
            return "No bot is open."
        return collapse_blocker(self._config, region_id, self._collapse_overrides)

    def is_region_collapsed(self, region_id: str) -> bool:
        """Whether a plain region is drawn collapsed: Run mode's override, else its saved flag."""
        region = self.region_by_id(region_id)
        return region is not None and region.source is None and is_collapsed(region, self._collapse_overrides)

    def bulk_collapse_targets(self, collapsed: bool) -> list[str]:
        """The plain regions *Collapse all* or *Expand all* would change, in the order it would.

        Collapse all goes largest first, and skips a region its own earlier collapses
        would block, so of two partly overlapping regions the larger folds. A region
        inside one that collapses is still marked, and shows when stepped into.
        """
        if self._config is None:
            return []
        plain = [r for r in self._config.regions if r.source is None and r.members]
        if not collapsed:
            return [r.id for r in plain if is_collapsed(r, self._collapse_overrides)]
        order = {r.id: i for i, r in enumerate(self._config.regions)}
        sim = dict(self._collapse_overrides)
        targets: list[str] = []
        for r in sorted(plain, key=lambda r: (-len(r.members), order[r.id])):
            if is_collapsed(r, sim) or collapse_blocker(self._config, r.id, sim) is not None:
                continue
            sim[r.id] = True
            targets.append(r.id)
        return targets

    def set_collapse_overrides(self, region_ids: list[str], collapsed: bool) -> bool:
        """Run mode's collapse or expand: a view over the saved flags, not an edit. False if nothing changed.

        An override equal to the saved flag is dropped rather than kept, so what is
        held is only where the view differs from the file. Expanding also stops holding
        the regions open.
        """
        if self._config is None:
            return False
        changed = False
        for rid in region_ids:
            region = self.region_by_id(rid)
            if (
                region is None
                or region.source is not None
                or is_collapsed(region, self._collapse_overrides) == collapsed
            ):
                continue
            if collapsed and collapse_blocker(self._config, rid, self._collapse_overrides) is not None:
                continue
            if bool(region.collapsed) == collapsed:
                self._collapse_overrides.pop(rid, None)
            else:
                self._collapse_overrides[rid] = collapsed
            changed = True
        if changed and not collapsed:
            self.close(region_ids)
        return changed

    def forget_collapse_overrides(self) -> bool:
        """Drop Run mode's view, back to the saved flags. Whether there was one."""
        had = bool(self._collapse_overrides)
        self._collapse_overrides = {}
        return had
