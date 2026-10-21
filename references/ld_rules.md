# Ludum Dare rules relevant to a Compo audit

Sources (checked October 2026):
- Rules page – https://ludumdare.com/resources/rules/ (also https://ldj.am/rules). The organisers note it was last
  substantially updated around 2020.
- FAQ "Can I use AI?" – https://ludumdare.com/resources/questions/can-i-use-ai/ (2025). Newer than the rules page
  and the explicit guidance on AI.
- ldjam.com submission checklist and opt-out UI (site source: github.com/JammerCore/JammerCore,
  `src/com/content-item/item-rulescheck.js`, `item.js`).

If the user says the rules changed for the event being checked, re-read the pages above (web search/fetch) and
prefer the current text.

## The Compo (vs the Jam)

| | Compo | Jam |
|---|---|---|
| Time | 48 hours + 1 Submission Hour | 72 hours + 1 Submission Hour |
| Team | solo only | solo or team |
| Content | all art, music, sound, etc. created during the 48h | pre-made / third-party assets allowed, with opt-outs |
| Source code | must be included | optional |

### Compo rules, as written
1. **Solo** – "You must work alone."
2. **Everything made in 48 hours** – the game and all content (art, music, sound, …) must be created during the
   48 hours; Compo games are "created entirely from scratch by one person".
3. **Tools, libraries, base code** – any tools or libraries may be used, and you may start from any base code you
   have. Everything needed to make the source work must be publicly available; internal libraries must be shared.
4. **Exemptions** (you must have the legal right to use them):
   - fonts;
   - a logo/intro screen for you or your brand;
   - photos and recordings of people or things, if acquired during the event;
   - content generators – allowed and "encouraged" (sfxr is the classic example);
   - texture masks, brushes, drums, loops, sampled instruments – only when used to create a derivative work.
5. **Source code must be included.** For tools without code (GameMaker, Construct, Bitsy…) the project file is the
   source.
6. **Submission Hour** – one extra hour after the 48h to package and submit; then Compo submissions close
   (later entries go to the Jam).
7. **After the deadline** – porting (e.g. to web/Windows) may happen later; bug fixes are allowed but **no new
   features**, and changes must be highlighted in a short change log.

### What the entrant ticks on ldjam.com when submitting to the Compo
- "I worked alone."
- "I created all the code, art, sound, music, and other assets myself during the event."
- "I created everything in 48 hours."
- "I have included source code."
- (all formats) "I have opted-out of all or any categories we are not eligible for."

## AI and content generators (FAQ, 2025)
- AI-powered tools **may be used, including in the Compo**. Assistant / "co-pilot" tools can be used without
  restriction.
- Entrants **should opt out of rating categories where a content generator did most of the work** – typically
  artistic categories such as Graphics, possibly others.
- **Non-AI generators are treated the same** as AI generators for opt-outs; the FAQ names Audio as the typical case
  (e.g. sfxr-only sound).
- "Most of the work" ≈ **90%** of the work in a category done by a generator.
- Transforming generated material into something new may make it a derivative work.
- Entrants are responsible for the legality of tools and their output.

How to reconcile with the Compo checklist ("I created all … myself"): the FAQ is newer and explicit, so treat AI
use as **allowed but subject to opt-outs**. What remains a hard Compo violation is *pre-made or someone else's*
content (non-generated assets made before the event or by others), not AI use itself.

## Rating categories and opt-outs
Categories: Overall, Fun, Innovation, Theme, Graphics, Audio, Humor, Mood (+ the special Coolness score).
- Which categories can be opted out of is set per event (`grade-XX-optional` on the event node). Overall cannot.
- The site asks entrants to opt out "if you and your team didn't make all your graphics, audio, or music during the
  event". Opting out of Humor, Mood or Theme is always optional (games not meant to be funny, etc.).
- Data: event node `meta["grade-0N"]` = category name; game node `meta["grade-0N-out"] = 1` = opted out.

## Mapping findings to outcomes

| Finding | Rule | Outcome |
|---|---|---|
| No source anywhere (links, description, itch uploads) | Source must be included | **Violation** |
| Source link dead/private and user can't get it either | Source must be included | **Violation** (confirm first) |
| More than one author on the page / collaborators credited | Solo | **Violation** (belongs in the Jam) |
| Several git identities | Solo | Review (often one person on two machines) |
| Art/audio made before the event, asset packs, Freesound, credits to others | Content made during the event | **Violation** unless an exemption applies |
| Fonts, brand logo/intro, sampled instruments in an original track | Exemptions | OK |
| Engine default icon (icon.svg/icon.png) | – | Ignore unless used as game art |
| Code files predating the event | Base code allowed | OK – note whether declared/public |
| Rated build contains post-deadline content/features | Bug fixes only | **Violation** |
| Post-deadline bug fixes / ports | Allowed with change log | OK (check the log) |
| Generated content (AI or not) ≥ ~90% of a category, not opted out | AI FAQ | **Opt-out violation** |
| Generated content is part but not most of a category | AI FAQ | Review – no opt-out required |
| AI-assisted / AI-generated code | AI FAQ (assistants unrestricted) | Allowed; no rating category maps to code |
| sfxr/jsfxr-only audio, Audio not opted out | AI FAQ (generators ≈ AI) | Advisory – commonly overlooked |
| Repository history starts after the deadline / one bulk commit | – | Not a violation; timing **unverifiable** |
