"""The 'zone' param type, and the compatibility rule between param types.

A zone param used to be a 'str' that got the zone widget because its key said "zone". The
widget is now chosen by type alone, and 'nav_zone' is a narrower 'zone': a value the map
offers is valid anywhere a zone is, which is what lets one subgraph input feed both.
"""

import pytest
from PyQt6.QtWidgets import QLineEdit

from bartlebot import zone_nav
from bartlebot.gui.canvas.edit_scene import _required_field_empty
from bartlebot.nodes.node_helpers import has_glob, zone_matches
from bartlebot.nodes.param_schema import ParamField, narrowest_type
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS

_ZONE_TYPES = ("zone", "nav_zone")


def _form(fields, params):
    from bartlebot.gui.canvas.param_panel import _DynamicParamForm

    form = _DynamicParamForm()
    form.load_schema(fields, params)
    return form


# ---------------------------------------------------------------------------
# Compatibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("t", ["zone", "nav_zone", "str", "int", "xyz"])
def test_a_type_is_compatible_with_itself(t):
    assert narrowest_type([t, t]) == t


@pytest.mark.parametrize("order", [["zone", "nav_zone"], ["nav_zone", "zone"], ["zone", "nav_zone", "zone"]])
def test_a_zone_and_a_nav_zone_meet_at_the_nav_zone(order):
    assert narrowest_type(order) == "nav_zone"


@pytest.mark.parametrize("types", [["zone", "str"], ["nav_zone", "str"], ["zone", "int"], ["nav_zone", "zone", "int"]])
def test_unrelated_types_have_no_narrowest(types):
    """A zone is not a 'str' param: a free-text field accepts values no zone param means."""
    assert narrowest_type(types) is None


def test_no_types_have_no_narrowest():
    assert narrowest_type([]) is None


def test_every_nav_zone_is_a_valid_zone_value():
    """What makes nav_zone narrower: each map zone is a literal path, so zone_matches reads it exactly."""
    zones = [z for world in zone_nav.load().worlds.values() for z in world.zones]

    assert zones
    for zone in zones:
        assert zone.strip() == zone and not has_glob(zone), zone
        assert zone_matches(zone, zone)


# ---------------------------------------------------------------------------
# The node library
# ---------------------------------------------------------------------------


def _all_fields():
    return [(nt, f) for nt, fields in NODE_PARAM_SCHEMAS.items() for f in fields]


def test_every_param_named_for_a_zone_is_typed_as_one():
    """The widget no longer reads the key, so a zone param left as 'str' would lose Fill and autocomplete."""
    untyped = [
        f"{nt}.{f.key}"
        for nt, f in _all_fields()
        if (f.key == "zone" or f.key.endswith("_zone")) and f.type not in _ZONE_TYPES
    ]
    assert untyped == []


def test_every_zone_param_is_named_for_one():
    """restricted_zones.check_bot_config finds zone params by key suffix; a zone typed param it missed would dodge it."""
    misnamed = [
        f"{nt}.{f.key}"
        for nt, f in _all_fields()
        if f.type in _ZONE_TYPES and not (f.key == "zone" or f.key.endswith("_zone"))
    ]
    assert misnamed == []


# ---------------------------------------------------------------------------
# The widget, and required
# ---------------------------------------------------------------------------


def test_a_zone_param_gets_the_zone_widget(qapp):
    from bartlebot.gui.canvas.param_panel import _ZoneWidget

    form = _form([ParamField(key="where", type="zone", label="Where")], {"where": "WizardCity/*"})

    assert isinstance(form.widget("where"), _ZoneWidget)
    assert form.get_params() == {"where": "WizardCity/*"}


async def test_fill_from_player_writes_the_ordinary_zone_of_a_challenge_mode_copy(qapp):
    """A bot stores the ordinary zone, which matches either one (#143)."""

    class _InCopy:
        async def zone_name(self):
            return "Aquila/AQ_Z02_Atlantea_Hard"

    from bartlebot.gui.canvas.param_panel import _DynamicParamForm

    form = _DynamicParamForm()
    form.set_clients_getter(lambda: [_InCopy()])
    form.load_schema([ParamField(key="where", type="zone", label="Where")], {})

    await form.widget("where")._fill_from_player(0)

    assert form.get_params() == {"where": "Aquila/AQ_Z02_Atlantea"}


def test_a_str_param_named_zone_is_a_plain_field(qapp):
    from bartlebot.gui.canvas.param_panel import _ZoneWidget

    form = _form([ParamField(key="zone", type="str", label="Zone")], {"zone": "WizardCity/WC_Hub"})

    assert type(form.widget("zone")) is QLineEdit
    assert not isinstance(form.widget("zone"), _ZoneWidget)


@pytest.mark.parametrize("t", _ZONE_TYPES)
@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_empty_required_zone_is_flagged(t, value):
    field = ParamField(key="zone", type=t, label="Zone", required=True)
    params = {} if value is None else {"zone": value}

    assert _required_field_empty(field, params)


@pytest.mark.parametrize("t", _ZONE_TYPES)
def test_a_filled_required_zone_is_not_flagged(t):
    field = ParamField(key="zone", type=t, label="Zone", required=True)

    assert not _required_field_empty(field, {"zone": "WizardCity/WC_Hub"})
