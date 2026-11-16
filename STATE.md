# Mars paper sims: where they run (2026-11-14 21:53 Bogota)

All 3 sims are PAPER and still running locally on the box. None were stopped (the stop request was cancelled before anything was touched).
- Sim 1 HL scalp v4 (/workspace/mars-larb-paper): supervisor 100203, bot 100207 alive, equity $25.00
- Sim 2 TG (/workspace/mars-tg-paper): supervisor 101341, sim 101343 alive, cash $25.00, 0 positions
- Sim 3 macro (/workspace/mars-macro-paper): supervisor 100920, bot 455130 alive, equity $25.00
- The "Sim 1 daily tweak" routine is enabled. Its next run is 2026-11-15 10:31 Bogota.

## Moving to GitHub Actions: BLOCKED
- The bundle in /workspace/mars-gh-sims is ready: all 3 sims, scan.sh came back clean, and a dry run showed all 3 alive.
- BLOCKED: gh CLI is not logged in, and device code 7560-DB1B expired (/workspace/gh-login.out). The GitHub connector is read-only (it has no repo-create or file-write tools).
- The step that needs the Captain: run `gh auth login --web` for a new code, then he signs in as clawdbro9-stack at https://github.com/login/device on Mars's box and enters the code.
- After that, Mars runs `gh repo create clawdbro9-stack/mars-paper-sims --public` and then golive.sh, which pushes the bundle, starts sims.yml and stops the local copies.
- Caveat: Yahoo may block GitHub runners, which would hit sim 3's stock prices.
