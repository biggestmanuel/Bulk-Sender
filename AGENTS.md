# AGENTS.md

Working notes for this repository. Written from mistakes that were actually made
here, so read them as scar tissue rather than style preferences.

## Commit and push after every file change

**Every file change gets its own commit, pushed immediately. Do not batch.**

**Never commit or push directly to `main`.** `main` receives merges only.
Work on a branch from an up-to-date `main`:

```bash
git switch -c <short-descriptive-name>
git push -u origin <branch>
```

Commit and push to your branch after every coherent change. One change, one
commit, pushed - so that a failure is always isolated to a single edit and
`git log` reads as an honest record rather than a summary.

Do not leave a change sitting in the working tree "until the work is finished".
Do not roll several edits into one commit. A commit is one coherent change
that leaves the repo consistent - not one file and not one keystroke. Two
unrelated fixes means two commits.

When you believe a branch is ready, say so, with your reasoning, CI status,
and a summary of the diff. Open the PR and wait. Merge only after the user
explicitly confirms. Reverting is a commit - history is append-only: if a
pushed commit was wrong, add a commit that undoes it and say what was wrong
in the message.

Two things make per-commit pushes safe to insist on:

- **CI runs on every push** and has been green on every push so far, so a broken
  commit surfaces immediately instead of after four more land on top of it.
- The repo is **public**, so an unpushed commit is a commit that does not exist
  for anyone reviewing the work, and a bad one stays bad longer.

Do the two gates below *before* each commit, not once at the end.

## Scan for hidden keys before every commit

This repo is public. Anything committed is world-readable from that moment, and
history is permanent - a secret removed in a later commit is still in the
clone. So scan first, commit second.

**The scan.** Look at what is about to be staged, and at the tree as a whole:

```bash
git status --short
git diff --cached
git grep -nE '(AKIA[0-9A-Z]{16})|(gh[pousr]_[A-Za-z0-9]{36})|(github_pat_)|(-----BEGIN [A-Z ]*PRIVATE KEY-----)|(npm_[A-Za-z0-9]{36})|(xox[abprs]-)|(sk_live_)|(AIza[0-9A-Za-z_-]{35})' -- . ':!AGENTS.md'
```

The `':!AGENTS.md'` is not optional. This file contains the pattern itself, so
without the exclusion the scan always reports one hit - on itself - and you
learn to ignore the output, which is exactly how a real key gets missed.

Also confirm nothing sensitive is *untracked-but-ignored*, which is how a real
key stays out of the commit while still sitting in the tree:

```bash
git status --porcelain --untracked-files=all --ignored | grep '^!!'
```

Every ignored entry should be `node_modules`, `dist`, `__pycache__`,
`backend/media`, `backend/whatsapp_session`, `backend/.cache`, or
`backend/db.sqlite3`. Anything else under `backend/sender` or `backend/config`
is a source file that a `.gitignore` pattern has silently swallowed - that looks
identical to "nothing to commit" while never reaching GitHub.

Strip the quotes before matching. Git quotes any path containing a space, so
Chromium's `Service Worker` and `Shared Dictionary` profile directories come back
quoted and will falsely fail a `-notlike 'backend/whatsapp_session/*'` test.
Exactly three such entries are expected noise; a genuine finding is a path under
`backend/sender` or `backend/config` that is not `__pycache__`.

**The two keys that are already committed, and why that is fine.**
`DEV_SECRET_KEY` and `DEV_CRYPTOGRAPHY_KEY` have been public since the initial
commit, and cannot be un-exposed without rewriting history. They are safe
*because* they are refused when `DEBUG=False`, they exist only to make a fresh
clone runnable, and the app warns loudly at startup when one is the live
encryption key. **A new secret is never acceptable** - generate it into the
environment, and never write it into a file that could be staged.

If a real key does get committed: rotate it first, then treat the history as
burned. Removing the line is not remediation.

## Verify before you commit

The most expensive recurring mistake in this repo has been **asserting a
severity you had not checked**, then having to walk it back.

- A filter that looked like a live duplicate-delivery bug
  (`[c for c in contacts if c.get('status') != 'sent']`, which filters on a key
  that does not exist) turned out to be **latent, not live**:
  `_send_to_contact` catches every exception itself, so the handler it feeds is
  unreachable mid-loop. It was still worth fixing - but the honest description
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

