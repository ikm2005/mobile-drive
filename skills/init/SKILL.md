---
name: init
description: One-time, opt-in project setup for mobile-drive — creates .claude/mobile-drive/ (app-facts.md, a flows folder, and ui-check.json with the design source screens are compared against) in the current project and, only if the user agrees, adds a short routing rule to CLAUDE.md.
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

4. **Screen checks (`mobile-drive:ui-check`).** If `<state>/ui-check.json` does not exist, ask
   the user **which design screens should be compared against**. There are four answers:
   - `reference-app`: another platform's build is right. Also ask which one, `ios` or `android`.
   - `figma`: Figma frames. Also ask for the file key from the URL. Tell the user to set their own
     `FIGMA_TOKEN` in the shell environment for the zero-token import, or to connect the Figma MCP
     instead. Never ask for the token in chat.
   - `baseline`: approve a screen once, then catch regressions.
   - `spec`: hand-written specs.
   - "skip" is also an answer.

   Write the answer as `{"source": "<answer>", …}` plus `reference` or `figma.file_key`. Every other
   key keeps its default (`${CLAUDE_PLUGIN_ROOT}/skills/ui-check/spec-format.md`). Then create
   `<state>/.gitignore` containing `reports/` if it is missing. Run reports hold screenshots, which
   should not be committed.
5. **Ask the user** whether to add the routing rule to a `CLAUDE.md`. It is optional: the skill
   description already routes most mobile tasks, but the rule makes delegation to
   `mobile-drive:mobile-runner` more reliable. Offer three answers: the project `CLAUDE.md`,
   `~/.claude/CLAUDE.md`, or neither.
   - If they pick one, and that file does not already contain `<!-- mobile-drive:routing -->`,
     append the contents of `${CLAUDE_PLUGIN_ROOT}/templates/routing.md`, preceded by one blank
     line. If the marker is already there, leave the file unchanged.
6. Report what you created or skipped, file by file, in a few lines. Remind the user to fill in
   `app-facts.md` (optional) and to sign the app in to a **test account** themselves.

Touch nothing else: no `settings.json`, no hooks, no other files.
