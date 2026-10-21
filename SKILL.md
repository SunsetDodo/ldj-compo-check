---
name: ldjam-compo-check
description: Audit a Ludum Dare (ldjam.com) Compo entry for rule compliance and AI use, starting from the game's ldjam.com page. Retrieves the source (GitHub/GitLab/download/itch.io) or reports NO SOURCE AVAILABLE / FAILED TO RETRIEVE, finds the commit matching the Compo deadline (ignoring post-jam versions) and matches the played itch.io build to it, measures what was created or edited during the 48h (files, LoC, artwork, raw .aseprite/.bosca files, file types), gathers AI-use and human-process evidence, gives probability-weighted NO_AI / AI_CONSULTED / AI_ASSISTED / AI_GENERATED verdicts for Overall, Idea, Code, Artwork, Music & sound and Documentation with all supporting evidence, and compares the rating opt-outs the entry should have made with the ones it made. Use whenever someone shares an ldjam.com game link and asks whether it follows the Compo rules, used AI, was really made in 48 hours, should have opted out of Graphics/Audio – or just pastes an LD game link and says "check this".
---

# Ludum Dare Compo check

Turns an ldjam.com game page into an evidence-based compliance report. Scripts do the mechanical work (fetching,
git archaeology, counting, pattern scanning); you do the judgment (reviewing flagged files, assigning
probabilities). Every decision in the output carries a probability and the evidence behind it – never a bare
yes/no – because AI detection and timestamp evidence are both uncertain, and the reader needs to see why.

Pipeline (one work directory per game, e.g. `ldcheck-<slug>/`):

| Stage | Script | Output |
|---|---|---|
| 0 Page | `ldjam_page.py` | `game.json` – game, event window, links, opt-outs, description, comments, author posts |
| 1 Source | `get_source.py` | `stage1.json` – status + certainty, `source/`, played build in `build/` |
| 2 Static | `analyze_static.py` | `stage2.json`, `stage2.md` – timeline, counts, LoC, flags |
| 3 AI | `scan_ai.py` + your review | `stage3_signals.*`, then `verdicts.json` written by you |
| 4 Report | `build_report.py` | `report.md` – verdicts, opt-out audit, rule probabilities, all evidence |

Scripts live in `scripts/` next to this file (use its absolute path). Python 3.9+; `pip install requests pillow`
if missing (`py7zr`/`rarfile` install themselves when an archive needs them).

## Stage 0 – read the game page
```
python3 <skill>/scripts/ldjam_page.py <ldjam game URL> --out ldcheck-<slug>
```
- Accepts `https://ldjam.com/events/ludum-dare/<N>/<game>` or `ldj.am/$<id>`. If the user gives an itch.io link,
  ask for the ldjam page (or find it on the itch page). Events before LD38 (ludumdare.com/compo) aren't in this
  API: analyse a provided source with `--event-start` overrides instead.
- **Exit code 3** = api.ldjam.com unreachable from this sandbox. Follow `references/fetching.md` (web-fetch the
  listed URLs, save the JSON into `raw/`, rerun). Fetch the optional comments/posts too – they are evidence.
- Read the WARNING lines. A Jam/Extra entry isn't bound by Compo-only rules (solo, everything in 48h, source):
  say so, and continue only with what still applies (AI verdicts, opt-out audit) unless the user wants more.
- The window comes from the event's `event-start`: Compo = start + 48h, Submission Hour = +1h. Override with
  `--event-start` only if the user says the data is wrong.

## Stage 1 – get the source (three outcomes)
```
python3 <skill>/scripts/get_source.py --workdir ldcheck-<slug>
```
It classifies every link (ldjam links with the Source Code tag, URLs in the description, itch.io uploads and the
itch description), clones git repos with full history, downloads archives, and checks that what it got actually
contains source (project files or readable code), not just a build. The result has a `certainty` with its basis.

1. **NO_SOURCE_AVAILABLE** – Compo entries must include source, so report this as a rule violation, *with* the
   certainty and what was checked. Read the warnings first: web/PICO-8/LÖVE builds can embed readable source –
   if the played build contains code, analyse it with `--local <build path>` before concluding. Then ask whether
   the user still wants the AI review of the page and build.
2. **FAILED_TO_RETRIEVE** – a source exists or may exist but couldn't be fetched here. Ask the user to provide it,
   quoting the `ACTION:` lines (the exact upload or repo). If itch.io pages were only unreachable, first try the
   itch fallback in `references/fetching.md` – it often settles the outcome without bothering the user. When
   files arrive: `get_source.py --workdir … --local <path>`.
3. **SOURCE_FOUND** – continue.

Post-jam versions: the script prefers itch uploads labelled as the compo/jam version over "post-jam/updated/v1.1"
ones, and fetches the build players actually get (labelled download or the embedded web build) into `build/`.
If it couldn't, and Stage 2 then shows commits after the deadline, ask the user (optionally) for the played build
(`--build-local`) – it lets Stage 2 prove which commit raters are playing.

