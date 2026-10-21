#!/usr/bin/env python3
"""Stage 3 (automated part) – collect evidence of AI use and of human process.

Reads game.json, stage1.json and stage2.json from the work directory and scans
the analysed snapshot. Produces <workdir>/stage3_signals.json and a readable
<workdir>/stage3_signals.md with, per area (code, artwork, music_sound,
documentation, idea, process):
  * signals  – {id, polarity ai|human, strength 1-4, title, detail, examples}
  * metrics  – the numbers behind them
  * lean     – an automatic, heuristic suggestion (NOT a verdict)
plus a review queue of code files, images and docs for the model to inspect
by itself, the feasibility numbers (LoC/velocity/art workload) and any AI
disclosures found on the Ludum Dare page.

Strength: 1 weak · 2 moderate · 3 strong · 4 decisive (direct proof of a tool).

Usage: scan_ai.py --workdir DIR [--max-images 400]
"""
from __future__ import annotations

import argparse
import collections
import math
import random
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import fmt_time, git, hours_between, human_int, load_json, log, parse_time, read_text, save_json  # noqa: E402
from filetypes import classify, count_lines, is_own  # noqa: E402

# ============================================================== AI tool artifacts
AI_FILES = [
    (r"(^|/)CLAUDE(\.local)?\.md$", "Claude Code memory file", 4),
    (r"(^|/)\.claude(/|$)", "Claude Code settings folder", 4),
    (r"(^|/)\.mcp\.json$", "MCP server config (AI agent tooling)", 3),
    (r"(^|/)AGENTS?\.md$", "AGENTS.md (Codex / coding-agent instructions)", 4),
    (r"(^|/)\.cursorrules$|(^|/)\.cursor(/|$)", "Cursor rules/config", 4),
    (r"(^|/)\.cursor(index(ing)?)?ignore$", "Cursor ignore file", 3),
    (r"(^|/)\.windsurfrules$|(^|/)\.windsurf(/|$)", "Windsurf rules", 4),
    (r"(^|/)\.codeiumignore$", "Codeium ignore file", 3),
    (r"(^|/)\.github/copilot-instructions\.md$", "GitHub Copilot instructions", 4),
    (r"(^|/)\.github/(instructions|prompts|chatmodes)/", "GitHub Copilot prompt/instruction files", 4),
    (r"\.prompt\.md$", "Copilot prompt file", 3),
    (r"(^|/)GEMINI\.md$|(^|/)\.gemini(/|$)", "Gemini CLI / Code Assist config", 4),
    (r"(^|/)\.aider[^/]*$", "Aider history/config", 4),
    (r"(^|/)CONVENTIONS\.md$", "CONVENTIONS.md (Aider convention file)", 2),
    (r"(^|/)\.clinerules|(^|/)\.cline(/|$)", "Cline rules", 4),
    (r"(^|/)\.roo(modes|rules)?(/|$)|(^|/)\.roorules", "Roo Code config", 4),
    (r"(^|/)memory-bank/", "Cline/Roo 'memory bank'", 4),
    (r"(^|/)\.kiro(/|$)", "Kiro specs/steering", 4),
    (r"(^|/)\.amazonq(/|$)", "Amazon Q rules", 4),
    (r"(^|/)\.continue(rules)?(/|$)", "Continue config", 4),
    (r"(^|/)\.junie(/|$)", "JetBrains Junie guidelines", 4),
    (r"(^|/)\.augment(-guidelines)?(/|$)", "Augment Code guidelines", 4),
    (r"(^|/)\.trae(/|$)", "Trae IDE rules", 4),
    (r"(^|/)\.specstory(/|$)", "SpecStory AI chat history (may contain the prompts!)", 4),
    (r"(^|/)replit\.md$", "Replit Agent project file", 4),
    (r"(^|/)\.bolt(/|$)", "bolt.new project config", 4),
    (r"(^|/)\.lovable", "Lovable config", 4),
    (r"(^|/)\.opencode(/|$)|(^|/)opencode\.json$", "opencode config", 4),
    (r"(^|/)\.goosehints$", "Goose hints", 4),
    (r"(^|/)\.openhands(/|$)", "OpenHands microagents", 4),
    (r"(^|/)\.codex(/|$)", "OpenAI Codex config", 4),
    (r"(^|/)\.agents?(/|$)", "agent config folder", 3),
    (r"(^|/)\.rules$", "Zed AI rules file", 3),
    (r"(^|/)llms(-full)?\.txt$", "llms.txt", 1),
    (r"(^|/)\.github/workflows/[^/]*(claude|copilot|codex|gemini|cursor|aider)[^/]*\.ya?ml$", "AI agent CI workflow", 3),
    (r"(^|/)(prompts?|PROMPTS?)(/[^/]+)?\.(md|txt)$", "prompt file(s)", 2),
    (r"(^|/)(IMPLEMENTATION_PLAN|TASKS|SPEC|PRD|CHANGES_SUMMARY|SUMMARY|PLAN)\.md$", "agent-style planning document", 1),
]
AI_IGNORE_RE = re.compile(r"^\s*/?(\.claude|CLAUDE\.local\.md|CLAUDE\.md|\.cursor|\.aider|\.specstory|\.windsurf|"
                          r"\.codex|\.gemini|AGENTS\.md|\.roo|\.kiro|\.continue|\.cline|memory-bank)\b", re.M)
RUNTIME_AI_RE = re.compile(r"""["'](openai|@anthropic-ai/sdk|anthropic|@google/generative-ai|@google/genai|"""
                           r"""google-generativeai|langchain|@langchain/[\w-]+|ollama|transformers|@huggingface/[\w-]+|"""
                           r"""replicate|elevenlabs|@mlc-ai/web-llm|onnxruntime-web|llama-cpp|groq-sdk|mistralai|"""
                           r"""cohere-ai|together-ai)["']""", re.I)
AI_BRANCH_RE = re.compile(r"^(origin/)?(claude|cursor|copilot|codex|devin|jules|aider|openhands|sweep|bolt|"
                          r"lovable|replit|gemini)[/-]", re.I)

# ============================================================== comment heuristics
AI_COMMENT_PATTERNS = [
    ("elision", 3, "elision markers left by AI edits ('… existing code …')",
     r"\.\.\.\s*(existing|rest of|previous|other|remaining|same as before|unchanged)\b|"
     r"\brest of (the )?(code|file|implementation|class|function|methods?)\b.{0,30}\b(unchanged|remains|stays|same|here|omitted)\b|"
     r"\b(existing|other) (code|methods|logic) (remains|stays|unchanged)\b"),
    ("change_narration", 2, "comments narrating a change ('FIX:', 'now uses X instead of Y')",
     r"^(fix(ed)?|bug ?fix|changed?|updated?|new|added|removed|modified|improved|refactored|replaced|moved)\s*[:\-–—]\s|"
     r"\b(changed|switched|moved|replaced|updated|reverted) (from|to)\b.{0,60}\b(to|instead|so that)\b|"
     r"\b(now|instead) (uses?|we|it|this)\b.{0,50}\b(instead of|rather than)\b|\bpreviously\b.{0,50}\bnow\b"),
    ("tutorial_hedge", 2, "tutorial/hedging phrases ('In a real game…', 'you can adjust…')",
     r"\bin a (real|full|production|complete|proper|larger|more complex) (game|app|application|implementation|project|scenario|setting)\b|"
     r"\bfor (simplicity|demonstration|demo purposes|this example)\b|\b(simplified|basic) (version|implementation|approach)\b|"
     r"\byou (can|could|may|might|would) (want to )?(adjust|tweak|modify|change|customi[sz]e|replace|extend|add)\b|"
     r"\bfeel free to\b|\bdepending on your (needs|game|use case)\b"),
    ("explainer", 1, "explanatory boilerplate ('This function…', 'This ensures…', 'Helper function to…')",
     r"^(this|the following) (function|method|class|script|code|block|component|module) (is|will|handles?|ensures?|creates?|manages?|returns?|checks?|updates?)\b|"
     r"\b(this|which) (ensures|allows|prevents|makes sure|guarantees)\b|\bmake sure (to|that|we)\b|\bensure (that|the|we)\b|"
     r"^helper (function|method) (to|for|that)\b|\bhandles? (the )?(edge case|case where|cases where)\b"),
    ("step_numbering", 2, "numbered step comments ('Step 1:', '1. Initialize…')",
     r"^(step\s*\d+\s*[:.)-]|\d\s*[.)]\s+[A-Z][a-z]+ (the|a|all|our|player|game)\b)"),
    ("emphasis_label", 1, "emphasis labels ('IMPORTANT:', 'CRITICAL:')", r"^(important|critical|key point|optional)\s*:"),
    ("example_usage", 2, "'Example usage' blocks", r"\b(example usage|usage example)\b"),
    ("placeholder", 1, "placeholder language", r"\b(placeholder|stub implementation|dummy implementation|replace (this )?with (actual|real|your))\b"),
]
AI_COMMENT_RES = [(k, w, d, re.compile(p, re.I)) for k, w, d, p in AI_COMMENT_PATTERNS]
TEMPLATE_COMMENTS = {
    "called when the node enters the scene tree for the first time", "replace with function body",
    "called every frame. 'delta' is the elapsed time since the previous frame",
    "declare member variables here. examples", "var a = 2", 'var b = "text"',
    "get the input direction and handle the movement/deceleration",
    "as good practice, you should replace ui actions with custom gameplay actions", "add the gravity",
    "handle jump", "get the gravity from the project settings to be synced with rigidbody nodes",
    "start is called before the first frame update", "update is called once per frame",
    "start is called once before the first execution of update after the monobehaviour is created",
    "sets default values", "called when the game starts or when spawned", "called every frame",
    "called to bind functionality to input", "add initialization code here", "remove this function if not needed",
    "learn more: https://defold.com/manuals/script/", "add update code here", "add message-handling code here",
    "add input-handling code here", "add reload-handling code here", "add finalization code here",
    "@description insert description here", "you can write your code in this editor",
    "todo: add your initialization logic here", "todo: use this.content to load your game content here",
    "todo: add your update logic here", "todo: add your drawing code here", "initialization", "main game loop",
    "de-initialization", "detect window close button or esc key", "set our game to run at 60 frames-per-second",
    "close window and opengl context", "todo: update your variables here", "todo: draw your scene here",
    "called when the node is about to leave the scene tree", "called every physics frame",
}
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\u2600-\u27BF\u2B50\u2B55"
                      "\u2705\u274C\u274E\u2728\U0001F004\U0001F0CF]")
