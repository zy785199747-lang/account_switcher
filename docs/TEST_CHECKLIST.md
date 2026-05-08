# Manual Test Checklist

Tick each box **before** tagging the corresponding release.
Automated tests live in `tests/` and run with `.venv\Scripts\python.exe -m pytest`.

---

## Phase 1 — Vault & master password (`v0.1-vault`)

### Automated (`pytest tests/`)
- [x] 10 crypto tests pass
- [x] 17 vault tests pass
- [x] All modules import cleanly (`python -c "import main"`)
- [x] Dialogs construct headless without errors

### Manual (run `.venv\Scripts\python.exe main.py`)
1. **First launch (no vault)**
   - [ ] "Set Master Password" dialog appears
   - [ ] Empty password is rejected with inline error
   - [ ] Mismatched passwords are rejected with inline error
   - [ ] Password shorter than 8 chars triggers a warning dialog (Yes/No)
   - [ ] Submitting a valid password closes the dialog
   - [ ] Phase 1 placeholder window appears showing "Vault unlocked"
   - [ ] File `%APPDATA%\RiotAccountSwitcher\vault.enc` now exists

2. **Restart and unlock**
   - [ ] Close the app
   - [ ] Run `.venv\Scripts\python.exe main.py` again
   - [ ] "Unlock Vault" dialog appears (not "Set Master Password")
   - [ ] Wrong password shows red error and clears the field
   - [ ] Counter shows "4 attempts left", then "3", etc.
   - [ ] After 5 wrong attempts, lockout banner shows for ~30s
   - [ ] Correct password closes dialog and shows the placeholder window

3. **Logs**
   - [ ] File `%APPDATA%\RiotAccountSwitcher\logs\app.log` was written
   - [ ] Running with `--debug` makes the console output verbose

4. **Cancel paths**
   - [ ] On Set: clicking Cancel exits cleanly (no vault file written)
   - [ ] On Unlock: clicking Cancel exits cleanly (vault still exists, untouched)

---

## Phase 2 — Account CRUD UI (`v0.2-crud`)

### Automated
- [x] All Phase 1 tests still pass (27/27)
- [x] Headless smoke test: AccountCard + AddAccountDialog + MainWindow construct (with 3 cards and empty-state)

### Manual

**Easy seed:**
Run `.venv\Scripts\python.exe scripts\seed_test_vault.py` to wipe-and-reseed the vault with 3 fake accounts. Master password: `test1234`.

1. **Empty state**
   - [ ] Delete `%APPDATA%\RiotAccountSwitcher\vault.enc`, run `main.py`, set new password
   - [ ] Main window opens with toolbar (Add Account / Refresh Ranks / Settings / Lock) and a centered "No accounts yet" message

2. **Add Account**
   - [ ] Click *Add Account* → dialog opens
   - [ ] Submitting empty fields shows red inline error
   - [ ] *Show* button toggles password between dots and plaintext
   - [ ] Click OK with valid fields → dialog closes, card appears in the grid

3. **Card display**
   - [ ] Card shows `gameName#tagLine`, region in caps, "Rank not loaded yet"
   - [ ] Hover over card → ✏️ and 🗑️ icons appear in the top-right corner
   - [ ] Move mouse off the card → icons disappear

4. **Edit**
   - [ ] Hover → click ✏️ → dialog opens with all fields prefilled, title says "Edit Account"
   - [ ] Change a field, click OK → card updates with the new values
   - [ ] Edit again, click Cancel → no changes

5. **Delete**
   - [ ] Hover → click 🗑️ → confirmation popup with the Riot ID
   - [ ] Click No → card stays
   - [ ] Click 🗑️ again, click Yes → card disappears

6. **Right-click menu**
   - [ ] Right-click on a card → menu shows Switch / Edit / Delete / Refresh rank
   - [ ] Each item triggers the same dialog as the equivalent button/icon

7. **Click to switch (Phase 4 stub)**
   - [ ] Left-click anywhere on the card body (not the icons) → "Switch (Phase 4)" stub message appears

