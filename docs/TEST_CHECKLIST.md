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

(filled in when Phase 2 starts)

---

## Phase 3 — Riot API + AdminWindow (`v0.3-api`)

(filled in when Phase 3 starts)

---

## Phase 4 — Launcher + auto-fill (`v0.4-switch`)

(filled in when Phase 4 starts)

---

## Phase 5 — Polish (`v0.5-polish`)

(filled in when Phase 5 starts)

---

## Phase 6 — Documentation (`v1.0`)

(filled in when Phase 6 starts)