TYPO_GRAPHY_RE = re.compile(r"[—–‘’“”…→←↔⇒✓✔✗✘•═─│┌┐└┘├┤╔╗╚╝]")
HUMAN_SLANG_RE = re.compile(
    r"\b(wtf|fuck\w*|shit\w*|damn\w*|crap\w*|ugh+|lol+|lmao|idk|dunno|kinda|gonna|wanna|gotta|pls|plz|hacky|"
    r"janky|jank|yolo|meh|oops|whoops|derp|yeet|bruh|omg|halp|aaa+|hmm+|ffs|bleh|meow)\b|\bno idea\b|"
    r"\bdon'?t ask\b|\bi hate\b|\bwhy (does|is|do) (this|it)\b|\btemp(orary)? (fix|hack)\b|\bquick (fix|hack)\b|"
    r"\bdirty (fix|hack)\b|\bdon'?t touch\b|\bfix (this|later|me)\b|\bsorry\b|\bugly\b|\bmagic number\b|"
    r"\bit works\b|\bwhatever\b|\bfor some reason\b", re.I)
TYPO_RE = re.compile(
    r"\b(recieve\w*|seperat\w*|lenght|heigth|widht|wierd|colision\w*|collison\w*|collsion\w*|enviroment|"
    r"definately|occured|untill|wich|teh|dammage|positon|postion|spwan\w*|spanw|veloctiy|velocty|destory|"
    r"defualt|retrun|funtion|fucntion|plaer|palyer|enemey|enmy|ennem\w+|bullett|ammount|buton|healht|heatlh|"
    r"helath|scoer|socre|rotaion|roation|accelaration|acceleartion|gravtiy|sprtie|spirte|inital\w*|intial\w*|"
    r"controler|succesful\w*|sucess\w*|begining|neccessary|necesary|existant|independant|comming|runing|"
    r"stoped|droped|becuase|beacuse|becasue|thier|whith|wiht|taht|jsut|dont|doesnt|cant|wont|isnt|"
    r"didnt|thats|whats|im|ive)\b")
NON_EN_WORDS_RE = re.compile(r"\b(para|esto|aqui|aquí|cuando|porque|fazer|isso|então|nicht|und|wenn|pour|avec|"
                             r"quand|parce|questo|quando|perché|jogador|jugador|joueur|spieler|giocatore|vida|"
                             r"pulo|salto|inimigo|enemigo|ennemi|gegner|nemico|tiempo|tempo|zeit|temps)\b", re.I)
DEBUG_PRINT_RE = re.compile(
    r"\b(print|printf|println|prints|print_debug|console\.log|Debug\.Log|debug\.log|trace|GD\.Print|printh|"
    r"love\.graphics\.print|show_debug_message)\s*\(\s*(['\"])(here|here\d*|test\w*|hi|hello|asdf\w*|aaa+|lol|"
    r"wtf|works|working|ok|yay|yes|no|\?+|!+|\.+|\d|[a-z]|debug|foo|bar|hmm+|why|pls|ping|pong|hit|boom)\2\s*\)", re.I)
CODEY_RE = re.compile(
    r"^(if|for|while|var|let|const|func|def|return|local|print|self\.|this\.|else|elif|end|import|from|function|"
    r"class|public|private|void|int|float|bool|await|yield|emit|get_node|\$|@onready|@export|console\.|"
    r"Debug\.|transform\.|GetComponent|Instantiate|Destroy|velocity|position)\b", re.I)

