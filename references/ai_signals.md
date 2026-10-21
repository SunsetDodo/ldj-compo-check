# AI-usage and human-process signals

How to read `stage3_signals.md`, what to check by hand, and the usual false positives.
Strength scale used by the scanner: 1 weak · 2 moderate · 3 strong · 4 decisive.

## Contents
1. Manual review checklist
2. Code signals
3. Artwork signals
4. Music & sound signals
5. Documentation, description and page signals
6. Process and feasibility
7. False positives to rule out

## 1. Manual review checklist
The scanner finds candidates; the verdict comes from looking. Budget roughly: 4–6 code files, 6–12 images,
the README, the description and the LD comments.

1. **AI tool artifacts** – open each listed file (CLAUDE.md, AGENTS.md, .cursor rules, .specstory history…). They
   often say what the agent was asked to build, which tells you the extent. `.specstory/` and `.aider.chat.history.md`
   contain the actual prompts.
2. **Commits** – for AI-signed commits and high-rate bursts, run
   `git -C <repo> show --stat <sha>` and skim the diff: what exactly did the agent write?
3. **Code** – read the files in the review queue: the most AI-looking ones and the most human-looking ones. Compare
   their styles. A codebase where one or two files look generated and the rest is messy and personal is
   AI_ASSISTED, not AI_GENERATED.
4. **Images** – view each queued image with Read. Look for: pixels not on a grid, mixed pixel sizes, smeared
   details, inconsistent light direction or outline thickness between sprites, garbled text, extra fingers,
   asymmetric "ornaments", watermarks or signatures in corners, a baked grey-white checkerboard, perspective that
   doesn't match the rest of the game.
5. **Raw files** – frames/layers counts and Krita editing time in `stage2.json` (`media`) are effort evidence.
   A single-layer, single-frame `.aseprite` holding a 1024px image with thousands of colours is an *imported*
   picture, not drawn work.
6. **Audio** – metadata only (you cannot listen). Say so in the evidence when it matters.
7. **Page** – read the description and every comment. An author statement about AI use is decisive for the
   category it names; "no AI was used" is a claim to weigh against the files, not proof.
8. **Links** – if there are time-lapse/stream links or progress GIFs, mention them; they are the strongest human
   evidence available.

