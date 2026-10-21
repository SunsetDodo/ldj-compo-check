#!/usr/bin/env python3
"""Final report: combines game.json, stage1/2/3 outputs and the reviewer's
verdicts.json into <workdir>/report.md.

Every decision carries a probability and the evidence behind it:
  * AI verdicts: a probability distribution over the four levels, P(AI used),
    evidence for and against
  * opt-out audit: P(category should have been opted out) vs what was opted out
  * Compo checklist: P(rule violated) per rule, with every piece of evidence
  * an appendix with every automated signal and Stage-2 flag

verdicts.json (written by the reviewer – see references/classification.md):
{
  "categories": {
    "code": {
      "probabilities": {"NO_AI": 5, "AI_CONSULTED": 10, "AI_ASSISTED": 60, "AI_GENERATED": 25},
      "evidence_for":     [{"text": "...", "ref": "path:line | sha | url", "weight": "decisive|strong|moderate|weak"}],
      "evidence_against": [{"text": "...", "ref": "...", "weight": "..."}],
      "reasoning": "one or two sentences"
    },
    "overall": {...}, "idea": {...}, "artwork": {...}, "music_sound": {...}, "documentation": {...}
  },
  "generators":      {"graphics": {"tool": "", "p_most": 0, "evidence": []},
                      "audio":    {"tool": "sfxr", "p_most": 80, "evidence": ["all 12 SFX use sfxr names"]}},
  "premade_content": {"graphics": {"p": 5, "evidence": []}, "audio": {"p": 85, "evidence": ["..."]}},
  "solo":            {"p_violation": 5, "evidence": []},
  "optout_overrides": {"Humor": {"p_should": 40, "reason": "..."}},
  "summary": "two or three sentences",
  "rule_notes": ["..."]
}
A category without content uses {"level": "N/A", "evidence_for": [...]}.
Older shorthand ({"level": ..., "confidence": "high", "evidence": [...]}) is still accepted.

Usage: build_report.py --workdir DIR
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import fmt_time, human_int, load_json, pct  # noqa: E402

LEVELS = ["NO_AI", "AI_CONSULTED", "AI_ASSISTED", "AI_GENERATED"]
CATS = [("overall", "Overall"), ("idea", "Idea"), ("code", "Code"), ("artwork", "Artwork"),
        ("music_sound", "Music & sound"), ("documentation", "Documentation")]
AREA_OF = {"idea": "idea", "code": "code", "artwork": "artwork", "music_sound": "music_sound",
           "documentation": "documentation"}
CONF_MASS = {"high": 85, "medium": 65, "low": 45}


def words(p):
    """Verbal label for a probability in [0, 1]."""
    return ("almost certain" if p >= 0.95 else "very likely" if p >= 0.85 else "likely" if p >= 0.65 else
            "more likely than not" if p > 0.5 else "uncertain" if p >= 0.35 else "unlikely" if p >= 0.1 else
            "very unlikely")


def norm_category(c):
    """Return dict(level, probs{level: 0-1} or None, evidence_for, evidence_against, reasoning)."""
    c = dict(c or {})
    if c.get("level") == "N/A" or c.get("probabilities") == "N/A":
        return {"level": "N/A", "probs": None, "certainty": 1.0, "p_ai": 0.0,
                "evidence_for": _ev(c.get("evidence_for") or c.get("evidence")),
                "evidence_against": _ev(c.get("evidence_against")), "reasoning": c.get("reasoning", "")}
    probs = c.get("probabilities")
    if isinstance(probs, dict) and probs:
        tot = sum(float(probs.get(k, 0)) for k in LEVELS) or 1.0
        probs = {k: float(probs.get(k, 0)) / tot for k in LEVELS}
    elif c.get("level") in LEVELS:  # shorthand: spread the rest over neighbouring levels
        mass = CONF_MASS.get(c.get("confidence", "medium"), 65) / 100
        i = LEVELS.index(c["level"])
        nb = [j for j in (i - 1, i + 1) if 0 <= j < len(LEVELS)]
        probs = {k: 0.0 for k in LEVELS}
        probs[c["level"]] = mass
        for j in nb:
            probs[LEVELS[j]] += (1 - mass) * 0.8 / len(nb)
        for j in range(len(LEVELS)):
            if j != i and j not in nb:
                probs[LEVELS[j]] += (1 - mass) * 0.2 / max(1, len(LEVELS) - 1 - len(nb))
        tot = sum(probs.values())
        probs = {k: v / tot for k, v in probs.items()}
    else:
        return None
    level = c.get("level") if c.get("level") in LEVELS else max(probs, key=probs.get)
    return {"level": level, "probs": probs, "certainty": probs[level], "p_ai": 1 - probs["NO_AI"],
            "evidence_for": _ev(c.get("evidence_for") or c.get("evidence")),
            "evidence_against": _ev(c.get("evidence_against")), "reasoning": c.get("reasoning", "")}


def _ev(items):
    out = []
    for e in items or []:
        if isinstance(e, str):
            out.append({"text": e, "ref": "", "weight": ""})
        elif isinstance(e, dict):
            out.append({"text": e.get("text", ""), "ref": e.get("ref", ""), "weight": e.get("weight", "")})
    return out


def fmt_ev(e):
    w = f"[{e['weight']}] " if e.get("weight") else ""
    r = f" — `{e['ref']}`" if e.get("ref") else ""
    return f"{w}{e['text']}{r}"


def p_gen(cats, key):
    c = cats.get(key)
    if not c or c["level"] == "N/A" or not c["probs"]:
        return 0.0
    return c["probs"]["AI_GENERATED"]


def gen_most(v, key):
    g = (v.get("generators") or {}).get(key) or {}
    if isinstance(g, str):
        return (0.8 if g else 0.0), g, []
    p = g.get("p_most")
    if p is None:
        p = {"most": 80, "some": 20, "none": 0}.get(g.get("share", "none"), 0)
    return float(p) / 100, g.get("tool", ""), g.get("evidence", [])


def premade(v, key):
    x = (v.get("premade_content") or {}).get(key)
    if isinstance(x, bool):
        return (0.9 if x else 0.05), []
    if isinstance(x, dict):
        return float(x.get("p", 0)) / 100, x.get("evidence", [])
    return None, []


# ======================================================== opt-out audit
def optout_audit(game, v, cats):
    compo = (game.get("game") or {}).get("format") == "compo"
    rows = []
    for g in game.get("grades", []):
        name = g["name"] or ""
        n = name.lower()
        p, why, ev = 0.0, "", []
        if n == "overall":
            rows.append({"category": name, "p": None, "opted_out": bool(g.get("opted_out")),
                         "assessment": "cannot be opted out", "why": "", "evidence": [], "assumed": g.get("assumed")})
            continue
        if n in ("graphics", "audio"):
            key = "artwork" if n == "graphics" else "music_sound"
            pg = p_gen(cats, key)
            pm, tool, gev = gen_most(v, n)
            p = max(pg, pm)
            c = cats.get(key)
            if c and c["level"] == "N/A":
                why = f"The game has no {n} of its own – opting out is reasonable but not required."
            else:
                why = (f"P({'Artwork' if n == 'graphics' else 'Music & sound'} = AI_GENERATED) = {pg:.0%}"
                       + (f"; P(a non-AI generator ({tool}) did ≥90% of the work) = {pm:.0%}" if pm else "")
                       + ". The AI FAQ asks for an opt-out when a generator did most (~90%) of a category's work.")
                ev = (c["evidence_for"] if c else []) + [{"text": x, "ref": "", "weight": ""} for x in gev]
            pre_p, pre_ev = premade(v, n)
            if compo and pre_p and pre_p >= 0.3:
                why += (f" Separately, P(pre-made/third-party {n}) = {pre_p:.0%}: that is not allowed in the Compo "
                        f"at all and opting out does not fix it.")
                ev += [{"text": x, "ref": "", "weight": "pre-made"} for x in pre_ev]
        elif n == "mood":
            pa, pu = p_gen(cats, "artwork"), p_gen(cats, "music_sound")
            p = (pa + pu) / 2
            why = (f"Mood comes mostly from graphics and audio (P generated: {pa:.0%} / {pu:.0%}); the FAQ doesn't "
                   f"name Mood, so this is derived – treat as advisory.")
        elif n == "innovation":
            p = p_gen(cats, "idea")
            why = f"P(Idea = AI_GENERATED) = {p:.0%}."
            ev = (cats.get("idea") or {}).get("evidence_for", []) if p > 0.2 else []
        elif n in ("theme", "humor"):
            p = 0.5 * p_gen(cats, "idea")
            why = f"Half of P(Idea = AI_GENERATED) – arguable, and {name} is an optional opt-out anyway."
        elif n == "fun":
            p = 0.0
            why = "No rating category covers code; AI-generated code needs no opt-out under the current FAQ."
        ov = (v.get("optout_overrides") or {}).get(name)
        if ov:
            p = float(ov.get("p_should", 100 * p)) / 100
            why = ov.get("reason", why) + " (reviewer override)"
        actual = bool(g.get("opted_out"))
        if actual:
            assess = "opted out ✔" if p >= 0.5 else "opted out (not required)"
        elif p >= 0.85:
            assess = "MISSING OPT-OUT – very likely"
        elif p >= 0.6:
            assess = "missing opt-out – likely"
        elif p >= 0.4:
            assess = "possibly missing – review"
        elif p >= 0.15:
            assess = "probably fine"
        else:
            assess = "fine"
        rows.append({"category": name, "p": p, "opted_out": actual, "assessment": assess, "why": why,
                     "evidence": ev, "assumed": g.get("assumed")})
    return rows


# ======================================================== checklist (P(violation) per rule)
def noisy_or(ps):
    q = 1.0
    for p in ps:
        q *= (1 - max(0.0, min(1.0, p)))
    return 1 - q


def checklist(game, s1, s2, v, audit):
    g = game.get("game") or {}
    items = []
    flags = {f[1]: f for f in (s2 or {}).get("flags", [])}
    rel = ((s2 or {}).get("timeline_reliability") or {}).get("score", 0.5)

    def item(name, p, ev_for, ev_against=None, note="", basis=None):
        items.append({"rule": name, "p": p, "for": ev_for, "against": ev_against or [], "note": note,
                      "basis": basis or []})

    # source
    st, cert = s1.get("status"), (s1.get("certainty") or {})
    cv = cert.get("value", 0.5)
    p_src = {"SOURCE_FOUND": 1 - cv, "NO_SOURCE_AVAILABLE": cv, "FAILED_TO_RETRIEVE": 1 - cv}.get(st, 0.5)
    lines = [f"Stage 1: {st} – {s1.get('reason') or ''}"] + cert.get("basis", [])
    if st == "NO_SOURCE_AVAILABLE":
        item("Source code included", p_src, lines)
    elif st == "SOURCE_FOUND":
        item("Source code included", p_src, [], lines)
    else:
        item("Source code included", p_src, [], [], basis=lines,
             note="Waiting for the user to provide the source.")

    # solo
    authors = g.get("authors", [])
    ev, ps = [], []
    if len(authors) > 1:
        ev.append(f"{len(authors)} authors listed on the page: {', '.join(a.get('name') or str(a['id']) for a in authors)}")
        ps.append(0.95)
    if "MULTIPLE_IDENTITIES" in flags:
        ev.append(flags["MULTIPLE_IDENTITIES"][2])
        ps.append(0.35)
    solo = v.get("solo") or {}
    if "p_violation" in solo:
        ps = [float(solo["p_violation"]) / 100]
        ev += solo.get("evidence", [])
    item("Solo", noisy_or(ps), ev, [] if ev else ["1 author on the page; no second committer identity during the event"])

    # content made during the event
    ev, ps, against = [], [], []
    weights = {"PRE_EVENT_ASSETS": 0.9 * rel, "THIRD_PARTY_ASSETS": 0.6, "FREESOUND_FILES": 0.85,
               "OLD_EMBEDDED_TIMESTAMP": 0.45, "CREDITS_MENTION": 0.35}
    for code, w in weights.items():
        if code in flags:
            ev.append(f"[{w:.0%}] {code}: {flags[code][2]}")
            ps.append(w)
    rev = [premade(v, k) for k in ("graphics", "audio")]
    if any(p is not None for p, _ in rev):
        p_final = max(p or 0 for p, _ in rev)
        for (p, e), k in zip(rev, ("graphics", "audio")):
            if p is not None:
                ev.append(f"reviewer: P(pre-made {k}) = {p:.0%}" + (f" – {'; '.join(e)}" if e else ""))
        p_content = p_final
    else:
        p_content = noisy_or(ps)
    if s2 and not ps:
        against.append("no pre-event, third-party, Freesound-style or credited assets detected")
    item("All content made during the event (fonts excepted)", p_content, ev, against)

    # timeline
    ev, ps = [], []
    for code, w in (("HISTORY_AFTER_DEADLINE", 0.3), ("NO_PRE_DEADLINE_COMMIT", 0.3), ("DATE_INVERSION", 0.5),
                    ("UNIFORM_TIMESTAMPS", 0.1), ("UNRELIABLE_TIMES", 0.1)):
        if code in flags:
            ev.append(f"[{w:.0%}] {code}: {flags[code][2]}")
            ps.append(w)
    against = []
    if s2:
        S = s2["summary"]
        co = S["created_own"]
        against.append(f"{co['during'] + co['submission_hour']} of {S['files_own']} own files created during the "
                       f"event; {co['before']} before (base code/template)")
        if not ev:
            against.append("no timeline anomalies (dates in order, history starts before the deadline)")
    basis = [f"timeline reliability {rel:.0%}: " + "; ".join(((s2 or {}).get('timeline_reliability') or {}).get('reasons') or [])] if s2 else []
    item("Developed within the 48 hours (timeline)", noisy_or(ps), ev, against, basis=basis,
         note="Unverifiable timelines are not violations – they lower the certainty of everything else.")

    # rated version
    if s2:
        post = (s2.get("source") or {}).get("post_cutoff") or []
        feats = [p for p in post if p["kind"] == "content/feature"]
        bm = s2.get("build_match") or {}
        ev, ps, against = [], [], []
        if "PLAYED_BUILD_POST_DEADLINE" in flags:
            ev.append(flags["PLAYED_BUILD_POST_DEADLINE"][2])
            ps.append(0.85)
        if "PLAYED_BUILD_HAS_FIXES" in flags:
            ev.append(flags["PLAYED_BUILD_HAS_FIXES"][2])
            ps.append(0.15)
        if feats and not bm:
            ev.append(f"{len(feats)} post-deadline content/feature commit(s) and the played build was not checked: "
                      + "; ".join(f"{p['sha']} {p['message'][:60]}" for p in feats[:5]))
            ps.append(0.35)
        if bm.get("status") == "matches a pre-deadline commit":
            against.append(f"played build matches the compo version ({bm.get('best_hits')} identical files)")
            ps = [p * 0.3 for p in ps]
        if post and not feats:
            against.append(f"{len(post)} post-deadline commit(s), all bug-fix/port/docs-like")
        if not post:
            against.append("no commits after the deadline")
        item("Rated version = compo version (only bug fixes after the deadline)", noisy_or(ps), ev, against)

    # opt-outs (Mood is derived from Graphics/Audio and Theme/Humor are optional: excluded to avoid double counting)
    req = [r for r in audit if r["p"] is not None and not r["opted_out"]
           and r["category"].lower() not in ("theme", "humor", "mood")]
    ps = [r["p"] for r in req]
    ev = [f"{r['category']}: P(should opt out) {r['p']:.0%} – not opted out" for r in req if r["p"] >= 0.15]
    adv = [f"{r['category']}: P {r['p']:.0%} – advisory" for r in audit
           if r["category"].lower() in ("mood", "theme", "humor") and r["p"] and r["p"] >= 0.4 and not r["opted_out"]]
    item("Rating opt-outs match the content", noisy_or(ps) if audit else None, ev,
         [] if ev or not audit else ["every required opt-out is consistent with the verdicts"],
         basis=adv if audit else ["not assessed yet – needs verdicts.json"])
    return items


# ======================================================== main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()
    wd = Path(args.workdir).resolve()
    game = load_json(wd / "game.json") or {}
    s1 = load_json(wd / "stage1.json") or {}
    s2 = load_json(wd / "stage2.json")
    s3 = load_json(wd / "stage3_signals.json")
    v = load_json(wd / "verdicts.json") or {}
    problems = []
    cats = {}
    for key, label in CATS:
        raw = (v.get("categories") or {}).get(key)
        if raw is None:
            continue
        c = norm_category(raw)
        if c is None:
            problems.append(f"verdicts.json: '{key}' needs probabilities or a level")
            continue
        if c["level"] != "N/A" and not c["evidence_for"] and not c["evidence_against"]:
            problems.append(f"'{label}' has no evidence listed")
        cats[key] = c
    if cats:
        content = [cats[k]["level"] for k in ("idea", "code", "artwork", "music_sound") if k in cats]
        top = max((LEVELS.index(x) for x in content if x in LEVELS), default=-1)
        ov = LEVELS.index(cats["overall"]["level"]) if "overall" in cats and cats["overall"]["level"] in LEVELS else -1
        if top >= 0 and ov < min(top, 2):
            problems.append("Overall is lower than the strongest content category allows – see the Overall rule.")

    g, e = game.get("game") or {}, game.get("event") or {}
    L = [f"# Ludum Dare Compo check: {g.get('name') or '?'}",
         f"{g.get('url', '')}  ",
         f"By {', '.join(a.get('name') or str(a.get('id')) for a in g.get('authors', []))} · {e.get('name') or ''} "
         f"(theme: {e.get('theme') or '?'}) · format: **{g.get('format') or '?'}**  ",
         f"Compo window: {fmt_time(e.get('event_start'))} → {fmt_time(e.get('compo_end'))} "
         f"(Submission Hour until {fmt_time(e.get('submission_end'))})", ""]
    if g.get("format") and g.get("format") != "compo":
        L += [f"**Note:** this entry is in the {g['format']} format – Compo-only rules (solo, everything made in 48h, "
              f"source required) don't apply; opt-out rules still do.", ""]
    if v.get("summary"):
        L += [f"> {v['summary']}", ""]
    L.append("_Probabilities are calibrated judgments from the evidence listed, not measurements._")
    L.append("")

    # ---- 1 source
    L.append("## 1 · Source")
    st = s1.get("status") or "not run"
    cert = s1.get("certainty") or {}
    L.append(f"**{st.replace('_', ' ')}**" + (f" – {int(100 * cert['value'])}% that {cert['statement']} "
                                              f"({words(cert['value'])})" if cert else ""))
    L.append(f"- Reason: {s1.get('reason') or ''}")
    for b in cert.get("basis", []):
        L.append(f"- Basis: {b}")
    for a in s1.get("manual_actions", []):
        L.append(f"- Action: {a}")
    pb = s1.get("played_build") or {}
    if pb.get("status") == "ok":
        L.append(f"- Played build retrieved ({pb.get('kind')}).")
    L.append("")

    # ---- 2 static
    if s2:
        S = s2["summary"]
        src = s2["source"]
        L.append("## 2 · Static analysis")
        if src.get("mode") == "git":
            c = src["cutoff"]
            L.append(f"Analysed commit `{c['sha'][:10]}` ({fmt_time(c['time'])}) – {c['why']}.")
        tr = s2.get("timeline_reliability") or {}
        if tr:
            L.append(f"Timeline reliability: **{int(100 * tr['score'])}%** – " + "; ".join(tr.get("reasons", [])) + ".")
        co, mo = S["created_own"], S["modified_own"]
        w = S["loc_written_during"]
        L += ["", "| | Count |", "|---|---|",
              f"| Files (whole project / own) | {S['files_total']} / {S['files_own']} |",
              f"| Own files created during the Compo | {co['during']} ({pct(co['during'], S['files_own'])}) |",
              f"| Own files created in Submission Hour | {co['submission_hour']} |",
              f"| Own files created before the event | {co['before']} |",
              f"| Own files last edited before Compo end | {mo['during'] + mo['before']} ({pct(mo['during'] + mo['before'], S['files_own'])}) |",
              f"| LoC written during the event (new files) | {human_int(w['code'])} |",
              f"| Artwork files | {S['artwork']['count']} |",
              f"| Raw/project files | {S['raw_files']['count']} |",
              f"| Audio files | {S['audio']['count']} |", "", "Full breakdown by type: `stage2.md`.", ""]

    # ---- 3 AI
    L.append("## 3 · AI usage")
    if cats:
        L += ["| Category | Most likely | Certainty | P(any AI use) | Distribution |", "|---|---|---|---|---|"]
        for key, label in CATS:
            c = cats.get(key)
            if not c:
                continue
            if c["level"] == "N/A":
                L.append(f"| {label} | N/A | – | – | no content of this kind |")
                continue
            dist = " · ".join(f"{k.replace('AI_', '')} {c['probs'][k]:.0%}" for k in LEVELS)
            L.append(f"| {label} | **{c['level']}** | {c['certainty']:.0%} ({words(c['certainty'])}) | "
                     f"{c['p_ai']:.0%} | {dist} |")
        L.append("")
        for key, label in CATS:
            c = cats.get(key)
            if not c:
                continue
            L.append(f"**{label} – evidence**" + (f" · {c['reasoning']}" if c.get("reasoning") else ""))
            for x in c["evidence_for"]:
                L.append(f"- for: {fmt_ev(x)}")
            for x in c["evidence_against"]:
                L.append(f"- against: {fmt_ev(x)}")
            if not c["evidence_for"] and not c["evidence_against"]:
                L.append("- (none listed)")
            L.append("")
    else:
        L.append("_verdicts.json not written yet – automatic priors from the scanner:_")
        if s3:
            for key, label in CATS[1:]:
                ln = s3["areas"].get(key, {}).get("lean") or {}
                if ln.get("probabilities"):
                    L.append(f"- {label}: " + " · ".join(f"{k} {p}%" for k, p in ln["probabilities"].items()))
                elif ln:
                    L.append(f"- {label}: {ln.get('suggestion')}")
        L.append("")
    if s3 and s3.get("runtime_ai_dependencies"):
        L.append(f"The game itself calls generative-AI services at runtime: {', '.join(s3['runtime_ai_dependencies'])}.\n")

    # ---- 4 opt-outs
    L.append("## 4 · Rating-category opt-outs (should vs actual)")
    audit = optout_audit(game, v, cats) if cats else []
    if audit:
        L += ["| Category | P(should opt out) | Opted out? | Assessment |", "|---|---|---|---|"]
        for r in audit:
            pv = "–" if r["p"] is None else f"{r['p']:.0%}"
            L.append(f"| {r['category']}{'*' if r['assumed'] else ''} | {pv} | {'yes' if r['opted_out'] else 'no'} | "
                     f"{r['assessment']} |")
        L.append("")
        for r in audit:
            if r["why"]:
                L.append(f"- **{r['category']}**: {r['why']}")
                for x in r["evidence"]:
                    L.append(f"    - {fmt_ev(x)}")
        if any(r["assumed"] for r in audit):
            L.append("\n*Category list assumed (event data unavailable).")
        L.append("\nRule basis: LD AI FAQ – opt out of a category when a content generator (AI or not) did most "
                 "(~90%) of its work. Pre-made content is not allowed in the Compo at all (fonts excepted).")
    else:
        opted = [x["name"] for x in game.get("grades", []) if x.get("opted_out")]
        L.append(f"Actually opted out of: {', '.join(opted) or 'nothing'}. (Needs verdicts.json for the comparison.)")
    L.append("")

    # ---- 5 checklist
    L.append("## 5 · Compo rules – probability each rule was broken")
    items = checklist(game, s1, s2, v, audit)
    for it in items:
        p = it["p"]
        if p is None:
            L.append(f"- ⏳ **{it['rule']}** – not assessed yet")
        else:
            icon = "❌" if p >= 0.6 else "⚠️" if p >= 0.25 else "✅"
            L.append(f"- {icon} **{it['rule']}** – P(violated) **{p:.0%}** ({words(p)})" +
                     (f" · {it['note']}" if it["note"] else ""))
        for x in it["for"]:
            L.append(f"    - for: {x}")
        for x in it["against"]:
            L.append(f"    - against: {x}")
        for x in it.get("basis", []):
            L.append(f"    - basis: {x}")
    worst = max((it["p"] for it in items if it["p"] is not None), default=0)
    L.append("")
    L.append(("**Preliminary outcome** (no reviewer verdicts yet)" if not cats else "**Outcome:**") +
             f" highest probability of a rule problem = {worst:.0%} ({words(worst)}). " +
             ("Rule problem(s) likely – see ❌ items." if worst >= 0.6 else
              "Needs human review – see ⚠️ items." if worst >= 0.25 else "No rule problem found."))
    for n in v.get("rule_notes", []):
        L.append(f"- {n}")
    if problems:
        L += ["", "### Report consistency warnings"] + [f"- {p}" for p in problems]

    # ---- appendix: all automated evidence
    L += ["", "## Appendix A · Every automated signal (Stage 3)"]
    if s3:
        names = {"code": "Code", "artwork": "Artwork", "music_sound": "Music & sound",
                 "documentation": "Documentation", "idea": "Idea / page", "process": "Process"}
        for key, label in names.items():
            a = s3["areas"].get(key) or {}
            sigs = sorted(a.get("signals", []), key=lambda s: (-s["strength"], s["polarity"]))
            L.append(f"**{label}** ({len(sigs)} signal(s))")
            for s in sigs:
                L.append(f"- [{s['polarity']} · strength {s['strength']}/4] {s['title']} – {s['detail']}")
                for ex in s.get("examples", []):
                    L.append(f"    - `{str(ex)[:200]}`")
                more = s.get("examples_total", 0) - len(s.get("examples", []))
                if more > 0:
                    L.append(f"    - … and {more} more")
            L.append("")
    else:
        L.append("_stage3_signals.json missing._")
    L.append("## Appendix B · Stage 2 flags")
    for sev, code, msg in (s2 or {}).get("flags", []):
        L.append(f"- **{sev}** `{code}` – {msg}")
    for n in ((s2 or {}).get("source") or {}).get("notes", []):
        L.append(f"- note: {n}")
    L += ["", "_AI-detection signals are probabilistic. Use this report as the basis for a conversation or a "
              "report to the organisers, not as proof on its own._"]
    md = "\n".join(L) + "\n"
    (wd / "report.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"Wrote {wd / 'report.md'}")


if __name__ == "__main__":
    main()