FUNC_DEFS = {
    "GDScript": [r"^\s*(?:static\s+)?func\s+([A-Za-z_]\w*)\s*\("],
    "Python": [r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\("],
    "Lua": [r"^\s*(?:local\s+)?function\s+([\w.:]+)\s*\(", r"^\s*(?:local\s+)?([\w.]+)\s*=\s*function\s*\("],
    "JavaScript": [r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(",
                   r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)",
                   r"^\s*(?:(?:public|private|protected|static|async|override|readonly|get|set)\s+)*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::\s*[^={]+)?\{\s*$"],
    "C#": [r"^\s*(?:\[[^\]]*\]\s*)*(?:(?:public|private|protected|internal|static|virtual|override|abstract|async|sealed|extern|unsafe|new|partial)\s+)+[\w<>\[\],.?]+\s+([A-Za-z_]\w*)\s*\(",
           r"^\s*(?:void|int|float|bool|string|IEnumerator|Vector[23]|Task|double|long)\s+([A-Za-z_]\w*)\s*\("],
    "Java": [r"^\s*(?:(?:public|private|protected|static|final|synchronized|abstract)\s+)+[\w<>\[\],.?]+\s+([A-Za-z_]\w*)\s*\("],
    "Kotlin": [r"^\s*(?:[\w@]+\s+)*fun\s+(?:<[^>]+>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)\s*\("],
    "C++": [r"^\s*(?:static\s+|inline\s+|virtual\s+|constexpr\s+)*[\w:<>,*&]+(?:\s+[\w:<>,*&]+)*\s+[*&]*([A-Za-z_~]\w*(?:::[A-Za-z_~]\w*)?)\s*\([^;]*\)\s*(?:const\s*)?(?:override\s*)?\{?\s*$"],
    "Go": [r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("],
    "Rust": [r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)"],
    "Haxe": [r"^\s*(?:(?:public|private|static|override|inline|macro)\s+)*function\s+([A-Za-z_]\w*)"],
    "GML": [r"^\s*function\s+([A-Za-z_]\w*)\s*\("],
    "Swift": [r"^\s*(?:(?:public|private|internal|fileprivate|static|override|mutating|@\w+)\s+)*func\s+([A-Za-z_]\w*)"],
    "Ruby": [r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[!?]?)"],
    "Zig": [r"^\s*(?:pub\s+)?fn\s+([A-Za-z_]\w*)"],
    "Odin": [r"^\s*([A-Za-z_]\w*)\s*::\s*proc"],
}
FUNC_DEFS["TypeScript"] = FUNC_DEFS["JavaScript"]
FUNC_DEFS["PICO-8"] = FUNC_DEFS["Lua"]
FUNC_DEFS["C"] = FUNC_DEFS["C/C++"] = FUNC_DEFS["C++"]
FUNC_DEFS["Dart"] = FUNC_DEFS["Java"]
FUNC_RES = {k: [re.compile(p) for p in v] for k, v in FUNC_DEFS.items()}
CONTROL_KW = {"if", "for", "while", "switch", "catch", "return", "sizeof", "else", "do", "new", "typeof",
              "function", "await", "yield", "foreach", "using", "lock", "fixed", "when", "match", "case", "elif",
              "constructor"}
CALLBACKS = {
    "_ready", "_process", "_physics_process", "_input", "_unhandled_input", "_unhandled_key_input", "_init",
    "_enter_tree", "_exit_tree", "_draw", "_notification", "_gui_input", "_integrate_forces", "_to_string",
    "_get", "_set", "_get_property_list", "_get_configuration_warnings", "_can_drop_data", "_drop_data",
    "_get_drag_data", "_on_", "Awake", "Start", "Update", "FixedUpdate", "LateUpdate", "OnEnable",
    "OnDisable", "OnDestroy", "OnCollisionEnter", "OnCollisionEnter2D", "OnCollisionExit", "OnCollisionExit2D",
    "OnCollisionStay", "OnCollisionStay2D", "OnTriggerEnter", "OnTriggerEnter2D", "OnTriggerExit",
    "OnTriggerExit2D", "OnTriggerStay", "OnTriggerStay2D", "OnMouseDown", "OnMouseUp", "OnMouseEnter",
    "OnMouseExit", "OnMouseOver", "OnMouseDrag", "OnGUI", "OnValidate", "Reset", "OnDrawGizmos",
    "OnDrawGizmosSelected", "OnApplicationQuit", "OnApplicationPause", "OnApplicationFocus", "OnBecameVisible",
    "OnBecameInvisible", "OnAnimatorMove", "OnRenderImage", "OnControllerColliderHit", "OnParticleCollision",
    "Main", "main", "preload", "create", "update", "init", "draw", "setup", "keyPressed", "keyReleased",
    "mousePressed", "mouseReleased", "mouseClicked", "mouseMoved", "mouseDragged", "windowResized",
    "touchStarted", "touchEnded", "touchMoved", "load", "keypressed", "keyreleased", "mousepressed",
    "mousereleased", "mousemoved", "wheelmoved", "textinput", "resize", "focus", "quit", "conf", "final",
    "fixed_update", "on_message", "on_input", "on_reload", "_update", "_update60", "toString", "render",
    "connectedCallback", "disconnectedCallback", "run", "start", "tick", "step", "loop", "gameLoop", "Draw",
    "Initialize", "LoadContent", "UnloadContent", "Configure", "new", "default", "fmt", "drop", "build",
}
GODOT3_IN_4_RE = re.compile(r"(\byield\s*\(|^\s*onready\s+var\b|^\s*export\s*(\(|var\b)|\bKinematicBody(2D)?\b|"
                            r"\.instance\(\)|\bchange_scene\(\s*\"|\bsetget\b|\bremotesync\b|\bPool\w+Array\b|"
                            r"\brand_range\(|\bdeg2rad\(|\brad2deg\(|\bstepify\(|\bVisualServer\b|"
                            r"\bconnect\(\s*\"\w+\"\s*,\s*self\s*,)", re.M)
GODOT4_IN_3_RE = re.compile(r"(^\s*@(export|onready|tool)\b|\bCharacterBody(2D|3D)\b|\bawait\s|\bcreate_tween\(|"
                            r"\.instantiate\(\)|\bchange_scene_to_file\(|\bdeg_to_rad\()", re.M)

LLM_NAMES_RE = re.compile(r"\b(Elara|Lyra|Kael|Zephyr|Seraphina|Thorne|Aetheria|Eldoria|Luminara|Aethelgard|"
                          r"Vespera|Nyx|Lumina|Aurelia|Elysia|Valerius|Caelum|Orion Vex|Kaelen|Lirael|Sylas|"
                          r"Echoes of|Whispers of|Shadows of|Chronicles of|Realm of|Aether|Nexus)\b")
AI_PROSE_RE = re.compile(
    r"\b(delve|delves|tapestry|immersive|seamless(ly)?|vibrant|captivating|intricate|elevate[sd]?|unleash|embark|"
    r"testament|realm|symphony|adventure awaits|unique blend|leverag\w+|robust|comprehensive|intuitive|"
    r"showcas\w+|meticulous(ly)?|effortless(ly)?|exhilarating|mesmeriz\w+|whimsical|ever-changing|"
    r"dive into|step into|get ready to|whether you'?re|not just (a|an)\b|a journey|navigate the|master the art|"
    r"test your (skills|reflexes)|fast-paced|key features|core mechanics|gameplay loop)\b", re.I)
README_SECTIONS_RE = re.compile(
    r"^#{1,4}\s*(?:[^\w\s]{1,3}\s*)?(features|key features|controls|how to play|gameplay|getting started|"
    r"installation|prerequisites|running( the game)?|build(ing)?( instructions)?|project structure|file structure|"
    r"architecture|technologies( used)?|tech stack|built with|technical details|game mechanics|scoring|tips|"
    r"future (improvements|enhancements|ideas)|roadmap|known issues|contributing|license|credits|"
    r"acknowledg(e)?ments|theme interpretation|development|design( notes)?|overview|about)\b", re.I | re.M)
DISCLOSURE_RE = re.compile(
    r"\b(chat ?gpt|gpt-?[345o]\w*|openai|claude|anthropic|copilot|cursor ai|cursor ide|windsurf|codeium|gemini|"
    r"bard|grok|deepseek|llama|mistral|qwen|midjourney|stable ?diffusion|sdxl|dall[- ·]?e|firefly|leonardo\.?ai|"
    r"ideogram|pixellab|retro ?diffusion|scenario\.gg|suno|udio|elevenlabs|eleven ?labs|aiva|mubert|soundraw|"
    r"stable ?audio|riffusion|text[- ]to[- ]speech|tts|vibe[- ]?cod\w*|llms?|generative|gen[- ]?ai|"
    r"ai[- ](generated|assisted|art|music|voice|tools?|model|image|images|code|helped)|"
    r"(generated|made|created|written|drawn|composed|helped) (with|by|using) (an? )?ai\b|"
    r"(used|using|use) (an? )?ai\b|\bno ai\b|without (any )?ai\b|\bai[- ]free\b)", re.I)
GENERATOR_SFX_RE = re.compile(r"(^|/)(pickup_?coin|laser_?shoot|hit_?hurt|blip_?select|power_?up|explosion|jump|"
                              r"synth|random|tone|click|sfxr|bfxr|jsfxr|chiptone|rfxgen)[_ -]?\d*\.(wav|ogg|mp3)$", re.I)
AI_AUDIO_NAME_RE = re.compile(r"(?<![a-z])(elevenlabs|suno|udio|aiva|mubert|soundraw|stable[_ -]?audio)(?![a-z])", re.I)
AI_SIZES = {(512, 512), (768, 768), (1024, 1024), (1024, 1536), (1536, 1024), (1792, 1024), (1024, 1792),
            (832, 1216), (1216, 832), (896, 1152), (1152, 896), (1344, 768), (768, 1344), (1536, 640),
            (640, 1536), (2048, 2048), (1328, 1328), (1664, 928), (928, 1664), (1248, 832), (832, 1248),
            (1184, 864), (864, 1184), (1392, 752), (752, 1392), (1024, 576), (576, 1024)}
POT_SQUARES = {(512, 512), (1024, 1024), (2048, 2048)}


def sig(area, sid, polarity, strength, title, detail="", examples=None):
    ex = list(examples or [])
    return {"area": area, "id": sid, "polarity": polarity, "strength": strength, "title": title,
            "detail": detail, "examples": ex[:25], "examples_total": len(ex)}


def norm_comment(s):
    return re.sub(r"\s+", " ", s.strip().strip("/#-*;! ").rstrip(".").lower())


# ============================================================== code
def analyze_code_file(text, lang, style, path):
    m = {"lines": 0, "code": 0, "comment": 0, "comments": 0, "defs": 0, "documented": 0, "restating": 0,
         "commented_code": 0, "todo_terse": 0, "todo_verbose": 0, "emoji": 0, "emoji_logs": 0,
         "typography": 0, "slang": 0, "typos": 0, "non_english": 0, "debug_prints": 0, "template": 0,
         "max_func_len": 0, "long_funcs": 0, "max_depth": 0, "ai_phrases": collections.Counter(),
         "examples": collections.defaultdict(list), "def_names": []}
    lc = count_lines(text, style, keep_comments=True, pico8=(lang == "PICO-8"))
    m["lines"], m["code"], m["comment"] = lc["lines"], lc["code"], lc["comment"]
    lines = text.splitlines()
    if lang == "PICO-8":
        mm = re.search(r"__lua__\s*\n(.*?)(\n__\w+__|\Z)", text, re.S)
        lines = (mm.group(1) if mm else "").splitlines()
    full_comment_lines = {i for i, _, full in lc["comments"] if full}
    for i, txt, full in lc["comments"]:
        n = norm_comment(txt)
        if not n:
            continue
        m["comments"] += 1
        if n in TEMPLATE_COMMENTS or any(n.startswith(t) for t in TEMPLATE_COMMENTS if len(t) > 25):
            m["template"] += 1
            continue
        for key, _w, _d, rx in AI_COMMENT_RES:
            if rx.search(txt.strip()):
                m["ai_phrases"][key] += 1
                if len(m["examples"][key]) < 6:
                    m["examples"][key].append(f"{path}:{i}: {txt.strip()[:110]}")
        e = len(EMOJI_RE.findall(txt))
        if e:
            m["emoji"] += e
            if len(m["examples"]["emoji"]) < 3:
                m["examples"]["emoji"].append(f"{path}:{i}: {txt.strip()[:90]}")
        m["typography"] += len(TYPO_GRAPHY_RE.findall(txt))
        if HUMAN_SLANG_RE.search(txt):
            m["slang"] += 1
            if len(m["examples"]["slang"]) < 3:
                m["examples"]["slang"].append(f"{path}:{i}: {txt.strip()[:90]}")
        t = TYPO_RE.findall(txt)
        if t:
            m["typos"] += len(t)
            if len(m["examples"]["typos"]) < 3:
                m["examples"]["typos"].append(f"{path}:{i}: {txt.strip()[:90]}")
        if re.search(r"[À-ɏЀ-ӿ֐-׿؀-ۿ一-鿿ぁ-ヿ가-힯Ͱ-Ͽ]", txt) or NON_EN_WORDS_RE.search(txt):
            m["non_english"] += 1
            if len(m["examples"]["non_english"]) < 3:
                m["examples"]["non_english"].append(f"{path}:{i}: {txt.strip()[:90]}")
        if re.search(r"\b(TODO|FIXME|XXX|HACK)\b", txt):
            words = len(re.sub(r".*?\b(TODO|FIXME|XXX|HACK)\b[:\s]*", "", txt).split())
            if words <= 4:
                m["todo_terse"] += 1
            elif words >= 8 and re.search(r"[.!]$", txt.strip()):
                m["todo_verbose"] += 1
        if full and looks_like_code(txt):
            m["commented_code"] += 1
            if len(m["examples"]["commented_code"]) < 2:
                m["examples"]["commented_code"].append(f"{path}:{i}: {txt.strip()[:90]}")
    # restating comments: full-line comment immediately followed by code that says the same thing
    for i, txt, full in lc["comments"]:
        if not full or i >= len(lines):
            continue
        nxt = lines[i].strip() if i < len(lines) else ""
        cw = [w for w in re.findall(r"[a-z]{3,}", txt.lower()) if w not in STOP]
        if not nxt or not cw or len(cw) > 8:
            continue
        ids = set()
        for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", nxt):
            for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", tok.replace("_", " ")):
                ids.add(part.lower())
        overlap = sum(1 for w in cw if w in ids or w.rstrip("s") in ids)
        if overlap >= 2 and overlap / len(cw) >= 0.5:
            m["restating"] += 1
            if len(m["examples"]["restating"]) < 2:
                m["examples"]["restating"].append(f"{path}:{i}: // {txt.strip()[:60]}  →  {nxt[:60]}")
    # emoji / debug prints in code (strings)
    for i, ln in enumerate(lines, 1):
        if i in full_comment_lines:
            continue
        if EMOJI_RE.search(ln) and re.search(r"(print|log|Log|trace|debug|console)\w*\s*\(", ln):
            m["emoji_logs"] += 1
            if len(m["examples"]["emoji_logs"]) < 3:
                m["examples"]["emoji_logs"].append(f"{path}:{i}: {ln.strip()[:100]}")
        if DEBUG_PRINT_RE.search(ln):
            m["debug_prints"] += 1
            if len(m["examples"]["debug_prints"]) < 2:
                m["examples"]["debug_prints"].append(f"{path}:{i}: {ln.strip()[:80]}")
    # function definitions, documentation coverage, lengths
    rxs = FUNC_RES.get(lang, [])
    defs = []
    for i, ln in enumerate(lines, 1):
        for rx in rxs:
            mm = rx.match(ln)
            if mm:
                name = mm.group(1).split(".")[-1].split(":")[-1]
                if name and name not in CONTROL_KW:
                    defs.append((i, name))
                break
    m["defs"] = len(defs)
    m["def_names"] = [n for _, n in defs]
    for idx, (i, _name) in enumerate(defs):
        j = i - 2
        while j >= 0 and not lines[j].strip():
            j -= 1
        prev_is_comment = (j + 1) in full_comment_lines or (j >= 0 and re.match(r"\s*(///|/\*\*|\*|##|---|#|//|--|\[)", lines[j] or "")
                                                             and not lines[j].strip().startswith("[") )
        doc_after = False
        if lang in ("Python",):
            k = i
            while k < len(lines) and not lines[k].strip():
                k += 1
            doc_after = k < len(lines) and lines[k].strip().startswith(('"""', "'''"))
        if prev_is_comment or doc_after:
            m["documented"] += 1
        end = defs[idx + 1][0] if idx + 1 < len(defs) else len(lines) + 1
        flen = end - i
        m["max_func_len"] = max(m["max_func_len"], flen)
        if flen > 120:
            m["long_funcs"] += 1
    # nesting depth
    if style in ("c", "css") or lang in ("JavaScript", "TypeScript", "C#", "Java", "C++", "Rust", "Go", "Haxe", "GML"):
        depth = 0
        for ln in lines:
            s = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//.*$', "", ln)
            depth += s.count("{") - s.count("}")
            m["max_depth"] = max(m["max_depth"], depth)
    elif lang in ("GDScript", "Python"):
        for ln in lines:
            if ln.strip() and not ln.strip().startswith("#"):
                ind = len(ln) - len(ln.lstrip("\t"))
                if ind == 0:
                    sp = len(ln) - len(ln.lstrip(" "))
                    ind = sp // 4
                m["max_depth"] = max(m["max_depth"], ind)
    return m


STOP = {"the", "and", "for", "this", "that", "with", "from", "into", "when", "then", "our", "are", "its", "it's",
        "not", "but", "all", "has", "have", "was", "will", "can", "get", "set", "new", "now", "use", "using"}


def looks_like_code(s):
    s = s.strip()
    if len(s) < 4:
        return False
    if CODEY_RE.match(s):
        return True
    if re.search(r"[;{}]\s*$", s):
        return True
    if re.match(r"^[\w.\[\]$]+\s*(=|\+=|-=|\*=|/=|:=)\s*\S", s) and not re.search(r"\b(the|is|are|this|that|we|to|of)\b", s.lower()):
        return True
    if re.match(r"^[\w.$:]+\(.*\)\s*$", s):
        return True
    return False


def code_signals(root, records, stage2, timeline):
    own = [r for r in records if r["own"] and r["category"] == "code" and r.get("loc") and not r["ext"] == ".p8.png"]
    eng = " ".join(stage2.get("engines", []))
    agg = collections.Counter()
    phrases = collections.Counter()
    examples = collections.defaultdict(list)
    per_file = []
    corpus_tokens = collections.Counter()
    all_defs = collections.Counter()
    def_where = {}
    gd_mix = []
    windows = collections.Counter()
    for r in own:
        p = root / r["path"]
        text = read_text(p, 3_000_000)
        info = classify(r["path"])
        m = analyze_code_file(text, info.get("lang") or r.get("lang"), info.get("style"), r["path"])
        for k in ("lines", "code", "comment", "comments", "defs", "documented", "restating", "commented_code",
                  "todo_terse", "todo_verbose", "emoji", "emoji_logs", "typography", "slang", "typos",
                  "non_english", "debug_prints", "template", "long_funcs"):
            agg[k] += m[k]
        agg["max_func_len"] = max(agg["max_func_len"], m["max_func_len"])
        agg["max_depth"] = max(agg["max_depth"], m["max_depth"])
        phrases.update(m["ai_phrases"])
        for k, v in m["examples"].items():
            examples[k].extend(v)
        for n in m["def_names"]:
            all_defs[n] += 1
            def_where.setdefault(n, r["path"])
        corpus_tokens.update(re.findall(r"[A-Za-z_]\w*", text))
        ai_pts = sum(w * m["ai_phrases"][k] for k, w, _d, _ in AI_COMMENT_RES) + 2 * m["emoji"] + 3 * m["emoji_logs"] \
            + m["typography"] * 0.3 + m["restating"]
        hu_pts = 2 * m["slang"] + 2 * m["typos"] + 2 * m["non_english"] + m["commented_code"] + 2 * m["debug_prints"] + m["todo_terse"]
        doc_cov = m["documented"] / m["defs"] if m["defs"] else 0
        per_file.append({"path": r["path"], "code": m["code"], "comment_ratio": round(m["comment"] / max(1, m["code"]), 2),
                         "doc_coverage": round(doc_cov, 2), "ai_points": round(ai_pts, 1), "human_points": hu_pts,
                         "created_phase": r.get("created_phase")})
        if info.get("lang") == "GDScript":
            if eng.startswith("Godot 4") and GODOT3_IN_4_RE.search(text):
                gd_mix.append(f"{r['path']}: {GODOT3_IN_4_RE.search(text).group(0).strip()[:40]}")
            elif "Godot 3" in eng and GODOT4_IN_3_RE.search(text):
                gd_mix.append(f"{r['path']}: {GODOT4_IN_3_RE.search(text).group(0).strip()[:40]}")
        norm = [re.sub(r"\s+", " ", l.strip()) for l in text.splitlines()]
        norm = [l for l in norm if len(l) > 3 and l not in ("{", "}", "};", "end", "else", "pass", "})", ");")
                and not l.startswith(("//", "#", "--", "*", "/*"))]
        for i in range(0, max(0, len(norm) - 5)):
            windows[hash(tuple(norm[i:i + 6]))] += 1
    # corpus for references also includes scenes / markup (signal connections, onclick=...)
    for r in records:
        if r["category"] in ("engine", "markup", "level", "data") and r["size"] < 2_000_000 and r["own"]:
            corpus_tokens.update(re.findall(r"[A-Za-z_]\w*", read_text(root / r["path"], 2_000_000)))
    unused = [n for n, c in all_defs.items() if n not in CALLBACKS and not n.startswith(("_on_", "On", "on_"))
              and not (n.startswith("__") and n.endswith("__")) and corpus_tokens[n] <= c]
    dup = sum(c for c in windows.values() if c > 1)
    total_w = sum(windows.values()) or 1
    M = {"files": len(own), "code_lines": agg["code"], "comment_lines": agg["comment"],
         "comment_ratio": round(agg["comment"] / max(1, agg["code"]), 3),
         "functions": agg["defs"], "doc_coverage": round(agg["documented"] / max(1, agg["defs"]), 3),
         "ai_phrase_hits": dict(phrases), "ai_phrase_per_100_comments": round(100 * sum(phrases.values()) / max(1, agg["comments"] - agg["template"]), 1),
         "template_comments": agg["template"], "restating_comments": agg["restating"],
         "emoji_in_comments": agg["emoji"], "emoji_in_log_strings": agg["emoji_logs"],
         "typographic_chars_in_comments": agg["typography"], "slang_comments": agg["slang"], "typos": agg["typos"],
         "non_english_comments": agg["non_english"], "commented_out_code_lines": agg["commented_code"],
         "debug_prints": agg["debug_prints"], "todo_terse": agg["todo_terse"], "todo_verbose": agg["todo_verbose"],
         "unused_functions": len(unused), "unused_function_ratio": round(len(unused) / max(1, len(all_defs)), 3),
         "unused_examples": [f"{def_where[n]}: {n}" for n in unused[:8]],
         "max_function_length": agg["max_func_len"], "functions_over_120_lines": agg["long_funcs"],
         "max_nesting_depth": agg["max_depth"], "duplicate_6line_block_ratio": round(dup / total_w, 3),
         "godot_version_mixups": gd_mix[:6]}
    S = []
    A = "code"
    ph = sum(phrases.values())
    if phrases.get("elision"):
        S.append(sig(A, "ELISION_MARKERS", "ai", 3, "AI-edit elision markers in comments",
                     f"{phrases['elision']} comment(s) like '… existing code …' – typical of pasted LLM output.", examples["elision"]))
    if phrases.get("change_narration", 0) >= 2:
        S.append(sig(A, "CHANGE_NARRATION", "ai", 2 if phrases["change_narration"] < 6 else 3,
                     "Comments narrate changes (FIX:/now uses X instead of Y)",
                     f"{phrases['change_narration']} hit(s); humans rarely annotate their own edits this way in a jam.",
                     examples["change_narration"]))
    tut = phrases.get("tutorial_hedge", 0) + phrases.get("example_usage", 0)
    if tut:
        S.append(sig(A, "TUTORIAL_PHRASES", "ai", 2 if tut < 4 else 3, "Tutorial/hedging phrases in comments",
                     f"{tut} hit(s) (e.g. 'In a real game…', 'you can adjust…').",
                     examples["tutorial_hedge"] + examples["example_usage"]))
    if phrases.get("step_numbering", 0) >= 3:
        S.append(sig(A, "STEP_COMMENTS", "ai", 2, "Numbered step comments", f"{phrases['step_numbering']} hit(s).",
                     examples["step_numbering"]))
    if M["ai_phrase_per_100_comments"] >= 8 and ph >= 5:
        S.append(sig(A, "AI_PHRASE_DENSITY", "ai", 2, "High density of LLM-style comment phrasing",
                     f"{M['ai_phrase_per_100_comments']} hits per 100 comments.",
                     examples["explainer"] + examples["emphasis_label"]))
    if M["emoji_in_log_strings"] >= 2 or M["emoji_in_comments"] >= 3:
        S.append(sig(A, "EMOJI_IN_CODE", "ai", 3 if M["emoji_in_log_strings"] >= 3 else 2,
                     "Emoji in log messages / comments",
                     f"{M['emoji_in_log_strings']} log line(s) and {M['emoji_in_comments']} comment emoji – a common LLM habit.",
                     examples["emoji_logs"] + examples["emoji"]))
    if M["typographic_chars_in_comments"] >= 6:
        S.append(sig(A, "TYPOGRAPHY_IN_COMMENTS", "ai", 1, "Em dashes / arrows / curly quotes in code comments",
                     f"{M['typographic_chars_in_comments']} such characters (hard to type, frequent in LLM text)."))
    if agg["defs"] >= 10 and M["doc_coverage"] >= 0.6:
        S.append(sig(A, "DOC_COVERAGE", "ai", 2 if M["doc_coverage"] < 0.85 else 3,
                     "Nearly every function has a doc comment",
                     f"{int(100 * M['doc_coverage'])}% of {agg['defs']} functions documented – unusual under 48h pressure."))
    if agg["code"] >= 300 and M["comment_ratio"] >= 0.35:
        S.append(sig(A, "HIGH_COMMENT_RATIO", "ai", 1, "Very high comment ratio",
                     f"{M['comment_ratio']:.2f} comment lines per code line."))
    if agg["comments"] >= 20 and agg["restating"] / max(1, agg["comments"]) >= 0.2:
        S.append(sig(A, "RESTATING_COMMENTS", "ai", 1, "Many comments just restate the next line",
                     f"{agg['restating']} of {agg['comments']} comments.", examples["restating"]))
    if M["unused_functions"] >= 5 and M["unused_function_ratio"] >= 0.15:
        S.append(sig(A, "UNUSED_FUNCTIONS", "ai", 1, "Many functions are never called",
                     f"{M['unused_functions']} defined but unreferenced ({int(100 * M['unused_function_ratio'])}%) – "
                     f"'complete API' style generation, or abandoned features (check which).", M["unused_examples"]))
    if gd_mix:
        S.append(sig(A, "GODOT_VERSION_MIXUP", "ai", 2, "Godot 3 and Godot 4 syntax mixed",
                     "LLMs often confuse Godot 3/4 APIs; humans working in one version rarely do.", gd_mix))
    # human signals
    if M["slang_comments"]:
        S.append(sig(A, "SLANG_COMMENTS", "human", 2 if M["slang_comments"] >= 3 else 1,
                     "Informal/frustrated comments", f"{M['slang_comments']} comment(s).", examples["slang"]))
    if M["typos"] >= 2:
        S.append(sig(A, "TYPOS", "human", 2, "Typos / missing apostrophes in comments",
                     f"{M['typos']} hit(s).", examples["typos"]))
    if M["non_english_comments"] >= 2:
        S.append(sig(A, "NON_ENGLISH_COMMENTS", "human", 2, "Comments in the author's own language",
                     f"{M['non_english_comments']} comment(s).", examples["non_english"]))
    if M["commented_out_code_lines"] >= 5:
        S.append(sig(A, "COMMENTED_OUT_CODE", "human", 1 if M["commented_out_code_lines"] < 20 else 2,
                     "Commented-out code left behind", f"{M['commented_out_code_lines']} line(s).",
                     examples["commented_code"]))
    if M["debug_prints"]:
        S.append(sig(A, "DEBUG_LEFTOVERS", "human", 2, "Throw-away debug prints", f"{M['debug_prints']} line(s).",
                     examples["debug_prints"]))
    if M["functions_over_120_lines"] >= 2 or M["max_nesting_depth"] >= 7:
        S.append(sig(A, "SPAGHETTI", "human", 1, "Long functions / deep nesting (jam-style code)",
                     f"{M['functions_over_120_lines']} function(s) over 120 lines; max nesting {M['max_nesting_depth']}."))
    if M["duplicate_6line_block_ratio"] >= 0.08:
        S.append(sig(A, "COPY_PASTE", "human", 1, "Copy-pasted blocks",
                     f"{int(100 * M['duplicate_6line_block_ratio'])}% of 6-line windows repeat."))
    if M["todo_terse"] >= 3 and M["todo_verbose"] == 0:
        S.append(sig(A, "TERSE_TODOS", "human", 1, "Terse TODO/FIXME/HACK notes", f"{M['todo_terse']} of them."))
    if M["todo_verbose"] >= 3:
        S.append(sig(A, "VERBOSE_TODOS", "ai", 1, "Polished full-sentence TODOs", f"{M['todo_verbose']} of them."))
    per_file.sort(key=lambda f: (f["ai_points"] - f["human_points"], f["code"]), reverse=True)
    return S, M, per_file


def vibe_single_file(root, records):
    """Pattern of LLM one-shot web games: one big HTML/JS file, canvas primitives,
    oscillator sounds, emoji sprites, localStorage high score."""
    own_code = [r for r in records if r["own"] and r["category"] in ("code", "markup") and r.get("loc")]
    big = [r for r in own_code if r["ext"] in (".html", ".htm", ".js") and r["loc"]["code"] >= 400]
    if not big or len([r for r in own_code if r["category"] == "code"]) > 4:
        return None
    r = max(big, key=lambda x: x["loc"]["code"])
    t = read_text(root / r["path"], 3_000_000)
    feats = {
        "canvas 2D primitives": len(re.findall(r"\b(fillRect|arc|beginPath|fillText|strokeRect|createLinearGradient)\(", t)) >= 15,
        "WebAudio oscillator sfx": "createOscillator" in t,
        "emoji drawn as sprites": bool(re.search(r"fillText\(\s*['\"`][^'\"`]*[\U0001F300-\U0001FAFF]", t)),
        "localStorage high score": bool(re.search(r"localStorage\.(get|set)Item\([^)]*(high|best)", t, re.I)),
        "menu/playing/gameOver state machine": bool(re.search(r"['\"](gameOver|game_over|playing|menu)['\"]", t)),
        "'Press Start 2P' font": "Press Start 2P" in t,
        "particles + screen shake helpers": bool(re.search(r"(screenShake|shakeIntensity|createParticles?|particles\.push)", t)),
        "no image/audio asset files": not any(x["category"] in ("art", "audio") and x["own"] for x in records),
    }
    n = sum(feats.values())
    return {"file": r["path"], "code_lines": r["loc"]["code"], "features": [k for k, v in feats.items() if v], "score": n}


# ============================================================== commits / velocity
def commit_signals(stage2, records, repo):
    src = stage2.get("source", {})
    tl = src.get("timeline") or []
    S, M = [], {}
    if not tl:
        return S, M
    win_tl = [t for t in tl if t["phase"] in ("during", "submission_hour")]
    own_paths = {}
    for t in tl:
        own_code = 0
        for f, a, d in t.get("file_stats", []):
            if f not in own_paths:
                info = classify(f)
                own_paths[f] = info["category"] == "code" and is_own(info)
            if own_paths[f]:
                own_code += a
        t["own_code_added"] = own_code
    trailers = [t for t in tl if t.get("ai_trailer")]
    M["commits_total"], M["commits_in_window"] = len(tl), len(win_tl)
    M["ai_trailer_commits"] = len(trailers)
    if trailers:
        share = len(trailers) / max(1, len(tl))
        S.append(sig("code", "AI_COMMIT_TRAILERS", "ai", 4,
                     "Commits signed by an AI coding agent",
                     f"{len(trailers)} of {len(tl)} commits carry AI co-author/generator trailers ({int(100 * share)}%).",
                     [f"{t['sha']} {t['time'][:16]}: {t['message'].splitlines()[0][:80]}" for t in trailers[:4]]))
    M["ai_trailer_share"] = round(len(trailers) / max(1, len(tl)), 2)
    if repo:
        br = git(repo, "branch", "-r", "--format=%(refname:short)").stdout.split()
        ai_br = [b for b in br if AI_BRANCH_RE.match(b)]
        if ai_br:
            S.append(sig("code", "AI_AGENT_BRANCHES", "ai", 3, "Branches created by AI agents", ", ".join(ai_br[:6])))
    msgs = [t["message"] for t in win_tl] or [t["message"] for t in tl]
    conv = sum(1 for m in msgs if re.match(r"^(feat|fix|chore|docs|style|refactor|perf|test|build|ci)(\(.+\))?!?:\s", m))
    bullets = sum(1 for m in msgs if len(re.findall(r"^\s*[-*•]\s+\S", m, re.M)) >= 3)
    emoji = sum(1 for m in msgs if EMOJI_RE.search(m))
    casual = sum(1 for m in msgs if re.match(r"^(wip|stuff|update|updates|changes|fix|fixes|more|asdf\w*|test|commit|"
                                              r"save|progress|done|final|aaa+|ugh|idk|lol|\.+|sleep|bed|food|stuff+|"
                                              r"[a-z]+)$", m.strip(), re.I) or HUMAN_SLANG_RE.search(m))
    M.update(conventional_commit_ratio=round(conv / max(1, len(msgs)), 2), bulleted_messages=bullets,
             emoji_messages=emoji, casual_messages=casual,
             avg_message_length=round(statistics.mean(len(m) for m in msgs), 1) if msgs else 0)
    if len(msgs) >= 5 and (conv / len(msgs) >= 0.7 or bullets / len(msgs) >= 0.4):
        S.append(sig("code", "AGENT_STYLE_COMMITS", "ai", 2, "Commit messages read like generated ones",
                     f"{conv} conventional-commit and {bullets} multi-bullet messages of {len(msgs)}.",
                     [m.splitlines()[0][:80] for m in msgs[:3]]))
    if len(msgs) >= 4 and casual / len(msgs) >= 0.3:
        S.append(sig("process", "CASUAL_COMMITS", "human", 1, "Casual, terse commit messages",
                     f"{casual} of {len(msgs)} (e.g. 'wip', 'stuff').", [m[:60] for m in msgs if len(m) < 25][:4]))
    # velocity
    prev = None
    rates, bursts = [], []
    for t in win_tl:
        tt = parse_time(t["time"])
        gap_h = max(0.25, (tt - prev).total_seconds() / 3600) if prev else None
        if gap_h and t["own_code_added"] >= 150 and t["files_created"] < 30:
            rate = t["own_code_added"] / gap_h
            rates.append(rate)
            if rate >= 600:
                bursts.append(f"{t['sha']} {t['time'][11:16]}: +{t['own_code_added']} code lines "
                              f"{gap_h * 60:.0f} min after the previous commit (≈{rate:.0f}/h)")
        prev = tt
    total_added = sum(t["own_code_added"] for t in win_tl)
    burst_lines = sum(int(b.split("+")[1].split(" ")[0]) for b in bursts) if bursts else 0
    M["burst_line_share"] = round(burst_lines / max(1, total_added), 2)
    times = [parse_time(t["time"]) for t in win_tl]
    gaps = [(b - a).total_seconds() / 3600 for a, b in zip(times, times[1:])]
    M.update(own_code_lines_added_in_window=total_added, longest_gap_hours=round(max(gaps), 1) if gaps else None,
             active_span_hours=round(hours_between(times[0], times[-1]), 1) if len(times) > 1 else 0,
             high_rate_commits=len(bursts))
    if bursts:
        S.append(sig("code", "IMPLAUSIBLE_BURSTS", "ai", 3 if len(bursts) >= 2 else 2,
                     "Code added faster than it can be typed",
                     f"{len(bursts)} commit(s) imply > 600 new code lines per hour (excluding bulk imports).", bursts))
    if gaps and max(gaps) >= 4:
        S.append(sig("process", "SLEEP_GAP", "human", 1, "Normal human rhythm (long breaks)",
                     f"Longest pause between commits: {max(gaps):.1f} h."))
    if len(win_tl) >= 15:
        S.append(sig("process", "MANY_SMALL_COMMITS", "human", 1, "Many incremental commits during the event",
                     f"{len(win_tl)} commits in the window."))
    return S, M


def feasibility(stage2, cm):
    S = stage2.get("summary", {})
    w = (S.get("loc_written_during") or {}).get("code", 0)
    added = cm.get("own_code_lines_added_in_window")
    basis = max(w, added or 0)
    tier = ("typical (≤3k)" if basis <= 3000 else "high but plausible (3–6k)" if basis <= 6000 else
            "exceptional – needs supporting evidence (6–10k)" if basis <= 10000 else
            "implausible for hand-written code in 48h (>10k)")
    out = {"loc_written_during": w, "lines_added_git": added, "tier": tier,
           "note": "Heuristic: experienced solo jammers usually write 1–4k lines in 48h while also making art "
                   "and audio; >600 lines/hour sustained exceeds typing speed for new code."}
    sigs = []
    if basis > 6000:
        sigs.append(sig("code", "LOC_FEASIBILITY", "ai", 2 if basis <= 10000 else 3,
                        "Amount of code is hard to write alone in 48h", f"{human_int(basis)} lines: {tier}."))
    return sigs, out


# ============================================================== artwork
def image_stats(path):
    from PIL import Image
    try:
        im = Image.open(path)
        frames = getattr(im, "n_frames", 1)
        im.seek(0)
        w, h = im.size
        rgba = im.convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    if w * h > 4_000_000:
        rgba = rgba.crop((0, 0, min(w, 2048), min(h, 2048)))
    st = {"w": w, "h": h, "frames": frames}
    colors = rgba.getcolors(maxcolors=1 << 18)
    vis = [c for c in colors if c[1][3] > 0] if colors else None
    st["unique_colors"] = len(vis) if vis is not None else 262144
    if vis:
        q = {(r >> 3, g >> 3, b >> 3) for _, (r, g, b, a) in vis}
        st["shade_ratio"] = round(len(vis) / max(1, len(q)), 2)
    a = rgba.getchannel("A").histogram()
    total = sum(a)
    transparent, opaque = a[0], a[255]
    visible = total - transparent
    st["has_transparency"] = transparent > 0
    st["semi_alpha_ratio"] = round((visible - opaque) / max(1, visible), 3)
    st["block"] = block_size(rgba)
    st["checkerboard"] = baked_checkerboard(rgba)
    return st


def block_size(rgba):
    """Integer upscale factor of pixel art (1 = native / not blocky)."""
    try:
        import numpy as np
        arr = np.asarray(rgba)
    except Exception:  # noqa: BLE001
        return None
    h, w = arr.shape[:2]
    runs = []
    for y in range(0, h, max(1, h // 24)):
        row = arr[y]
        change = np.any(row[1:] != row[:-1], axis=1)
        idx = np.flatnonzero(change)
        if len(idx) > 1:
            runs.extend(np.diff(idx).tolist())
    if len(runs) < 10:
        return None
    g = 0
    for r in runs[:2000]:
        g = math.gcd(g, int(r))
        if g == 1:
            return 1
    return int(g)


def baked_checkerboard(rgba):
    w, h = rgba.size
    if w < 64 or h < 64:
        return False
    tile = rgba.crop((0, 0, 48, 48)).convert("RGB")
    cols = tile.getcolors(4096) or []
    cols.sort(reverse=True)
    if len(cols) < 2:
        return False
    (n1, c1), (n2, c2) = cols[0], cols[1]
    if (n1 + n2) < 0.9 * 48 * 48:
        return False
    l1, l2 = sum(c1) / 3, sum(c2) / 3
    if not (l1 > 150 and l2 > 150 and 8 < abs(l1 - l2) < 90):
        return False
    px = tile.load()
    changes = sum(1 for x in range(1, 48) if px[x, 4] != px[x - 1, 4])
    return 2 <= changes <= 12


def art_signals(root, records, max_images):
    imgs = [r for r in records if r["own"] and r["category"] == "art" and
            r["ext"] in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tga") and not r.get("lfs_pointer")]
    raw = [r for r in records if r["own"] and r["category"] == "art_raw"]
    S, M = [], {}
    meta_ai = [r for r in records if r["category"] in ("art", "art_raw") and
               (r.get("ai_meta_hits") or r.get("generation_params") or
                "trainedalgorithmic" in (r.get("digital_source_type") or "").lower())]
    c2pa = [r for r in records if r["category"] == "art" and r.get("c2pa")]
    if meta_ai:
        S.append(sig("artwork", "AI_IMAGE_METADATA", "ai", 4, "Image files carry AI-generator metadata",
                     f"{len(meta_ai)} file(s).",
                     [f"{r['path']}: {', '.join(r.get('ai_meta_hits') or ['generation parameters'])}" for r in meta_ai]))
    if c2pa:
        S.append(sig("artwork", "C2PA_MANIFEST", "ai", 3, "Content Credentials (C2PA) manifests present",
                     "Usually written by AI generators (OpenAI, Adobe Firefly, Google, Microsoft); verify the issuer.",
                     [r["path"] for r in c2pa]))
    import importlib.util
    have_pil = importlib.util.find_spec("PIL") is not None
    if not have_pil:
        M["note"] = "Pillow not installed – pixel statistics skipped (pip install pillow)."
    stats = []
    if have_pil:
        sample = imgs if len(imgs) <= max_images else random.Random(1).sample(imgs, max_images)
        for r in sample:
            st = image_stats(root / r["path"])
            if "error" in st:
                continue
            st["path"] = r["path"]
            stats.append(st)
    flagged = collections.defaultdict(list)
    effort_min = 0.0
    palettes = []
    for st in stats:
        w, h, u = st["w"], st["h"], st["unique_colors"]
        k = st.get("block") or 1
        native = (w // k, h // k)
        pixel_art = (u <= 64 and max(native) <= 512) or (k >= 2 and u <= 256)
        st["pixel_art"] = pixel_art
        if st.get("checkerboard"):
            flagged["checkerboard"].append(st["path"])
        if (w, h) in AI_SIZES and not (pixel_art and k >= 4):
            flagged["ai_size_pot" if (w, h) in POT_SQUARES else "ai_size"].append(f"{st['path']} ({w}×{h})")
        if max(native) <= 128 and u > 512:
            flagged["many_colors_small"].append(f"{st['path']} ({w}×{h}, {u} colours)")
        if st.get("shade_ratio", 1) >= 4 and u >= 1500 and max(w, h) <= 1024 and st["has_transparency"]:
            flagged["noisy_sprite"].append(f"{st['path']} ({u} colours, shade ratio {st['shade_ratio']})")
        if st["has_transparency"] and st["semi_alpha_ratio"] >= 0.08 and u > 256 and max(w, h) <= 1024:
            flagged["soft_alpha_halo"].append(f"{st['path']} ({int(100 * st['semi_alpha_ratio'])}% semi-transparent)")
        nw, nh = native
        f = max(1, st["frames"])
        if pixel_art:
            effort_min += (4 + 3 * (nw * nh) / 1024) * min(f, 60)
        elif u > 256:
            effort_min += (30 + 20 * (w * h) / 65536) * min(f, 12)
        else:
            effort_min += 10 * min(f, 12)
        if u <= 64 and not st.get("checkerboard"):
            palettes.append(st)
    if flagged["checkerboard"]:
        S.append(sig("artwork", "BAKED_CHECKERBOARD", "ai", 3, "Fake transparency checkerboard baked into images",
                     "Typical of AI images saved with a 'transparent' preview background.", flagged["checkerboard"]))
    if flagged["ai_size"]:
        S.append(sig("artwork", "AI_CANVAS_SIZES", "ai", 2, "Images at exact AI-generator output sizes",
                     f"{len(flagged['ai_size'])} file(s) (non-power-of-two SDXL/FLUX/GPT-image sizes).", flagged["ai_size"]))
    if len(flagged["ai_size_pot"]) >= 3:
        S.append(sig("artwork", "AI_CANVAS_SIZES_POT", "ai", 1, "Several 512/1024/2048 square images",
                     "Also common for hand-made textures; only meaningful with other signals.", flagged["ai_size_pot"]))
    if flagged["many_colors_small"]:
        S.append(sig("artwork", "SMALL_SPRITES_MANY_COLOURS", "ai", 2, "Small sprites with photo-like colour counts",
                     "Hand-made sprites this small rarely use >512 colours (downscaled generated/photo art?).",
                     flagged["many_colors_small"]))
    if flagged["noisy_sprite"]:
        S.append(sig("artwork", "NOISY_SHADES", "ai", 1, "Sprites made of many near-identical shades",
                     "Painterly or generated; check visually.", flagged["noisy_sprite"]))
    if flagged["soft_alpha_halo"]:
        S.append(sig("artwork", "SOFT_ALPHA_EDGES", "ai", 1, "Soft semi-transparent fringes (background removal?)",
                     "", flagged["soft_alpha_halo"]))
    # raw files / process
    stems = {Path(r["path"]).stem.lower().split(".")[0] for r in raw}
    matched = [r for r in imgs if any(Path(r["path"]).stem.lower().startswith(s) or s in Path(r["path"]).stem.lower()
                                      for s in stems if len(s) >= 3)]
    ase = [r for r in raw if r["ext"] in (".aseprite", ".ase")]
    frames = sum((r.get("media") or {}).get("frames", 0) or 0 for r in ase)
    layers = sum((r.get("media") or {}).get("layers", 0) or 0 for r in raw)
    edit_s = sum((r.get("media") or {}).get("editing_seconds", 0) or 0 for r in raw)
    tl = sum((r.get("media") or {}).get("timelapse_segments", 0) or 0 for r in raw)
    M.update(images=len(imgs), images_analysed=len(stats), raw_files=len(raw),
             raw_by_ext=dict(collections.Counter(r["ext"] for r in raw)),
             exports_with_matching_raw=len(matched), aseprite_frames=frames, raw_layers=layers,
             krita_editing_minutes=round(edit_s / 60) if edit_s else None, procreate_timelapse_segments=tl or None,
             pixel_art_share=round(sum(1 for s in stats if s.get("pixel_art")) / max(1, len(stats)), 2),
             median_unique_colours=int(statistics.median([s["unique_colors"] for s in stats])) if stats else None,
             estimated_art_hours=round(effort_min / 60, 1), flags={k: len(v) for k, v in flagged.items()})
    if raw:
        strength = 3 if (frames >= 20 or layers >= 15 or edit_s >= 3600 or tl) else 2
        S.append(sig("artwork", "RAW_ART_FILES", "human", strength, "Layered/animated source art files included",
                     f"{len(raw)} raw file(s) ({', '.join(f'{k} {v}' for k, v in M['raw_by_ext'].items())}); "
                     f"{len(matched)}/{len(imgs)} exported images have a matching source file"
                     + (f"; {frames} Aseprite frames" if frames else "")
                     + (f"; {M['krita_editing_minutes']} min Krita editing time" if edit_s else "")
                     + ("; Procreate time-lapse recorded" if tl else "") + "."))
    if len(palettes) >= 5:
        S.append(sig("artwork", "SMALL_PALETTE_PIXEL_ART", "human", 1, "Consistent low-colour pixel art",
                     f"{len(palettes)} images use ≤64 colours (hand-placed pixels are typical)."))
    # visual review queue
    queue = []
    for key in ("checkerboard", "ai_size", "many_colors_small", "noisy_sprite", "soft_alpha_halo"):
        for item in flagged[key]:
            p = item.split(" (")[0]
            if p not in queue:
                queue.append(p)
    rest = sorted(stats, key=lambda s: (s["w"] * s["h"], s["unique_colors"]), reverse=True)
    for s in rest:
        if len(queue) >= 10:
            break
        if s["path"] not in queue:
            queue.append(s["path"])
    small = [s["path"] for s in stats if s.get("pixel_art") and s["path"] not in queue]
    queue += random.Random(2).sample(small, min(2, len(small)))
    return S, M, queue[:12], stats


# ============================================================== audio
def audio_signals(records, texts):
    aud = [r for r in records if r["own"] and r["category"] == "audio"]
    raw = [r for r in records if r["own"] and r["category"] == "music_raw"]
    S, M = [], {}
    meta_ai = [r for r in aud if r.get("ai_meta_hits")]
    if meta_ai:
        S.append(sig("music_sound", "AI_AUDIO_METADATA", "ai", 4, "Audio files carry AI-generator metadata",
                     f"{len(meta_ai)} file(s).", [f"{r['path']}: {', '.join(r['ai_meta_hits'])}" for r in meta_ai]))
    named = [r["path"] for r in aud if AI_AUDIO_NAME_RE.search(Path(r["path"]).name)]
    if named:
        S.append(sig("music_sound", "AI_AUDIO_FILENAMES", "ai", 3, "File names from AI audio services", "", named))
    gen = [r["path"] for r in aud if GENERATOR_SFX_RE.search(r["path"])]
    durations = [(r["path"], (r.get("media") or {}).get("duration")) for r in aud]
    music = [(p, d) for p, d in durations if d and d >= 30]
    tools = collections.Counter(t for r in aud for t in r.get("tools", []))
    beepbox = [u for t in texts for u in re.findall(r"https?://(?:www\.)?(?:beepbox\.co|jummb\.us|ultrabox\.\w+|"
                                                    r"pandabox\.\w+)/\S+", t)]
    M.update(audio_files=len(aud), music_tracks=len(music), music_minutes=round(sum(d for _, d in music) / 60, 1),
             generator_named_sfx=len(gen), project_files=len(raw),
             project_by_ext=dict(collections.Counter(r["ext"] for r in raw)),
             tools=dict(tools.most_common(8)), beepbox_links=len(beepbox))
    if raw or beepbox:
        S.append(sig("music_sound", "MUSIC_PROJECT_FILES", "human", 3,
                     "Music/SFX project files or editor links included",
                     f"{len(raw)} project file(s) ({', '.join(f'{k} {v}' for k, v in M['project_by_ext'].items()) or '–'})"
                     + (f", {len(beepbox)} BeepBox-style song link(s)" if beepbox else "") + "."))
    if gen:
        S.append(sig("music_sound", "SFX_GENERATOR", "neutral", 1, "Sound effects from sfxr-style generators",
                     f"{len(gen)} file(s) with generator default names. Allowed in the Compo, but the AI FAQ treats "
                     f"generators like AI: if they did ~90% of the audio work, Audio should be opted out.", gen))
    if music and not raw and not beepbox:
        S.append(sig("music_sound", "MUSIC_WITHOUT_SOURCE", "ai", 1, "Finished music without any project file",
                     f"{len(music)} track(s), {M['music_minutes']} min. Not evidence by itself – many composers "
                     f"export only – but nothing documents how it was made.", [p for p, _ in music]))
    if sum(d for _, d in music) >= 6 * 60:
        S.append(sig("music_sound", "LOTS_OF_MUSIC", "ai", 1, "Unusually much music for a solo 48h entry",
                     f"{M['music_minutes']} minutes across {len(music)} tracks."))
    return S, M


# ============================================================== docs / description / page
def text_metrics(text):
    words = re.findall(r"[A-Za-z']+", text)
    nw = len(words)
    heads = re.findall(r"^#{1,6}\s.*$", text, re.M)
    return {"words": nw, "lines": text.count("\n") + 1, "headings": len(heads),
            "emoji_headings": sum(1 for x in heads if EMOJI_RE.search(x)),
            "sections_typical_of_ai": sorted({m.group(1).lower() for m in README_SECTIONS_RE.finditer(text)}),
            "bold_lead_bullets": len(re.findall(r"^\s*[-*]\s+\*\*[^*]+\*\*\s*[:–—-]", text, re.M)),
            "tree_diagram": bool(re.search(r"[├└]──", text)),
            "ai_vocab_hits": sorted({m.group(0).lower() for m in AI_PROSE_RE.finditer(text)}),
            "ai_vocab_per_100w": round(100 * len(AI_PROSE_RE.findall(text)) / max(1, nw), 2),
            "em_dashes": text.count("—"), "emoji": len(EMOJI_RE.findall(text)),
            "llm_names": sorted(set(LLM_NAMES_RE.findall(text))),
            "casual_markers": len(HUMAN_SLANG_RE.findall(text)) + len(TYPO_RE.findall(text))}


def docs_signals(root, records):
    docs = [r for r in records if r["own"] and r["category"] == "doc" and r["ext"] in (".md", ".txt", ".markdown", ".rst", "(none)")]
    S, M = [], {"doc_files": len(docs)}
    readmes = [r for r in docs if Path(r["path"]).name.lower().startswith("readme")]
    total_words = 0
    for r in docs:
        total_words += len(read_text(root / r["path"], 1_000_000).split())
    M["doc_words_total"] = total_words
    if readmes:
        r = sorted(readmes, key=lambda x: x["path"].count("/"))[0]
        t = read_text(root / r["path"], 1_000_000)
        tm = text_metrics(t)
        M["readme"] = dict(tm, path=r["path"], created_phase=r.get("created_phase"), modified_phase=r.get("modified_phase"))
        score = 0
        reasons = []
        if tm["emoji_headings"] >= 2:
            score += 2
            reasons.append(f"{tm['emoji_headings']} emoji headings")
        if len(tm["sections_typical_of_ai"]) >= 6:
            score += 2
            reasons.append(f"{len(tm['sections_typical_of_ai'])} boiler-plate sections ({', '.join(tm['sections_typical_of_ai'][:6])})")
        if tm["bold_lead_bullets"] >= 4:
            score += 1
            reasons.append(f"{tm['bold_lead_bullets']} '**Label**: text' bullets")
        if tm["tree_diagram"]:
            score += 2
            reasons.append("project-structure tree diagram")
        if tm["ai_vocab_per_100w"] >= 0.8 and len(tm["ai_vocab_hits"]) >= 3:
            score += 1
            reasons.append(f"LLM vocabulary ({', '.join(tm['ai_vocab_hits'][:5])})")
        if tm["words"] >= 700:
            score += 1
            reasons.append(f"{tm['words']} words – long for a jam README")
        if tm["em_dashes"] >= 5:
            score += 1
            reasons.append(f"{tm['em_dashes']} em dashes")
        if score >= 3:
            S.append(sig("documentation", "AI_STYLE_README", "ai", 3 if score >= 6 else 2,
                         "README has the shape of an LLM-written README", "; ".join(reasons)))
        if tm["casual_markers"] >= 3 and score < 3:
            S.append(sig("documentation", "CASUAL_README", "human", 1, "README is casual / has typos",
                         f"{tm['casual_markers']} markers."))
        if r.get("created_phase") == "after":
            S.append(sig("documentation", "README_AFTER_DEADLINE", "neutral", 1, "README written after the deadline", ""))
    big_docs = [r["path"] for r in docs if r not in readmes and len(read_text(root / r["path"], 200000).split()) > 400]
    if len(big_docs) >= 2:
        S.append(sig("documentation", "EXTENSIVE_DOCS", "ai", 2, "Several long design/technical documents",
                     "Developers rarely write these during a 48h jam; agents often do.", big_docs))
    return S, M


def page_signals(game):
    g = game.get("game") or {}
    body = g.get("body") or ""
    S, M = [], {}
    tm = text_metrics(body)
    M["description"] = tm
    score = 0
    if tm["emoji_headings"] >= 2 or tm["emoji"] >= 6:
        score += 1
    if tm["bold_lead_bullets"] >= 4:
        score += 1
    if tm["ai_vocab_per_100w"] >= 1 and len(tm["ai_vocab_hits"]) >= 3:
        score += 2
    if tm["em_dashes"] >= 3:
        score += 1
    if tm["llm_names"]:
        score += 1
    if score >= 3:
        S.append(sig("idea", "AI_STYLE_DESCRIPTION", "ai", 2, "Game description reads like LLM marketing copy",
                     f"vocabulary: {', '.join(tm['ai_vocab_hits'][:6]) or '–'}; em dashes {tm['em_dashes']}; "
                     f"names: {', '.join(tm['llm_names']) or '–'}"))
    if tm["llm_names"]:
        S.append(sig("idea", "LLM_FAVOURITE_NAMES", "ai", 1, "Names LLMs over-use",
                     ", ".join(tm["llm_names"])))
    if tm["casual_markers"] >= 2 and score < 2:
        S.append(sig("idea", "CASUAL_DESCRIPTION", "human", 1, "Casual, personal description", ""))
    disclosures = []
    for src, text in [("description", body)] + [
            (f"comment by {'the author' if c.get('is_game_author') else (c.get('author_name') or 'someone')} "
             f"({(c.get('created') or '')[:10]})", c.get("body") or "") for c in game.get("comments", [])]:
        for m in DISCLOSURE_RE.finditer(text):
            s = max(0, m.start() - 90)
            ctx = re.sub(r"\s+", " ", text[s:m.end() + 90])
            if re.search(r"\b(enemy|enemies|npc|opponent|bot|pathfind\w*|behaviou?r|state machine) (ai)\b|"
                         r"\bai (enemy|enemies|opponent|behaviou?r|controller|pathfind\w*|logic|script)\b", ctx, re.I):
                continue
            disclosures.append({"where": src, "match": m.group(0), "context": ctx})
    M["disclosures"] = disclosures[:20]
    if any(d["where"] == "description" or "the author" in d["where"] for d in disclosures):
        S.append(sig("idea", "AI_MENTIONED_BY_AUTHOR", "neutral", 3, "The author mentions AI tools",
                     "Read the context: it may be a disclosure (decisive for that category) or a 'no AI' statement.",
                     [f"{d['where']}: …{d['context']}…" for d in disclosures if d["where"] == "description" or "the author" in d["where"]][:4]))
    others = [d for d in disclosures if d["where"] != "description" and "the author" not in d["where"]]
    if others:
        S.append(sig("idea", "AI_MENTIONED_BY_OTHERS", "neutral", 1, "Other participants mention AI in comments",
                     "Accusations are not evidence by themselves – weigh against the files.",
                     [f"{d['where']}: …{d['context']}…" for d in others][:4]))
    posts = game.get("author_posts", [])
    pmedia = sum(p.get("images", 0) for p in posts)
    M["author_posts"] = {"count": len(posts), "images": pmedia, "gifs": sum(p.get("gifs", 0) for p in posts)}
    if posts and pmedia:
        S.append(sig("process", "PROGRESS_POSTS", "human", 2 if pmedia >= 3 else 1,
                     "The author posted progress during the event",
                     f"{len(posts)} post(s) with {pmedia} image(s)/GIF(s).",
                     [f"{p['published'][:16] if p.get('published') else '?'}: {p['name']}" for p in posts][:5]))
    media = game.get("media_links", []) + [u for p in posts for u in p.get("media_links", [])]
    if media:
        S.append(sig("process", "TIMELAPSE_OR_STREAM", "human", 3, "Timelapse / stream / video links",
                     "Recorded development is strong evidence of human work – open the links to confirm.", media[:5]))
    hist = game.get("author_history", [])
    M["author_history"] = [{"name": h["name"], "format": h["format"], "published": h["published"]} for h in hist][-8:]
    return S, M


def runtime_ai(root, records):
    hits = []
    for r in records:
        if r["own"] and Path(r["path"]).name.lower() in ("package.json", "requirements.txt", "cargo.toml", "pyproject.toml"):
            t = read_text(root / r["path"], 200000)
            hits += [f"{r['path']}: {m.group(1)}" for m in RUNTIME_AI_RE.finditer(t)]
    return hits


def tool_artifacts(root: Path, snapshot: Path, repo):
    found = []
    roots = {root, snapshot} if snapshot else {root}
    per_label = collections.Counter()
    skip = {".git", "node_modules", ".godot", "Library", "Temp", ".import", "__pycache__"}
    for base in roots:
        if not base or not base.exists():
            continue
        stack = [base]
        while stack:
            d = stack.pop()
            try:
                children = list(d.iterdir())
            except OSError:
                continue
            for p in children:
                if p.name in skip:
                    continue
                rel = p.relative_to(base).as_posix()
                for rx, label, strength in AI_FILES:
                    if re.search(rx, rel if p.is_file() else rel + "/"):
                        per_label[label] += 1
                        if per_label[label] <= 3 and not any(f["path"].endswith(rel) for f in found):
                            found.append({"path": rel, "tool": label, "strength": strength, "in": "snapshot"})
                        break
                if p.is_dir():
                    stack.append(p)
        gi = base / ".gitignore"
        if gi.exists():
            for m in AI_IGNORE_RE.finditer(read_text(gi, 100000)):
                found.append({"path": ".gitignore", "tool": f"ignores '{m.group(1)}'", "strength": 3, "in": "gitignore"})
        mf = base / "Packages" / "manifest.json"
        if mf.exists():
            for m in re.finditer(r'"(com\.unity\.(ai|muse)[\w.-]*)"', read_text(mf, 100000)):
                found.append({"path": "Packages/manifest.json", "tool": f"Unity AI package {m.group(1)}", "strength": 3, "in": "snapshot"})
        addons = base / "addons"
        if addons.is_dir():
            for d in addons.iterdir():
                if re.search(r"(^|[_-])(ai|copilot|gpt|llm|assistant|ziva|ollama|claude|gemini|codeium)([_-]|$)", d.name, re.I):
                    found.append({"path": f"addons/{d.name}", "tool": "Godot AI assistant plugin", "strength": 3, "in": "snapshot"})
    if repo:
        r = git(repo, "log", "--all", "--diff-filter=A", "--name-only", "--format=@@%at", timeout=300)
        t = None
        for ln in r.stdout.splitlines():
            if ln.startswith("@@"):
                t = ln[2:]
                continue
            for rx, label, strength in AI_FILES:
                if ln and re.search(rx, ln) and not any(f["path"] == ln for f in found):
                    import datetime as _d
                    when = _d.datetime.fromtimestamp(int(t), _d.timezone.utc) if t and t.isdigit() else None
                    found.append({"path": ln, "tool": label, "strength": strength, "in": f"git history (added {fmt_time(when)})"})
                    break
    return found


# ============================================================== leans
LEVELS = ["NO_AI", "AI_CONSULTED", "AI_ASSISTED", "AI_GENERATED"]


def lean(signals, present=True, extent=None):
    """Heuristic suggestion. Strength of evidence says *whether* AI was used;
    `extent` (0-1, share of the content affected) says *how much*."""
    if not present:
        return {"suggestion": "N/A", "ai_score": 0, "human_score": 0, "basis": "no content of this kind"}
    ai = sum(s["strength"] ** 1.6 for s in signals if s["polarity"] == "ai")
    hu = sum(s["strength"] ** 1.6 for s in signals if s["polarity"] == "human")
    decisive = any(s["strength"] >= 4 and s["polarity"] == "ai" for s in signals)
    net = max(0.0, ai - 0.6 * hu)
    ext = extent if extent is not None else 0.0
    # P(any AI use): decisive artifacts ≈ proof; otherwise grows with net evidence
    p_ai = 0.97 if decisive else min(0.9, max(0.03, 1 - math.exp(-net / 6)))
    # how much: split the AI mass by the share of content that carries evidence
    # extent is itself a rough estimate, so never push all mass into one level
    g = min(0.8, max(0.0, (ext - 0.3) / 0.6))
    c = min(0.7, max(0.0, (0.15 - ext) / 0.15) * (0.3 if decisive else 1.0))
    a = max(0.0, 1 - g - c)
    dist = {"NO_AI": 1 - p_ai, "AI_CONSULTED": p_ai * c, "AI_ASSISTED": p_ai * a, "AI_GENERATED": p_ai * g}
    dist = {k: round(100 * v) for k, v in dist.items()}
    top = max(dist, key=dist.get)
    return {"suggestion": top, "probabilities": dist, "p_ai_used": round(100 * p_ai),
            "ai_score": round(ai, 1), "human_score": round(hu, 1),
            "extent_estimate": round(ext, 2) if extent is not None else None,
            "basis": "automatic heuristic prior – adjust after reviewing the files"}


def extents(code_M, per_file, art_M, art_stats, records, aud_S, doc_M):
    """Rough share of each area that carries AI evidence."""
    out = {}
    cm = code_M.get("commits", {}) if isinstance(code_M.get("commits"), dict) else {}
    lines = sum(f["code"] for f in per_file) or 1
    ai_files = sum(f["code"] for f in per_file if f["ai_points"] >= 3 and f["ai_points"] > f["human_points"])
    out["code"] = max(cm.get("ai_trailer_share", 0), ai_files / lines, cm.get("burst_line_share", 0))
    flagged = {p.split(" (")[0] for s in art_stats for p in [s["path"]]
               if s.get("checkerboard") or (s["w"], s["h"]) in AI_SIZES - POT_SQUARES}
    flagged |= {r["path"] for r in records if r["category"] == "art" and (r.get("ai_meta_hits") or r.get("generation_params"))}
    n_img = max(1, art_M.get("images", 0))
    out["artwork"] = min(1.0, len(flagged) / n_img)
    aud = [r for r in records if r["own"] and r["category"] == "audio"]
    dur = lambda r: (r.get("media") or {}).get("duration") or 1  # noqa: E731
    ai_aud = [r for r in aud if r.get("ai_meta_hits") or AI_AUDIO_NAME_RE.search(Path(r["path"]).name)]
    out["music_sound"] = sum(dur(r) for r in ai_aud) / max(1, sum(dur(r) for r in aud))
    out["documentation"] = 1.0 if any(s["id"] == "AI_STYLE_README" for s in doc_M.get("_signals", [])) else 0.0
    return out


# ============================================================== main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--max-images", type=int, default=400)
    args = ap.parse_args()
    wd = Path(args.workdir).resolve()
    game = load_json(wd / "game.json") or {}
    s2 = load_json(wd / "stage2.json")
    if not s2:
        raise SystemExit("stage2.json missing – run analyze_static.py first.")
    root = Path(s2["analysis_root"])
    records = s2["files"]
    src = s2.get("source", {})
    repo = src.get("repo") if src.get("mode") == "git" else None
    snapshot = Path(src["snapshot"]) if src.get("snapshot") else None

    log("Scanning for AI tool artifacts…")
    artifacts = tool_artifacts(root, snapshot, repo)
    log("Analysing code…")
    code_S, code_M, per_file = code_signals(root, records, s2, src.get("timeline"))
    vibe = vibe_single_file(root, records)
    if vibe and vibe["score"] >= 5:
        code_S.append(sig("code", "ONE_FILE_WEB_GAME_PATTERN", "ai", 2 if vibe["score"] < 7 else 3,
                          "Matches the 'one-shot LLM web game' template",
                          f"{vibe['file']} ({vibe['code_lines']} lines): " + ", ".join(vibe["features"])))
    com_S, com_M = commit_signals(s2, records, repo)
    feas_S, feas = feasibility(s2, com_M)
    for a in artifacts:
        area = "code"
        code_S.append(sig(area, "AI_TOOL_FILE", "ai", a["strength"], f"AI tool artifact: {a['tool']}",
                          f"{a['path']} ({a['in']})"))
    log("Analysing artwork…")
    art_S, art_M, img_queue, img_stats = art_signals(root, records, args.max_images)
    texts = [(game.get("game") or {}).get("body") or ""] + [read_text(root / r["path"], 300000) for r in records
                                                            if r["own"] and r["category"] == "doc"][:20]
    aud_S, aud_M = audio_signals(records, texts)
    doc_S, doc_M = docs_signals(root, records)
    page_S, page_M = page_signals(game)
    rt = runtime_ai(root, records)

    areas = {
        "code": {"signals": code_S + [s for s in com_S if s["area"] == "code"] + feas_S,
                 "metrics": dict(code_M, commits=com_M), "present": bool(code_M["files"])},
        "artwork": {"signals": art_S, "metrics": art_M,
                    "present": bool(art_M.get("images") or art_M.get("raw_files")) or
                    any(r["category"] in ("art", "art_3d") and r["own"] for r in records)},
        "music_sound": {"signals": aud_S, "metrics": aud_M, "present": bool(aud_M["audio_files"] or aud_M["project_files"])},
        "documentation": {"signals": doc_S, "metrics": doc_M, "present": bool(doc_M["doc_files"])},
        "idea": {"signals": [s for s in page_S if s["area"] == "idea"], "metrics": page_M, "present": True},
        "process": {"signals": [s for s in com_S + page_S if s["area"] == "process"],
                    "metrics": {"commits": com_M, "author_posts": page_M.get("author_posts")}, "present": True},
    }
    ext = extents(dict(code_M, commits=com_M), per_file, art_M, img_stats, records, aud_S,
                  {"_signals": doc_S})
    for k, v in areas.items():
        if k != "process":
            sigs = v["signals"] + ([s for s in areas["process"]["signals"]] if k in ("code",) else [])
            v["lean"] = lean(sigs, v["present"], ext.get(k))
    out = {"areas": areas, "ai_tool_artifacts": artifacts, "runtime_ai_dependencies": rt,
           "feasibility": feas, "vibe_pattern": vibe,
           "review_queue": {"code_files": [f["path"] for f in per_file[:5]] +
                            [f["path"] for f in sorted(per_file, key=lambda f: f["ai_points"] - f["human_points"])[:2]
                             if f["path"] not in [g["path"] for g in per_file[:5]]],
                            "images": img_queue,
                            "docs": [r["path"] for r in records if r["own"] and r["category"] == "doc"][:5]},
           "per_file_code_scores": per_file[:40], "image_stats_sample": img_stats[:60]}
    save_json(wd / "stage3_signals.json", out)
    md = render(out, game)
    (wd / "stage3_signals.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"Wrote {wd / 'stage3_signals.json'} and {wd / 'stage3_signals.md'}")


def render(out, game):
    L = ["## Stage 3 – Automated AI-usage signals",
         "_Heuristic evidence for the reviewer; the verdict comes from reviewing the flagged files._", ""]
    names = {"code": "Code", "artwork": "Artwork", "music_sound": "Music & sound", "documentation": "Documentation",
             "idea": "Idea / page", "process": "Process (human-work evidence)"}
    for key in ("code", "artwork", "music_sound", "documentation", "idea", "process"):
        a = out["areas"][key]
        ln = a.get("lean")
        L.append(f"### {names[key]}")
        if ln and ln.get("probabilities"):
            p = ln["probabilities"]
            L.append("Automatic prior: " + " · ".join(f"{k} {v}%" for k, v in p.items()) +
                     f" (P(AI used) {ln['p_ai_used']}%; evidence AI {ln['ai_score']} / human {ln['human_score']}"
                     + (f"; ~{int(100 * ln['extent_estimate'])}% of content flagged" if ln.get("extent_estimate") is not None else "")
                     + ")")
        elif ln:
            L.append(f"Automatic prior: {ln['suggestion']}")
        if not a["signals"]:
            L.append("- no signals")
        for s in sorted(a["signals"], key=lambda s: (-s["strength"], s["polarity"])):
            mark = {"ai": "AI", "human": "HUMAN", "neutral": "NOTE"}[s["polarity"]]
            L.append(f"- [{mark} {s['strength']}] **{s['title']}** – {s['detail']}")
            for e in s["examples"][:8]:
                L.append(f"    - `{str(e)[:150]}`")
            more = s.get("examples_total", len(s["examples"])) - min(8, len(s["examples"]))
            if more > 0:
                L.append(f"    - … {more} more in stage3_signals.json")
        L.append("")
    f = out["feasibility"]
    L.append(f"**Feasibility:** {human_int(f['loc_written_during'])} code lines written in new files"
             + (f", {human_int(f['lines_added_git'])} lines added per git" if f.get("lines_added_git") is not None else "")
             + f" → {f['tier']}. Estimated art workload ≈ {out['areas']['artwork']['metrics'].get('estimated_art_hours', 0)} h "
               f"(very rough).")
    if out["runtime_ai_dependencies"]:
        L.append(f"**Runtime generative AI in the game:** {', '.join(out['runtime_ai_dependencies'])}")
    rq = out["review_queue"]
    L.append("")
    L.append("**Review next:** code " + ", ".join(f"`{p}`" for p in rq["code_files"]) +
             " · images " + ", ".join(f"`{p}`" for p in rq["images"][:8]) +
             (" · docs " + ", ".join(f"`{p}`" for p in rq["docs"]) if rq["docs"] else ""))
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