## 2. Code signals
| Signal | Strength | Notes |
|---|---|---|
| AI tool files (CLAUDE.md, .claude/, AGENTS.md, .cursorrules/.cursor/, .windsurfrules, .github/copilot-instructions.md, GEMINI.md, .aider*, .clinerules, memory-bank/, .kiro/, .specstory/, replit.md, .bolt/, .opencode/…) | 4 | Proves the tool was used on this repo. Check the git history entry: added during the event or later? |
| .gitignore entries for AI tools | 3 | Tool used but its files kept out of git |
| Commit trailers: `Co-Authored-By: Claude`, `Generated with [Claude Code]`, Copilot/Cursor/Codex/Devin/Jules co-authors, `(aider)` author suffix, `Replit-Commit-Author: Agent` | 4 | Share of signed commits is a direct extent measure |
| Agent branches (`claude/…`, `codex/…`, `copilot/…`, `cursor/…`) | 3 | |
| Elision markers ("… existing code …", "rest of the code unchanged") | 3 | Pasted chat output |
| Change-narrating comments ("FIX: use X instead of Y", "now uses…") | 2–3 | Humans rarely annotate their own edits in a jam |
| Tutorial/hedging ("In a real game…", "you can adjust…", "Example usage") | 2–3 | |
| Emoji in log strings / comments (`print("✅ Game ready")`) | 2–3 | Strong habit of current LLMs |
| Doc comment on nearly every function (≥ 60–85%) | 2–3 | Under 48h pressure humans document little |
| Restating comments ("// Increment score" above `score += 1`) | 1 | Weak alone |
| Many unreferenced functions ("complete API": reset/serialize/getState never called) | 1 | Abandoned features look the same – read them |
| Godot 3 syntax in a Godot 4 project (yield, onready var, KinematicBody2D, instance()) or vice versa | 2 | Typical LLM confusion |
| One-file web game template (canvas primitives, oscillator sfx, emoji sprites, localStorage high score, menu/playing/gameOver states, "Press Start 2P") | 2–3 | Characteristic of one-shot LLM games |
| Commit messages: conventional commits / multi-bullet bodies throughout | 2 | Some humans write like this; weigh with the rest |
| Velocity bursts: > 600 new code lines per hour since the previous commit | 2–3 | Faster than typing new code; also happens when pasting a library or base code – check the diff |
| LoC feasibility: > 6k lines exceptional, > 10k implausible for hand-written code in 48h | 2–3 | Language matters (C#/Java verbose); generated data tables don't count |
| Runtime AI dependencies (openai, @anthropic-ai/sdk, …) | – | The game uses AI at runtime – report it; it is a design choice, not authoring evidence |

Human-side code signals: informal/frustrated comments (wtf, lol, "idk why this works"), typos and missing
apostrophes, comments in the author's own language, commented-out code, `print("here")` leftovers, long functions
and deep nesting, copy-paste blocks, terse TODO/HACK notes, inconsistent naming, magic numbers.

## 3. Artwork signals
| Signal | Strength | Notes |
|---|---|---|
| Generator metadata: PNG `parameters`/`prompt`/`workflow`, NovelAI/InvokeAI keys, "Steps: …, Sampler: …", IPTC `trainedAlgorithmicMedia` | 4 | Decisive for that file |
| C2PA / Content Credentials manifest | 3 | Usually written by OpenAI, Adobe Firefly, Google, Microsoft generators; editing apps can add it too – check the issuer if a tool is available |
| Baked fake-transparency checkerboard | 3 | |
| Exact generator canvas sizes (832×1216, 1344×768, 1536×1024, 1792×1024…) | 2 | 512/1024/2048 squares are common for hand-made textures too (weak) |
| Small sprite (≤128 px) with > 512 colours | 2 | Downscaled generated or photo art |
| Many near-identical shades / soft semi-transparent fringes | 1 | Painterly hand art also has these |
| Style inconsistency between assets | 2 | Needs your visual review |
| Missing raw files for detailed art | 1 | Many artists don't commit sources; never decisive |

Human-side: layered and animated sources matching the exports, Aseprite frame counts, Krita editing time,
Procreate time-lapse, consistent small palettes, simple programmer art, progress GIFs and time-lapses.

AI pixel-art tools to keep in mind: PixelLab, Retro Diffusion (runs inside Aseprite – metadata may be absent),
Scenario, Layer, plus general generators downscaled and palette-reduced. Visual review matters most here.

## 4. Music & sound signals
| Signal | Strength | Notes |
|---|---|---|
| Metadata naming Suno, Udio, AIVA, Mubert, Soundraw, Stable Audio, ElevenLabs | 4 | |
| File names from those services | 3 | |
| Finished, mastered songs (vocals especially) with no project file, much music for a solo 48h | 1 | Many composers export only |
| Project files (.ceol/.bosca, .mmp, .flp, .als, .ptcop, trackers), BeepBox links | human 3 | |
| sfxr/bfxr/jsfxr/ChipTone default file names (Pickup_Coin3.wav, laserShoot.wav…) | note | Allowed, not AI; but if generators made ~all the audio the FAQ says opt out of Audio |

## 5. Documentation, description and page signals
- README in LLM shape: emoji headings, Features/Getting Started/Project Structure (tree diagram)/Tech
  Stack/Future Improvements sections, "**Label**: text" bullets, LLM vocabulary (immersive, seamless, vibrant,
  delve, embark, testament…), em dashes, 700+ words. A README added after the deadline says nothing about the game.
- Several long design/architecture documents in a jam repo – typical of agent workflows (planning files).
- Description: the same style markers; LLM-favourite names (Elara, Kael, Lyra, Zephyr, "Echoes of…"). Marketing
  vocabulary is also common among humans on itch.io – weak alone.
- Disclosures: the scanner lists every AI mention in the description and comments with context. Skip mentions of
  game AI (enemy AI, pathfinding). Comments by other people are accusations, not evidence.

## 6. Process and feasibility
- Strong human evidence: time-lapse or stream recordings, progress posts with screenshots/GIFs during the event,
  many small commits with natural pauses (sleep gaps of 4+ h), raw files with history.
- Feasibility numbers (heuristics, not rules): experienced solo jammers usually write 1–4k code lines in 48h while
  also making art and audio; a typist cannot sustain > 600 lines/hour of new code. The art-hours estimate is very
  rough – use it only to notice extreme mismatches (e.g. 150 detailed illustrations plus 8k lines of code).
- Author history: earlier entries by the same author (game.json `author_history`) – a sudden jump in output or
  polish is context, not evidence.

## 7. False positives to rule out
- **Engine templates**: Godot ("Called when the node enters the scene tree for the first time", CharacterBody2D
  template comments "Add the gravity", "Handle jump"), Unity ("Start is called before the first frame update"),
  Unreal, Defold, GameMaker, MonoGame, raylib templates. The scanner ignores the known ones – watch for others.
- **Tutorial or base code** copied in: comments in tutorial voice may come from a course, not an AI. Check git:
  added in one commit before the event = base code.
- **Libraries** pasted into the repo (license headers, known library names) – exclude from LoC and style.
- **Formatters/linters** make code uniform; uniformity alone is not evidence.
- **Non-native English speakers** may use translators for READMEs; translated text ≠ AI-authored game.
- **Doc-comment generators** in IDEs (Rider, VS) create `/// <summary>` stubs – stubs are usually empty or minimal.
- **Bulk commits**: a single "initial commit" at the end makes velocity look superhuman; the scanner excludes
  commits creating 30+ files, but check large single-file commits manually.
- **Painterly or 3D-rendered art** naturally has many colours and soft edges.