## Stage 2 – static analysis
```
python3 <skill>/scripts/analyze_static.py --workdir ldcheck-<slug>
```
- **Git**: picks the compo version = an explicit compo tag/branch or the ldjam-linked ref if its history predates
  the deadline, else the last commit whose whole history predates deadline + Submission Hour. Later commits are
  listed and classified (bug-fix/port vs content/feature) and never analysed as the compo version. The cutoff is
  checked out to `snapshot/`. Per-file creation and last-edit times follow renames. Overrides: `--cutoff <sha>`,
  `--ref <branch/tag>`, `--subpath <dir>` (multi-game repos are detected automatically).
- **Archives**: timestamps stored in the archive (UT/NTFS/DOS/7z/tar); creation is inferred only for files untouched
  since before the event. **Folders**: filesystem times, usually useless.
- **Every asset**: embedded timestamps (PNG tIME, XMP history, EXIF, Krita, BWF, ID3, fonts) and generator metadata.
- **Played build** (if any): matched to the commit it was built from (identical files + Godot .pck resource lists).
- `stage2.md` answers the user's Stage-2 questions: files created during the Compo out of the whole project, files
  last edited before the end, LoC written during the event (own code files created in the window), artwork files,
  raw files (.aseprite, .ceol/.bosca, .psd, .kra, .blend…), counts per file type, plus a **timeline reliability**
  percentage and rule flags. Show this summary to the user (compact) and keep going.

## Stage 3 – AI usage
```
python3 <skill>/scripts/scan_ai.py --workdir ldcheck-<slug>
```
This collects signals per area with strengths 1–4 (AI tool files, agent commit trailers, comment patterns, emoji
logs, doc coverage, unused "complete API" functions, Godot 3/4 mix-ups, velocity bursts, LoC feasibility, image
metadata/canvas sizes/fake pixel art/baked checkerboards, audio metadata, README/description style, disclosures
in the description and comments, progress posts, time-lapses…) and an automatic prior distribution per category.

The prior is only a starting point. Then **review the evidence yourself** – read `references/ai_signals.md` first
(checklist and false positives, e.g. engine template comments):
- read the queued code files (most AI-looking and most human-looking) and `git show --stat` the flagged commits;
- view the queued images with Read;
- read the README, the description and every comment;
- open any AI tool files – they often state what the agent built (extent).

Then write `ldcheck-<slug>/verdicts.json` following `references/classification.md`: for each of the six
categories a probability distribution over NO_AI / AI_CONSULTED / AI_ASSISTED / AI_GENERATED (or `N/A`),
**all** evidence for and against with file/commit/URL references and weights, and a one-line reasoning; plus
`generators` (non-AI generators like sfxr, with the probability they did ≥90% of a category), `premade_content`
probabilities, and a short summary.

## Stage 4 – report
```
python3 <skill>/scripts/build_report.py --workdir ldcheck-<slug>
```
`report.md` contains: Stage 1 status with certainty; the Stage 2 summary; the AI table (most likely level,
certainty, P(any AI use), full distribution) with every piece of evidence per category; the opt-out audit –
**P(should opt out) vs what was actually opted out** for every rating category, with the reasoning; the Compo
rules as **P(violated)** each with evidence for/against; and appendices listing every automated signal and flag.
Fix anything listed under "Report consistency warnings" and rerun.

## Presenting the result
Send `report.md` as a file (plus `stage2.md`), and in the chat give a short summary – the user may be on a phone:
1. one line: the highest rule-problem probability and what it is;
2. Stage 1 status + certainty;
3. the Stage 2 numbers (created during / edited before end / LoC / artwork / raw files / main file types);
4. the six AI verdicts as `level – certainty% (P(any AI) %)`;
5. opt-outs: each category where P(should) ≥ 15% or that was opted out, as `P(should) vs actual → assessment`;
6. the 3–5 strongest pieces of evidence, and anything you need from the user.
Use percentages and words like "likely"/"very likely" – never a bare yes/no.

## Fairness and limits
- AI use itself is allowed in Ludum Dare, including the Compo; the rule questions are opt-outs, pre-made content,
  solo work, timing and source. Keep these separate in what you write.
- Evidence of AI is probabilistic, timestamps can be edited, and absence of evidence isn't proof. State
  uncertainty plainly, mention innocent explanations you considered, and never call something proven without
  decisive evidence.
- Many entries: run each in its own work directory and finish with a one-row-per-game table.

## Reference files
- `references/ld_rules.md` – Compo rules, AI FAQ, opt-out mechanics, finding → outcome table. Read when judging
  rule questions.
- `references/classification.md` – level definitions, probability calibration, Overall rule, opt-out formulas,
  `verdicts.json` schema with examples. Read before writing verdicts.
- `references/ai_signals.md` – signal catalog, manual review checklist, false positives. Read before reviewing.
- `references/fetching.md` – what to do when api.ldjam.com/itch.io/file hosts are blocked; asking for files.
