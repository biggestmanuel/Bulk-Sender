# AGENTS.md

Working notes for this repository. Written from mistakes that were actually made
here, so read them as scar tissue rather than style preferences.

## Verify before you commit

The most expensive recurring mistake in this repo has been **asserting a
severity you had not checked**, then having to walk it back.

- A filter that looked like a live duplicate-delivery bug
  (`[c for c in contacts if c.get('status') != 'sent']`, which filters on a key
  that does not exist) turned out to be **latent, not live**:
  `_send_to_contact` catches every exception itself, so the handler it feeds is
  unreachable mid-loop. It was still worth fixing — but the honest description
  was "latent", and a probe was what established that.
- Twelve "pre-existing failures" were caused by the agent's own shell:
  `WHATSAPP_DISABLE_AUTOMATION=true` short-circuits `send_whatsapp_messages`
  before `_one_pass`, so the automation tests were asserting against a stub.

**Before calling something a bug:** write a throwaway probe that demonstrates
it, and state reachability explicitly. If the probe shows the path is
unreachable, say so in the code comment, the test docstring, and the commit
message. A test that fabricates an impossible scenario to look convincing is
worse than no test.

## Prove each fix fails without the fix

The single highest-yield habit here. Every real bug found in the last two
passes was caught by reverting the change and confirming the new test goes red:

- the duplicate-delivery filter: revert → `[(1,'sent'), (2,'sent'), (1,'failed'), (2,'failed')]`
- the dead-browser abort: revert → `send_attempts` 2 → 5, `logged_in` False → True
- `retry_campaign`: revert → *"a delivered contact was passed to the browser a second time"*

A test that passes both before and after the change is not evidence. Check it.

## `DEBUG` in your shell will break the build

This is not hypothetical — it stalled a whole session. This machine's shell
exports `DEBUG='-LH:*, LH:*:error'` (captured reporter output). Both tools read
that variable for unrelated purposes:

- **Django**: `settings.env_bool` rejects unrecognised booleans *on purpose*, so
  every `manage.py test` died at import with `ImproperlyConfigured`.
- **Vite**: reads `process.env.DEBUG` as a `debug` namespace filter **while
  building the config**, before `test.env` would apply — so the run collapses
  to worker-startup timeouts and only part of the suite executes.

Both are now defended: `backend/config/settings_test.py` pins `DEBUG`,
`SECRET_KEY`, `CRYPTOGRAPHY_KEYS` and `WHATSAPP_DISABLE_AUTOMATION` before
importing settings; `frontend/vite.config.js` does `delete process.env.DEBUG`
ahead of `defineConfig()`. **Do not "simplify" either back into a `test.env`
block** — that was tried and it silently broke 7 of 10 test files.

If a whole suite suddenly fails at import, or a fraction of the frontend tests
run, suspect the ambient environment before suspecting the code.

## "It's just flaky" is usually wrong here

`CsvUpload.test.jsx > names the pending file in the prompt` failed on roughly a
quarter of runs and was written off as machine load. It was not load — it
reproduced 2 times in 8 **in isolation**. The cause was an ambiguous matcher:
the filename renders twice (once in "Loaded …", once inside the merge/replace
prompt), the query matched the first, and React detached that node when the
parse completed and the prompt appeared.

Two lessons. When a test is flaky, reproduce it **alone** before deciding it is
environmental. And a flaky test is a real defect until proven otherwise — it
burns a future session's time and invites a bogus "fix" for a phantom.

## Commands

Run these exactly. Both suites pin the environment, so no `set DEBUG=...` is
needed — and setting one by hand is how you break them.

```bash
# Backend — 367 tests
cd backend
python manage.py test sender --settings=config.settings_test

# Frontend — 173 tests
cd frontend
npm test
npm run lint
npm run build

# Smoke — 29 checks, needs a live server
cd backend
# terminal 1
set WHATSAPP_DISABLE_AUTOMATION=true   # Windows; export on macOS/Linux
python manage.py runserver 127.0.0.1:8777 --noreload
# terminal 2
python smoke.py
```

`smoke.py` deliberately uses the **real** settings module, so a genuinely
invalid `DEBUG` fails loudly there. That is correct — do not make it hermetic.

Do **not** set `WHATSAPP_DISABLE_AUTOMATION` for the unit suite. It is a
smoke-test escape hatch; it marks contacts sent without contacting WhatsApp.

## Don't truncate command output

`git status -sb | Select-Object -First 2` hid uncommitted changes and briefly
made it look like a fix had been lost. Read the whole thing, or filter with
`-U0` on an explicit path. Same for `Select-Object -Last N` over test output:
a failure you did not read is not a failure you have diagnosed.

## Before declaring done

Run all four: backend tests, `npm test`, `npm run lint`, and the smoke test.
Then `git status` to confirm the worktree is what you think it is.

Commit messages in this repo explain *why*, including reachability and what was
verified by reverting. Match that. Keep the subject free of a UTF-8 BOM — write
the file with `[System.IO.File]::WriteAllText`, not `Set-Content -Encoding UTF8`.