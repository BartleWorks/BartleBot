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
