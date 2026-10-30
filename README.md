<h1><img src="site/pages/assets/bartlebot.svg" alt="" width="48" align="middle"> BartleBot</h1>

A Wizard101 bot for the repetitive parts of the game: farming dungeons, gathering, and the
fights in between. BartleBot is in public beta.

## Installing BartleBot

Download it from **[bartleworks.github.io/BartleBot](https://bartleworks.github.io/BartleBot/)**,
which also says what Windows will warn you about first, and why.

## Running from source

For working on BartleBot itself. You need Python 3.13 or newer and
[uv](https://docs.astral.sh/uv/getting-started/installation/).

```powershell
uv sync
uv run python bartlebot.py
```

`CLAUDE.md` covers the rest: developer mode, the tests, and how the code is laid out.
BartleBot is built on [WizWalker](https://github.com/BartleWorks/wizwalker) and
[WizSprinter](https://github.com/BartleWorks/wizsprinter).

## License

Copyright (C) 2026 BartleBot

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
this program. If not, see <https://www.gnu.org/licenses/>.
