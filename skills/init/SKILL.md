---
name: init
description: One-time, opt-in project setup for mobile-drive — creates .claude/mobile-drive/ (app-facts.md and a flows folder) in the current project and, only if the user agrees, adds a short routing rule to CLAUDE.md.
disable-model-invocation: true
argument-hint: "[--user]"
---

# mobile-drive init

Project state lives outside the plugin, because the plugin's own folder is replaced on every
update. With no argument, the state folder is `<project root>/.claude/mobile-drive/`. With
`--user`, it is `~/.claude/mobile-drive/` (for apps with no project folder, such as a third-party
APK).

1. Create `<state>/flows/` if missing.
2. If `<state>/app-facts.md` does not exist, copy
   `${CLAUDE_PLUGIN_ROOT}/skills/drive/app-facts.template.md` there. **Never overwrite** an
   existing one.
3. If `<state>/flows/README.md` does not exist, write it with exactly this content:

   ```
   # Saved flows

   Replayed by the mobile-drive plugin. Hardening checklist: the plugin's `skills/drive/flows-guide.md`.

   | Flow | Status |
   |---|---|
   | _none yet_ | |
   ```

4. **Ask the user** whether to add the routing rule to a `CLAUDE.md`. It is optional: the skill
   description already routes most mobile tasks, but the rule makes delegation to
   `mobile-drive:mobile-runner` more reliable. Offer three answers: the project `CLAUDE.md`,
   `~/.claude/CLAUDE.md`, or neither.
   - If they pick one, and that file does not already contain `<!-- mobile-drive:routing -->`,
     append the contents of `${CLAUDE_PLUGIN_ROOT}/templates/routing.md`, preceded by one blank
     line. If the marker is already there, leave the file unchanged.
5. Report what you created or skipped, file by file, in a few lines. Remind the user to fill in
   `app-facts.md` (optional) and to sign the app in to a **test account** themselves.

Touch nothing else: no `settings.json`, no hooks, no other files.
