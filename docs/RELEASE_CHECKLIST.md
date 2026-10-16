# Release checklist: <version>

Tick a box by typing an x in it, and save. This copy lives in
C:\Users\Public\BartleBot\<version>, so ticks survive resetting the test account.
docs/RELEASING.md, "Testing a draft", has the why behind each step.

## Before, in your own account

- [ ] Draft downloaded:
      gh release download v<version> --dir C:/Users/Public/BartleBot/<version>
- [ ] Previous release downloaded (skip for the first release):
      gh release download v<previous> --dir C:/Users/Public/BartleBot/<previous>

## 1. Fresh install

- [ ] Run Setup.exe from C:\Users\Public\BartleBot\<version>.
      No UAC prompt. The logo shows with a green bar. The app opens into setup.
      If it fails: %LocalAppData%\velopack\velopack.log
- [ ] Setup's Quick path: game folder found, account added, game art extracted.
- [ ] Bot library shows badges and tag icons as soon as the art is in, without a relaunch.
- [ ] Self-check prints all passes and 0, in PowerShell:
      & "$env:LOCALAPPDATA\BartleBot\current\BartleBot.exe" --self-check | Out-Host; $LASTEXITCODE
- [ ] An Official bot runs end to end in game (one this release changed, or a short one).
- [ ] Session log header says "packaged (PyInstaller)" and <version>, and the log has no
      unexplained WARNING or ERROR. Logs: %APPDATA%\Bartleby\BartleBot\logs
- [ ] Export a bot to the Desktop. Explorer calls it a BartleBot bot, with the logo.
- [ ] App closed, double-click it: the app starts and imports it.
- [ ] App open, double-click it again: the same window comes to the front with the import
      summary, and no second window opens.
- [ ] Settings › Apps lists BartleBot, published by Bartleby.

## 2. Uninstall

- [ ] Settings › Apps › BartleBot › Uninstall. It asks nothing.
- [ ] The exported bot on the Desktop is no longer a BartleBot bot.
- [ ] This prints False, False, False, True, in PowerShell:
      Test-Path "$env:LOCALAPPDATA\BartleBot", 'HKCU:\Software\Bartleby', 'HKCU:\Software\Classes\.bbot', "$env:APPDATA\Bartleby"

## 3. Update from <previous> (skip for the first release)

- [ ] Sign out of the test account. In your own, from an elevated PowerShell:
      powershell -ExecutionPolicy Bypass -File tools\release_test_user.ps1 -Version <version> -Previous <previous>
      Then sign in to the test account again.
- [ ] Run Setup.exe from C:\Users\Public\BartleBot\<previous>, take setup's Quick path,
      and close the app.
- [ ] Start it pointed at the draft, in PowerShell:
      $env:BARTLEBOT_UPDATE_FEED = 'C:\Users\Public\BartleBot\<version>'
      & "$env:LOCALAPPDATA\BartleBot\BartleBot.exe"
- [ ] The popup offers <version>, with its notes.
- [ ] Not now: the title bar shows "Update <version>".
- [ ] Close the app and start it again the same way: the button shows, and the popup doesn't.
- [ ] Button › Install when I close BartleBot › close the app › start it again.
- [ ] Session log header says <version>. Settings, account and bot library are as they were.

## 4. Update from the draft

Only for the first release, or when the draft changed bartlebot/updates/,
bartlebot/packaging/, gui/update_prompt.py or the Velopack pin.

- [ ] Pack a newer version, sign its feed and update the draft to it:
      docs/INSTALLER.md, "Trying an install, an update and an uninstall".
- [ ] Restart now: the app closes and opens again as the newer version.

## 5. Publish, in your own account

- [ ] gh release edit v<version> --draft=false