8. **Refresh Ranks (Phase 3 stub)**
   - [ ] Toolbar *Refresh Ranks* → "Refresh Ranks (Phase 3)" stub message

9. **Settings (Phase 5 stub)**
   - [ ] Toolbar *Settings* → "Settings (Phase 5)" stub message

10. **Lock**
    - [ ] Toolbar *Lock* → window closes, unlock dialog reappears
    - [ ] Wrong password keeps dialog open
    - [ ] Right password → main window opens again with all cards intact

11. **Persistence**
    - [ ] Add 2 accounts, close window (X) → relaunch `main.py` → unlock → both cards present, in same order

12. **Multiple cards**
    - [ ] Run `seed_test_vault.py`, launch app with password `test1234` → see 3 cards in a 3-wide grid
    - [ ] Add a 4th account → grid wraps to second row

---

## Phase 3 — Riot API + AdminWindow (`v0.3-api`)

### Automated
- [x] All previous tests still pass (53/53 = 27 vault + 10 crypto + 16 API URL tests)
- [x] Headless smoke test: AccountCard renders fresh/stale/unranked, banner toggles, AdminWindow constructs

### Manual

**You'll need a Riot dev API key** for the API portions. Get one at https://developer.riotgames.com/. The key expires in 24h — that's expected and is what we're testing for.

1. **Admin window opens via flag**
   - [ ] `.venv\Scripts\python.exe main.py --admin` → unlock dialog → AdminWindow appears
   - [ ] Two buttons at bottom of dev-portal links open browser tabs
   - [ ] *Show* toggles the API key field between dots and plaintext

2. **Test key**
   - [ ] Paste a valid key, click *Test key* → green ✓ "key is valid", "Last successful call" updates
   - [ ] Replace with a clearly bad key (e.g. "RGAPI-broken"), click *Test key* → red ✗ "key rejected"
   - [ ] Empty field + *Test key* → ✗ "no key entered"

3. **Save key**
   - [ ] Paste valid key → *Save* → "API key saved" popup
   - [ ] Close admin window → run `python main.py` (no flag) → main window opens normally
   - [ ] Walk every menu/dialog: confirm API key is NOT visible anywhere in the user UI

4. **Verify in Add Account dialog**
   - [ ] With valid key set: Add Account → fill form with a real Riot ID → click *Verify* → green ✓ "Found: Diamond II 47 LP" (or whatever the real rank is)
   - [ ] Verify with a bogus Riot ID like `Nope#Z9Z9Z9` → red ✗ "Player not found"
   - [ ] Submit Save → card appears with the rank already filled in

5. **Refresh Ranks toolbar**
   - [ ] With valid key + at least 1 account: click *Refresh Ranks* → status bar briefly shows "Ranks refreshed at HH:MM:SS"
   - [ ] Card text updates if rank changed (compare to before)

6. **API failure banner (informational only)**
   - [ ] Run `python main.py --admin`, paste a deliberately broken key, Save
   - [ ] Run `python main.py` → click *Refresh Ranks* → soft yellow/amber banner appears reading exactly *"Rank info is temporarily unavailable"* with NO buttons
   - [ ] Cards still show their cached ranks
   - [ ] Click *Add Account* → Verify button is HIDDEN (form still works without it)

7. **Banner auto-clears**
   - [ ] Run `python main.py --admin` again, paste good key, Save
   - [ ] Run `python main.py` → click *Refresh Ranks* → banner disappears, ranks update

8. **Stale tag**
   - [ ] After a successful refresh, leave the app running for ≥24h (or temporarily edit the system clock forward) → stale tag "(updated Xh ago)" appears under the rank text
   - [ ] Push beyond 7 days → tag turns amber

9. **Right-click refresh single card**
   - [ ] Right-click a card → *Refresh rank* → that one card updates (others untouched)

---

## Phase 4 — Launcher + auto-fill (`v0.4-switch`)

(filled in when Phase 4 starts)

---

## Phase 5 — Polish (`v0.5-polish`)

(filled in when Phase 5 starts)

---

## Phase 6 — Documentation (`v1.0`)

(filled in when Phase 6 starts)
