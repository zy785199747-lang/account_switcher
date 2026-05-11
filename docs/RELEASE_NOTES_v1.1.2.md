# Riot Account Switcher v1.1.2

Status-bar feedback polish.

- Status bar now shows live progress for rank refresh, account switch, and
  update operations (e.g. "Refreshing ranks...", "Downloading update...").
- Successful switch now confirms with **"Logged in as `<RiotID>`."** instead
  of a generic "Logged in." message.
- "No update available" dialog now displays the current version.
- Permanent **Version X.X.X** label added to the right side of the status bar.
- Internal: `SwitchWorker.finished` signal now carries the `riot_id` string.
