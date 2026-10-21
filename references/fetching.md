# Getting data when the sandbox can't reach a site

The scripts fetch everything themselves when the network allows. In sandboxes with an egress allowlist (e.g. the
claude.ai workspace) `api.ldjam.com`, `itch.io`, GitLab and file hosts are often blocked from the shell, while
public GitHub repositories can still be cloned. This file says what to do in each case.

## Hosts the scripts use
| Stage | Hosts |
|---|---|
| 0 – page | `api.ldjam.com` |
| 1 – git | `github.com` (+ `gist.github.com`), `gitlab.com`, `codeberg.org`, `bitbucket.org`, `git.sr.ht` |
| 1 – files | `drive.google.com`, `drive.usercontent.google.com`, `www.dropbox.com`, `dl.dropboxusercontent.com`, direct archive hosts |
| 1 – itch.io | `itch.io`, `<user>.itch.io`, `html-classic.itch.zone` / `html.itch.zone` (web builds), itch's download CDN |

Tell the user once, briefly, that allowing these domains in their workspace settings (claude.ai: Settings →
Capabilities, or the org admin) lets the whole check run automatically. Then continue with the fallbacks below –
don't stop.

## Stage 0: ldjam API via the web-fetch tool
`ldjam_page.py` exits with code 3 and writes `fetch_plan.json` listing `{save_as, url, purpose, required}`.
For each entry (required ones first; the optional ones – comments, the author's posts and other games – are worth
fetching, they feed the evidence):
1. Fetch the URL with the web-fetch tool and this prompt:
   > Return the complete raw JSON response exactly as received – no commentary, no summarising, no reformatting.
   > Do not shorten the "body" or "meta" fields.
2. Write the JSON to `<workdir>/<save_as>` with the file-writing tool.
3. If the tool returns prose instead of JSON, retry once asking for:
   > Output JSON only: {"node":[...]} where each node has id, parent, author, type, subtype, subsubtype, name,
   > slug, path, published, created, modified, body (verbatim) and meta (every key and value verbatim).
4. Rerun the same `ldjam_page.py` command. It uses the saved files and asks for the next round (comment author
   names, post contents) if needed.

If the description (`body`) came back summarised rather than verbatim, note that the description-style signals are
unreliable for this run.

## Stage 1: itch.io pages
When `get_source.py` reports "could not inspect" for an itch page:
1. Fetch the page with the web-fetch tool:
   > List every downloadable file on this itch.io page exactly as named (file name, size, platform icons),
   > say whether the game can be played in the browser, give the exact "Updated" and "Published" dates from the
   > "More information" section, and list every link in the description (full URLs). Quote any sentence that
   > mentions source code, GitHub, post-jam or updated versions.
2. Write `<workdir>/itch.json`:
   ```json
   {"page": "https://user.itch.io/game",
    "uploads": [{"name": "Game-Windows.zip", "size": "25 MB", "platforms": ["Windows"]},
                {"name": "Game-Source.zip", "size": "3 MB", "platforms": []}],
    "web_build": true,
    "updated": "2026-10-20T09:15:00Z", "published": "2026-10-18T22:41:00Z",
    "description_links": ["https://github.com/user/game"]}
   ```
3. Rerun `get_source.py --workdir <wd> --itch-manual <wd>/itch.json`. Git links found there are cloned; a
   source-looking upload becomes FAILED_TO_RETRIEVE with an exact "download X" request; no source anywhere
   becomes NO_SOURCE_AVAILABLE.

Upload names from a summarised page can be incomplete – the stage-1 certainty already accounts for that.

## Asking the user for files
Be specific: name the exact upload ("Game-Source.zip" on https://user.itch.io/game) or repository, and say what
it is for. When files arrive (attachments appear in the conversation's uploads folder):
- source: `get_source.py --workdir <wd> --local <path>` (zip/7z/tar/folder; a zip that contains `.git` is analysed
  with its history);
- played build (optional): `get_source.py --workdir <wd> --local <source> --build-local <build zip or folder>`.

Ask for the played build only when it matters: commits exist after the deadline, or the itch page was updated
after the deadline.

## GitHub extras (optional)
`api.github.com` may be blocked or rate-limited; nothing depends on it. When the compo version is ambiguous, the
releases page can help: web-fetch `https://github.com/<owner>/<repo>/releases` and `/tags` and ask for each
release/tag name with its date, then rerun `analyze_static.py --ref <tag>` or `--cutoff <sha>` if one clearly
marks the compo version.

## itch.io downloads with an API key
If the user has an itch.io API key (itch.io → Settings → API keys), `ITCH_API_KEY=<key> get_source.py …` uses
itch's API to download uploads, which avoids the browser download flow. Never ask for passwords.
