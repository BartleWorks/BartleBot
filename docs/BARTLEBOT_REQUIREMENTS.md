# BartleBot Requirements

This file details the requirements for implementing BartleBot. BartleBot is a Wizard101 botting application that is designed to complete repetitive tasks for the user, such as dungeon farming.

## Tech Stack

BartleBot's tech stack must be able to handle asynchronous programming.

- Scripts written in Python
- GUI designed with PyQt6 or similar
- Depdencies managed by poetry
- Interaction with Wizard101 game client handled by WizWalker and WizSprinter libraries

## Bot Design

### Example Scripts

There are several example scripts located in /BartleBot/.claude/example_deimos_scripts/ from an existing bot tool called Deimos. Deimos uses a DSL called deimoslang to configure its bot scripts, but it lacks many important features that BartleBot aims to improve upon. Deimoslang does not support imports, function parameters, or function return values. This causes deimoslang scripts to be very repetitive and makes it impossible to standardize core script libraries. It also does not handle string manipulation. These scripts are here to provide examples of situations that the BartleBot bots will be used in and the type of logic that BartleBot will need to be able to handle. The mt_olympus.txt file provides a good example of a linear dungeon, showing that the bots do not need to make very many decisions. The do_daga_bot_adv.txt file showcases some rudimentary failsafe functionality, but at the same time shows the limitations of deimoslang.

### Bot Structure

Bots are designed as a deterministic FSM due to the mostly linear nature of the tasks that they are intended to perform.

The FSM state nodes define behavior on a per-node basis. The behavior is defined by a Python script, which uses WizWalker and WizSprinter to interface with the Wizard101 clients. BartleBot will provide a library of standard behavior action scripts, accessible via the in-app node browser. Node behavior will generally consist of transitions between zones, performing combat, and interacting with NPCs or other world items. Combat is configured via WizSprinter's combat config DSL, so BartleBot does not need to concern itself with combat handling and any of the decision making that comes with it.

The edges define conditions for transitions between states, which will mainly depend on certain game states accessed via the WizWalker `client` object such as current player health/mana or the zone the player is currently in.

Using this bot structure will provide opportunities to build in failsafes for the bots. For example, one of the most common issues with multi-client bots is zone desync, where multiple clients will attempt to enter a zone but the transition will only succeed for some of the clients. Handling zone desyncs in deimoslang is very verbose and can lead to stack overflows via recursion. BartleBot aims to alleviate this via its state machine logic.

It is possible that BartleBot's FSM will need to be capable of defining FSM logic that applies only to a subset of hooked clients. While it is possible to execute logic for subssets of clients using WizWalker/WizSprinter within the behavior script in a single node, more complex logic that transitions between nodes would likely require more advanced capability for multi-client handling.

## GUI Design

BartleBot provides a visual GUI that users can use to create, edit, and run their bots.

### Bot Creation and Editing

Users can use the BartleBot bot editor to create, save, load, and edit bots. The bot editor displays a visual representation of the bot's defined FSM. The visual editor does not provide any script editing functionality. It is purely to define the set of states in the bot FSM, and the transitions between those states. In other words, the bot editor is a low-code/no-code experience where the GUI simply exposes the parameters of the nodes to the user. For the predefined nodes in the BartleBot library, these parameters are also pre-defined. Similarly, transition conditions are displayed visually in the GUI. For user-defined types, the parameters are also user-defined and will be reflected on the GUI. Custom types can be saved locally so users can reuse them in the future.

To help facilitate the collection of necessary info for bot creation, BartleBot's GUI provides a set of basic utilities. These utilities include displaying the current XYZ and zone of the client(s), and the ability to copy that information to the clipboard. It also includes the ability to enter freecam mode and teleport the player to the current freecam location, which will subsequently deactivate freecam mode.

### Bot Running

When a user loads a bot script from a file, the visual representation of the bot's FSM will display on the BartleBot GUI. As the bot runs, its current state and transition logic will be reflected on the GUI. This will aid in debugging and help the users understand how the bots work. When the bot is running, the current runtime will be displayed on the GUI.

### Bot Logging

BartleBot will be capable of logging the actions of bots that users run, including creating a table of drops received and the quantities of each item. The drop table will be capable of being split between clients if there are multiple hooked clients running. Users will be able to export a .csv file containing the table of drops.

## References

I have downloaded reference files to assist in planning:

| Library     | Local Location               | Repo URL                                             | Description |
|-------------|------------------------------|------------------------------------------------------|-------------|
| WizWalker   | `C:\GitHub\wizwalker`        | https://github.com/Deimos-Wizard101/wizwalker        | Core library for hooking Wizard101 clients and interacting with them. Provides main basic functionality.
| WizSprinter | `C:\GitHub\wizsprinter`      | https://github.com/Deimos-Wizard101/wizsprinter      | Extension for WizWalker that adds slightly more advanced functionality. Not very actively maintained, so consult Deimos (below) for examples on how to use. Deimos does have some custom WizSprinter implementation.
| Deimos      | `C:\GitHub\Deimos-Wizard101` | https://github.com/Deimos-Wizard101/Deimos-Wizard101 | An existing Wizard101 botting tool. Includes examples of how to interface with WizWalker and WizSprinter, as well as some custom utilities 