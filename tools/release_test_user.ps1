<#
.SYNOPSIS
Creates, or recreates from nothing, the Windows account a release draft is tested in.

.DESCRIPTION
docs/RELEASING.md, "Testing a draft", has why the account exists and what is tried in it.

Everything an install and the app write is per user: the install folder, the Start Menu
entry, the HKCU keys, %APPDATA%\Bartleby, Credential Manager. Deleting the account's
profile deletes all of it, so a recreated account has never seen BartleBot. The account is
a standard user, never an administrator, so an install that asks for elevation shows it.

It also copies docs/RELEASE_CHECKLIST.md into the draft's folder, with the versions filled
in, and puts a shortcut to it on the account's desktop. The copy lives outside the account,
in C:\Users\Public\BartleBot\<version>, so its ticks survive a reset, and one already there
is kept.

Run it from an elevated PowerShell, with the account signed out:

    powershell -ExecutionPolicy Bypass -File tools\release_test_user.ps1 -Version 1.2.0 -Previous 1.1.0
    powershell -ExecutionPolicy Bypass -File tools\release_test_user.ps1 -Remove

-WhatIf says what it would do, and needs no elevation.

.PARAMETER Version
The draft's version, as in its tag without the v.

.PARAMETER Previous
The latest published release's version, which the update is tried from. Left out for the
first release.

.PARAMETER Name
The account's name. Never a real person's: it shows on the sign-in screen.

.PARAMETER Remove
Delete the account and its profile, and create nothing.
#>
[CmdletBinding(SupportsShouldProcess, DefaultParameterSetName = 'Create')]
param(
    [Parameter(ParameterSetName = 'Create', Mandatory)]
    [string]$Version,
    [Parameter(ParameterSetName = 'Create')]
    [string]$Previous,
    [string]$Name = 'BartleTest',
    [Parameter(ParameterSetName = 'Remove', Mandatory)]
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

# Loaded here with -WhatIf off, or loading it on first use prints a "What if" for each of
# the module's aliases.
& { $WhatIfPreference = $false; Import-Module CimCmdlets }

# BUILTIN\Users by its well-known SID, since the group's name is translated on a
# non-English Windows. New-LocalUser adds the account to no group, and without this one
# it cannot sign in.
$UsersGroupSid = 'S-1-5-32-545'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin -and -not $WhatIfPreference) {
    Write-Error 'Run this from an elevated PowerShell (Run as administrator), or with -WhatIf.'
}

$user = Get-LocalUser -Name $Name -ErrorAction SilentlyContinue
if ($user) {
    $userProfile = Get-CimInstance Win32_UserProfile | Where-Object SID -eq $user.SID.Value
    if ($userProfile -and $userProfile.Loaded) {
        Write-Error "$Name is signed in. Sign it out (Task Manager, Users, Sign off), then run this again."
    }
    if ($userProfile -and $PSCmdlet.ShouldProcess($userProfile.LocalPath, 'Delete the profile')) {
        # Deleting the profile through WMI removes its folder and its registry hive
        # together, as Settings does. Deleting the folder alone leaves Windows a profile
        # entry pointing at nothing, and a temporary profile at the next sign-in.
        $userProfile | Remove-CimInstance
    }
    if ($PSCmdlet.ShouldProcess($Name, 'Delete the account')) {
        Remove-LocalUser -SID $user.SID
    }
}

if ($Remove) {
    return
}

if ($PSCmdlet.ShouldProcess($Name, 'Create a standard account')) {
    $password = Read-Host -AsSecureString "A password for $Name (Enter for none)"
    # Windows allows a local account's description 48 characters at most.
    $description = 'BartleBot draft tests; release_test_user.ps1'
    if ($password.Length -eq 0) {
        New-LocalUser -Name $Name -NoPassword -Description $description | Out-Null
    } else {
        New-LocalUser -Name $Name -Password $password -PasswordNeverExpires -Description $description | Out-Null
    }
    Add-LocalGroupMember -SID $UsersGroupSid -Member $Name
}

# Every interactive user may change files under C:\Users\Public, so the test account can
# tick the copy without a grant of its own.
$draftDir = Join-Path $env:PUBLIC "BartleBot\$Version"
$checklist = Join-Path $draftDir 'RELEASE_CHECKLIST.md'
if (Test-Path $checklist) {
    Write-Host "Kept the checklist already in $draftDir, and its ticks."
} elseif ($PSCmdlet.ShouldProcess($checklist, 'Copy the checklist')) {
    $text = [IO.File]::ReadAllText((Join-Path $PSScriptRoot '..\docs\RELEASE_CHECKLIST.md'), [Text.Encoding]::UTF8)
    $text = $text.Replace('<version>', $Version)
    if ($Previous) {
        $text = $text.Replace('<previous>', $Previous)
    }
    New-Item -ItemType Directory -Force $draftDir | Out-Null
    [IO.File]::WriteAllText($checklist, $text, [Text.UTF8Encoding]::new($false))
}

if ($PSCmdlet.ShouldProcess($Name, 'Make the profile, with the checklist on its desktop')) {
    # A profile is otherwise made at the first sign-in, too late to put anything on its
    # desktop. CreateProfile makes it now, from the Default profile, as that sign-in would.
    Add-Type -Namespace BartleBot -Name UserEnv -MemberDefinition @'
[DllImport("userenv.dll", CharSet = CharSet.Unicode)]
public static extern int CreateProfile(string pszUserSid, string pszUserName, System.Text.StringBuilder pszProfilePath, uint cchProfilePath);
'@
    $profilePath = [Text.StringBuilder]::new(260)
    $result = [BartleBot.UserEnv]::CreateProfile((Get-LocalUser -Name $Name).SID.Value, $Name, $profilePath, 260)
    if ($result -ne 0) {
        Write-Error ('CreateProfile failed: 0x{0:X8}' -f $result)
    }
    $desktop = Join-Path $profilePath.ToString() 'Desktop'
    New-Item -ItemType Directory -Force $desktop | Out-Null
    # Notepad by name, since a new account may have nothing that opens .md.
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop "Release checklist $Version.lnk"))
    $shortcut.TargetPath = Join-Path $env:WINDIR 'System32\notepad.exe'
    $shortcut.Arguments = "`"$checklist`""
    $shortcut.Save()
    Write-Host "$Name is ready, with the checklist on its desktop."
}
