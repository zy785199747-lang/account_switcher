# Riot Account Switcher v1.1.4

- Fixed Stop switch getting stuck while waiting for the Riot Client login window.
- Added a short wait for old Riot Client windows to close before relaunching, reducing paste-into-stale-window failures.
- Added regression tests for switch cancellation and stale-window handling.
