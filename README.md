# BartleBot

A Wizard101 bot built on [WizWalker](https://github.com/Deimos-Wizard101/wizwalker) and [WizSprinter](https://github.com/Deimos-Wizard101/wizsprinter).

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Wizard101 running with at least one logged-in client
- **Run your terminal as Administrator** — BartleBot reads game process memory, which requires elevated privileges.

## Setup

```powershell
uv sync
```

If you get an execution policy error activating the venv:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

## Activate the virtual environment

```powershell
.venv\Scripts\Activate.ps1
```

## Run

```powershell
python bartlebot.py
```

## Utilities

Launch a single Wizard101 instance and log in automatically:

```powershell
python launch_wizard101.py username:password
```

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
