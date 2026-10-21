# Classifying AI use (Stage 3 verdicts)

Classify six categories: **Overall, Idea, Code, Artwork, Music & sound, Documentation**.
Nothing is a bare yes/no: each category gets a **probability distribution over the four levels**, and **every
piece of evidence** you relied on – for and against – with a pointer to where it is.

## Levels

| Level | Meaning | Typical evidence |
|---|---|---|
| `NO_AI` | No AI involvement. | Human process traces, no AI artifacts or patterns after review |
| `AI_CONSULTED` | AI used as an advisor (questions, API explanations, debugging help, brainstorming); the content itself was authored by the person. | "Asked ChatGPT how to…"; an isolated AI-style helper in otherwise human code; AI tool present but no AI-authored content visible |
| `AI_ASSISTED` | AI produced a meaningful but minority share of the content, or AI output was substantially reworked. Human authorship dominates. | Some files/functions in agent style; AI trailers on some commits; an AI image repainted; AI audio edited |
| `AI_GENERATED` | AI produced most of the category (the FAQ's ~90% threshold); the person mostly prompted, selected and assembled. | Agent-written codebase; generated images used as-is; Suno/Udio tracks |
| `N/A` | The game has no content of this kind (e.g. no audio). | – |

Two separate questions: **was AI used?** (strength of evidence → P(any AI) = 100% − P(NO_AI)) and **how much of
the category came from it?** (extent → how the AI mass splits between CONSULTED / ASSISTED / GENERATED). One
decisive artifact (a `CLAUDE.md`, one AI-signed commit) makes P(any AI) high but says little about extent – look at
what the tool touched before moving mass to GENERATED.

## Assigning probabilities
1. Start from the scanner's automatic prior (`stage3_signals.md`, "Automatic prior"). It is crude: it only sees
   pattern counts.
2. Review the files (see ai_signals.md §1), then move probability mass for each finding.
3. Anchor the numbers:

| Evidence situation | P(any AI) |
|---|---|
| Decisive and specific to this category (metadata, tool files listing what was generated, AI-signed commits touching it, the author's own statement) | 95–99% |
| Several strong signals that agree, confirmed by reading/viewing | 80–95% |
| Several moderate signals, nothing contradicting | 60–80% |
| Mixed: signals both ways | 35–60% |
| Only weak signals | 15–35% |
| Nothing found, plenty reviewed, human process evidence | 2–10% |
| Nothing found but little to review (tiny code, no history, no raw files) | 10–25% |

   Extent within the AI mass: the share of the category's content that carries AI evidence after review (by
   code lines, number/visibility of images, minutes of music). ≥ ~80% of the content → most mass on GENERATED;
   a few files/assets → ASSISTED; advice-only traces → CONSULTED.
4. Don't use 0% or 100% – leave room for what you couldn't see. Numbers must sum to 100.
5. The most likely level is the label; its probability is the certainty. Verbal scale used in the report:
   ≥95 almost certain · ≥85 very likely · ≥65 likely · >50 more likely than not · ≥35 uncertain · ≥10 unlikely.

## Evidence lists
List **all** evidence that moved your numbers, both directions, each with:
- `text` – what you observed, in one line;
- `ref` – where: `path:line`, commit sha, image path, URL, "ldjam comment 2026-10-19";
- `weight` – decisive | strong | moderate | weak.
Include automated signals you confirmed and ones you rejected (as evidence_against with the reason, e.g. "emoji in
logs come from a tutorial copied before the event"). The report also appends every automated signal unfiltered.

## Per category

**Idea** – concept, mechanics, theme interpretation, story, names and in-game text.
- AI: author admits the idea/story came from an AI; prompt/spec files describing the concept; agent planning docs
  written before the code; description/README in LLM marketing style with LLM-favourite names. Description style
  alone is weak – it may be only the copy that is AI.
- Human: progress posts showing the idea evolving, brainstorming notes, idiosyncratic personal framing.

**Code** – own code only (no libraries, generated files, third-party plugins).
- Decisive: AI tool files, AI co-author/generator trailers, agent branches, the author's statement.
- Strong/moderate: elision markers, change-narrating comments, emoji in logs, near-total doc coverage, tutorial
  phrasing, velocity bursts, Godot 3/4 API mix-ups, the one-file web-game template, very large LoC.
- Human: incremental messy commits, debug leftovers, typos/slang/native-language comments, commented-out code,
  spaghetti, copy-paste, terse TODOs.
- Extent: share of code lines in AI-style files, of AI-signed commits, of lines added in bursts.

**Artwork** – sprites, backgrounds, UI, textures, models, animations.
- Decisive: generator metadata, AI-issuer C2PA, the author's statement.
- Strong/moderate (confirm by viewing): baked checkerboard; generator canvas sizes; fake pixel art; photo-like
  colour counts in tiny sprites; inconsistent style; malformed details; background-removal halos.
- Human: layered/animated raw files matching exports, editing time, time-lapses, consistent small palettes,
  progress GIFs, programmer art.

**Music & sound**
- Decisive: Suno/Udio/ElevenLabs/AIVA metadata or file names; the author's statement.
- Weak: polished songs without project files; lots of music for a solo 48h.
- Human: project files (.ceol/.bosca, .mmp, .flp, .als, .ptcop, trackers), BeepBox links.
- sfxr/jsfxr/ChipTone are **not AI**: keep them out of the level, but record them in `generators` with the
  probability that they did ≥90% of the audio work (drives the Audio opt-out).
- You can't listen: when audio verdicts rest on metadata only, say so and keep probabilities moderate.

**Documentation** – README, docs, design notes in the repo.
- AI: emoji headings, boiler-plate sections, project-structure tree, bold-label bullets, LLM vocabulary, unusual
  length, several long design docs.
- Human: short, casual, typos.

## Overall
Overall describes the game itself (Idea, Code, Artwork, Music & sound):
1. mass on `AI_GENERATED` when most of the game is generated (e.g. Code and Artwork/Music both likely
   GENERATED, or a vibe-coded game whose code and design came from an AI);
2. otherwise mostly `AI_ASSISTED` if any content category is likely ASSISTED/GENERATED;
3. otherwise `AI_CONSULTED` if any category (Documentation included) shows AI use;
4. otherwise `NO_AI`.
Its distribution should be consistent with the categories (P(any AI) for Overall ≥ the highest category's).

## How the numbers drive the opt-out audit (build_report.py)

| Category | P(should opt out) |
|---|---|
| Graphics | max(P(Artwork = AI_GENERATED), `generators.graphics.p_most`) |
| Audio | max(P(Music & sound = AI_GENERATED), `generators.audio.p_most`) |
| Innovation | P(Idea = AI_GENERATED) |
| Theme, Humor | ½ × P(Idea = AI_GENERATED) – optional categories |
| Mood | average of the Graphics and Audio generation probabilities – advisory |
| Fun | 0 (no category covers code) |
| Overall | cannot be opted out |

Assessment when not opted out: ≥85% missing (very likely) · ≥60% missing (likely) · ≥40% possibly missing ·
≥15% probably fine · else fine. Pre-made/third-party content (`premade_content`) is a separate Compo rule issue
that opting out cannot fix. Use `optout_overrides` (with `p_should` and a reason) when your judgment differs.

## verdicts.json
```json
{
  "categories": {
    "code": {
      "probabilities": {"NO_AI": 2, "AI_CONSULTED": 8, "AI_ASSISTED": 65, "AI_GENERATED": 25},
      "evidence_for": [
        {"text": "CLAUDE.md added during the event", "ref": "CLAUDE.md, commit c0dc485", "weight": "decisive"},
        {"text": "emoji in 6 log lines", "ref": "scripts/enemy.gd:11,31", "weight": "moderate"}],
      "evidence_against": [
        {"text": "player.gd is messy with debug leftovers", "ref": "scripts/player.gd:2,13", "weight": "moderate"}],
      "reasoning": "AI use certain; agent touched 2 of 9 scripts, so ASSISTED is most likely."
    },
    "music_sound": {"level": "N/A", "evidence_for": [{"text": "no audio files", "ref": "", "weight": ""}]},
    "overall": {"probabilities": {"NO_AI": 2, "AI_CONSULTED": 8, "AI_ASSISTED": 70, "AI_GENERATED": 20},
                "evidence_for": [], "evidence_against": [], "reasoning": "…"},
    "idea": {}, "artwork": {}, "documentation": {}
  },
  "generators":      {"graphics": {"tool": "", "p_most": 0, "evidence": []},
                      "audio":    {"tool": "jsfxr", "p_most": 70, "evidence": ["all 9 SFX have jsfxr names; no music"]}},
  "premade_content": {"graphics": {"p": 5, "evidence": []}, "audio": {"p": 5, "evidence": []}},
  "solo":            {"p_violation": 3, "evidence": []},
  "optout_overrides": {},
  "summary": "2–3 sentences a rater can act on, with the key probabilities.",
  "rule_notes": []
}
```
(The empty `{}` entries above stand for categories you fill the same way.) `solo` is optional – omit it to let the
report derive the probability from the page and git identities.

## Worked examples
- Godot game, 2.8k lines, 40 small messy commits, `.aseprite` for every sprite, one commit co-authored by Copilot
  adding a 60-line pathfinding helper → Code: NO_AI 2 / CONSULTED 13 / ASSISTED 80 / GENERATED 5; Artwork: NO_AI
  88 / CONSULTED 5 / ASSISTED 6 / GENERATED 1; Overall mostly ASSISTED.
- Single 1,900-line `index.html` (canvas primitives, oscillator sounds, emoji sprites), commits "Add game", "Fix
  bugs", README with emoji headings and a tree, created 2 h before the deadline → Code: NO_AI 10 / CONSULTED 5 /
  ASSISTED 25 / GENERATED 60 (pattern + velocity, no decisive artifact); Documentation GENERATED ~75; Overall
  GENERATED ~55.
- Hand-written code and art; the music file carries `made with suno` metadata and Audio is not opted out →
  Music & sound GENERATED ~90 → Audio P(should opt out) 90% → "MISSING OPT-OUT – very likely".