- the duplicate-delivery filter: revert - > `[(1,'sent'), (2,'sent'), (1,'failed'), (2,'failed')]`
- the dead-browser abort: revert - > `send_attempts` 2 -> 5, `logged_in` False -> True
- `retry_campaign`: revert - > "a delivered contact was passed to the browser a second time"

A test that passes both before and after the change is not evidence. Check it.

## `DEBUG` in your shell will break the build

This is not hypothetical - it stalled a whole session. This machine's shell
exports `DEBUG='-LH:*, LH:*:error'` (captured reporter output). Both tools read
that variable for unrelated purposes:

- **Django**: `settings.env_bool` rejects unrecognised booleans *on purpose*, so
  every `manage.py test` died at import with `ImproperlyConfigured`.
- **Vite**: reads `process.env.DEBUG` as a `debug` namespace filter **while
  building the config**, before `test.env` would apply - so the run collapses
  to worker-startup timeouts and only part of the suite executes.

Both are now defended: `backend/config/settings_test.py` pins `DEBUG`,
`SECRET_KEY`, `CRYPTOGRAPHY_KEYS` and `WHATSAPP_DISABLE_AUTOMATION` before
importing settings; `frontend/vite.config.js` does `delete process.env.DEBUG`
ahead of `defineConfig()`. **Do not "simplify" either back into a `test.env`
block** - that was tried and it silently broke 7 of 10 test files.

If a whole suite suddenly fails at import, or a fraction of the frontend tests
run, suspect the ambient environment before suspecting the code.

## "It's just flaky" is usually wrong here

`CsvUpload.test.jsx > names the pending file in the prompt` failed on roughly a
quarter of runs and was written off as machine load. It was not load - it
reproduced 2 times in 8 **in isolation**. The cause was an ambiguous matcher:
the filename renders twice (once in "Loaded .", once inside the merge/replace
prompt), the query matched the first, and React detached that node when the
parse completed and the prompt appeared.

Two lessons. When a test is flaky, reproduce it **alone** before deciding it is
environmental. And a flaky test is a real defect until proven otherwise - it
burns a future session's time and invites a bogus "fix" for a phantom.

## Commands

Run these exactly. Both suites pin the environment, so no `set DEBUG=...` is
needed - and setting one by hand is how you break them.

```bash
# Backend - 378 tests
cd backend
python manage.py test sender --settings=config.settings_test

# Frontend - 186 tests
cd frontend
npm test
npm run lint
npm run build

# Smoke - 28 checks, needs a live server
cd backend
# terminal 1
set WHATSAPP_DISABLE_AUTOMATION=true   # Windows; export on macOS/Linux
python manage.py runserver 127.0.0.1:8000 --noreload
# terminal 2
python smoke.py
```

On a memory-constrained machine `npm test` can fail to fork workers and report
"no tests". `npx vitest run --pool=threads` uses worker threads instead and
succeeds. That is an environment limit, not a code fault - but check before
concluding either way.

`smoke.py` deliberately uses the **real** settings module, so a genuinely
invalid `DEBUG` fails loudly there. That is correct - do not make it hermetic.

Do **not** set `WHATSAPP_DISABLE_AUTOMATION` for the unit suite. It is a
smoke-test escape hatch; it marks contacts sent without contacting WhatsApp.

## Don't truncate command output

`git status -sb | Select-Object -First 2` hid uncommitted changes and briefly
made it look like a fix had been lost. Read the whole thing, or filter with
`-U0` on an explicit path. Same for `Select-Object -Last N` over test output:
a failure you did not read is not a failure you have diagnosed.

## Before each commit

Three gates, in order. All three, every time - not once at the end of a batch.

1. **Tests.** Backend tests, `npm test`, `npm run lint`, and the smoke test for
   anything touching the send path. A commit that does not pass is not a commit.
2. **Key scan.** The `git grep` and ignored-file checks above. This repo is
   public; a staged secret is a published secret.
3. **`git status`.** Confirm the worktree holds what you think it does - no
   stray probe scripts, no half-written files, nothing unrelated swept up by
   `git add -A`.

Then commit and push, one change at a time.

Commit messages in this repo explain *why*, including reachability and what was
verified by reverting. Match that. Keep the subject free of a UTF-8 BOM - write
the file with `[System.IO.File]::WriteAllText`, not `Set-Content -Encoding UTF8`.

## Filename casing

This file is `AGENTS.md`, not `AGENTS.MD`. Windows will happily treat those as
the same file, but git records the casing it was added with, and `AGENTS.md` is
both what is tracked and the name tooling looks for.