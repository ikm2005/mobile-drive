<!-- mobile-drive:routing -->
## Mobile app interaction — accessibility tree first (mobile-drive)

For **any** interaction with a mobile app on an emulator, simulator or device — looking at a
screen, tapping, typing, navigating, testing, reproducing a bug, verifying a change — use the
**`mobile-drive:drive`** skill. Start with the accessibility tree (`agent-device snapshot -i`) and
act on refs. Screenshots and coordinate taps are the **fallback**, for screens the tree can't
answer (canvas, game, custom-drawn view, a visual check). Use them there so the task still
completes, not as the first move. Replay a saved flow from `.claude/mobile-drive/flows/` in one
call; hand walks of more than ~5 steps to the **`mobile-drive:mobile-runner`** agent.
