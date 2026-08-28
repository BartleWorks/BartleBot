"""What opening a bot file does to its stored values.

Two settings, two opposite rules, and the difference is the point:

- Safety Delay describes the machine's connection, not the bot, so the app
  setting replaces whatever the file carries.
- The quicksell sell_* params describe the bot, so loading must not touch them
  at all. They used to be rewritten here, which is what made the node param
  unreachable; they resolve against the settings at run time now.

Exercised through the real AppController method, unbound — it reads only the
settings and the config, and standing up a window to reach it would test Qt
rather than this rule.
"""

from bartlebot.app_controller import AppController
from bartlebot.bot.schema import BotConfig
from bartlebot.settings_manager import SettingsManager


def _config(params: dict, *, safety_delay: float = 3.0) -> BotConfig:
    return BotConfig.model_validate(
        {
            "name": "Test Bot",
            "initial": "qs",
            "recovery_state": "qs",
            "options": [{"name": "Safety Delay", "type": "float", "default": safety_delay}],
            "states": [{"id": "qs", "node_type": "quicksell", "params": params}],
            "transitions": [],
        }
    )


def _apply(config: BotConfig) -> dict:
    AppController._apply_bot_defaults(None, config)
    return config.states[0].params


def test_safety_delay_is_replaced_by_the_app_setting():
    SettingsManager.instance().default_safety_delay = 7.5
    config = _config({}, safety_delay=3.0)

    AppController._apply_bot_defaults(None, config)

    assert config.options[0].default == 7.5


def test_a_pinned_quicksell_flag_survives_loading():
    """The whole point of the third state — and what the old override broke."""
    SettingsManager.instance().set_general("default_quicksell_sell_pets", True)

    assert _apply(_config({"sell_pets": False}))["sell_pets"] is False


def test_an_unset_quicksell_flag_is_left_unset():
    # Resolved at run time instead, so changing the app setting later still works.
    assert "sell_pets" not in _apply(_config({}))


def test_loading_does_not_rewrite_quicksell_params():
    """The regression this whole change exists to prevent: values written back
    over the bot's own, every time the file is opened."""
    stored = {"keep_list": ["hat"], "sell_pets": True, "sell_socket_wrenches": False}
    SettingsManager.instance().set_general("default_quicksell_sell_pets", False)

    assert _apply(_config(dict(stored))) == stored


def test_a_non_quicksell_state_is_untouched():
    config = BotConfig.model_validate(
        {
            "name": "Test Bot",
            "initial": "s0",
            "recovery_state": "s0",
            "states": [{"id": "s0", "node_type": "sleep", "params": {"sell_pets": True}}],
            "transitions": [],
        }
    )

    AppController._apply_bot_defaults(None, config)

    assert config.states[0].params == {"sell_pets": True}
