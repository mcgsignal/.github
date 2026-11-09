#!/usr/bin/env python3
"""The check for a clean repository of MCG Signal.

    python tools/check.py local <repository folder> [--title "<pull request title>"]
    python tools/check.py live [organization]

"local" reads a working copy before a pull request. "live" reads what the public sees:
it makes a fresh copy of each public repository, does the same checks, and then reads the
settings and the rendered pages. The exit code is 1 when a check fails.

Python standard library only. "live" also needs the GitHub CLI (gh) and git.
"""
import html
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import urllib.request

ORG = "mcgsignal"
SITE = "https://github.com"
VERBATIM = {"LICENSE", "CODE_OF_CONDUCT.md"}  # standard texts, not ours to change
BASE_TYPES = {"docs", "repo"}
TITLE_MAX = 72
BRITISH = ["licence", "licences", "organisation", "colour", "behaviour", "recognise", "favour", "centre", "analyse", "full stop"]
SLOP = ("delve leverage seamless robust crucial pivotal comprehensive furthermore moreover utilize "
        "showcase tapestry elevate unlock empower streamline holistic").split()
PRIVATE_NET = re.compile(r"\b(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b")
ATTRIBUTION = re.compile(r"co-authored-by|generated with|claude|anthropic|chatgpt|copilot", re.I)
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
LOGIN_OK = ("/issues/new", "/security/advisories/new")  # these pages ask for a login by design
UA = {"User-Agent": "Mozilla/5.0 (mcgsignal-check)"}
_fetched = {}


def run(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def fetch(url):
    """Open a URL as a visitor: (status, final URL, content type, body)."""
    if url not in _fetched:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                kind = r.headers.get("Content-Type", "").split(";")[0].strip()
                _fetched[url] = (r.status, r.geturl(), kind, r.read(400_000).decode("utf-8", "replace"))
        except Exception as error:  # a link that does not open is a finding, not a crash
            _fetched[url] = (getattr(error, "code", 0), url, "", "")
    return _fetched[url]


def slug(heading):
    return re.sub(r"[^a-z0-9 -]", "", heading.lower()).strip().replace(" ", "-")


def repo_types(root):
    """The title types of a repository: the shared ones and those its README lists."""
    readme = root / "README.md"
    listed = re.findall(r"^\| `([a-z]+)` \|", readme.read_text(encoding="utf-8"), flags=re.M) if readme.exists() else []
    return BASE_TYPES | set(listed)


def check_title(title, types):
    out = []
    match = re.match(r"^([a-z]+): (.+)$", title)
    if not match:
        return [f'title "{title}": the form is "<type>: <what changed>"']
    kind, what = match.groups()
    if kind not in types:
        out.append(f'title "{title}": the type "{kind}" is not one of {sorted(types)}')
    if not what[0].islower():
        out.append(f'title "{title}": use lower case after the colon')
    if what.endswith("."):
        out.append(f'title "{title}": no period at the end')
    if len(title) > TITLE_MAX:
        out.append(f'title "{title}": {len(title)} characters, the limit is {TITLE_MAX}')
    return out


def check_text(root):
    """The files of a working copy: characters, words, links, forms."""
    out = []
    files = [root / name for name in run("git", "ls-files", cwd=root).stdout.split("\n") if name]
    if not (root / "README.md").exists():
        out.append("no README.md in the root: the repository page opens on another file")
    for path in files:
        rel = path.relative_to(root).as_posix()
        if path.name in VERBATIM or path.suffix not in (".md", ".yml", ".yaml"):
            continue
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.split("\n"), 1):
            where = f"{rel}:{number}"
            if re.search(r"[^\x00-\x7F]", line):
                out.append(f"{where}: a character that is not on a keyboard")
            if PRIVATE_NET.search(line):
                out.append(f"{where}: a private network address")
            for word in BRITISH:
                if re.search(rf"\b{word}\b", line, flags=re.I):
                    out.append(f'{where}: British spelling "{word}"')
            for word in SLOP:
                if re.search(rf"\b{word}\b", line, flags=re.I):
                    out.append(f'{where}: filler word "{word}"')
        if path.suffix == ".md":
            out += check_links(root, path, rel, text)
        elif "ISSUE_TEMPLATE" in rel and path.name != "config.yml":
            for key in ("name:", "description:", "body:"):
                if not re.search(rf"^{key}", text, flags=re.M):
                    out.append(f"{rel}: the issue form has no top-level {key[:-1]}")
    return out


def check_links(root, path, rel, text):
    out = []
    for label, target in LINK.findall(text):
        if target.startswith("mailto:"):
            continue
        if target.startswith("http"):
            status, final, kind, _ = fetch(target.split("#")[0])
            if status != 200:
                out.append(f"{rel}: the link {target} does not open (status {status})")
            elif "/login" in final and not any(part in target for part in LOGIN_OK):
                out.append(f"{rel}: the link {target} asks for a login")
            elif kind and kind != "text/html" and "(" not in label:
                short = {"application/json": "JSON", "text/plain": "text"}.get(kind, kind)
                out.append(f'{rel}: the link "{label}" opens a {short} file, not a page: '
                           f'tell the file type in the link text, for example "{label} ({short})"')
            continue
        file_part, _, anchor = target.partition("#")
        dest = (path.parent / file_part).resolve() if file_part else path
        if not dest.exists():
            out.append(f"{rel}: the link {target} goes to a file that is not there")
        elif anchor and dest.suffix == ".md":
            heads = [slug(h) for h in re.findall(r"^#+ (.+)$", dest.read_text(encoding="utf-8"), flags=re.M)]
            if anchor not in heads:
                out.append(f"{rel}: the link {target} goes to a heading that is not there")
    return out


def check_history(root, revisions, types):
    """Each commit in the range: title form, no tool attribution, the no-reply address."""
    out = []
    log = run("git", "log", "--format=%h%x1f%ae%x1f%s%x1f%b%x1e", revisions, cwd=root).stdout
    for entry in filter(None, (e.strip() for e in log.split("\x1e"))):
        short, email, subject, body = (entry.split("\x1f") + ["", "", ""])[:4]
        subject = re.sub(r" \(#\d+\)$", "", subject)
        out += [f"commit {short}: {finding}" for finding in check_title(subject, types)]
        if not email.endswith("@users.noreply.github.com"):
            out.append(f"commit {short}: the author address is not a GitHub no-reply address")
        if ATTRIBUTION.search(subject + "\n" + body):
            out.append(f"commit {short}: the message names a tool as an author")
    return out


def gh_json(path):
    result = run("gh", "api", path)
    return json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else None


def check_settings(org, name):
    out = []
    repo = gh_json(f"repos/{org}/{name}") or {}
    wanted = {"allow_squash_merge": True, "allow_merge_commit": False, "allow_rebase_merge": False,
              "squash_merge_commit_title": "PR_TITLE", "squash_merge_commit_message": "PR_BODY",
              "delete_branch_on_merge": True, "has_wiki": False, "has_projects": False}
    for key, value in wanted.items():
        if repo.get(key) != value:
            out.append(f"setting {key} is {repo.get(key)!r}, not {value!r}")
    if not repo.get("description"):
        out.append("no description")
    rules = {}
    for item in gh_json(f"repos/{org}/{name}/rulesets") or []:
        full = gh_json(f"repos/{org}/{name}/rulesets/{item['id']}") or {}
        if full.get("enforcement") == "active" and not full.get("bypass_actors"):
            rules.update({rule["type"]: rule for rule in full.get("rules", [])})
    for kind in ("deletion", "non_fast_forward", "required_linear_history", "pull_request"):
        if kind not in rules:
            out.append(f"main has no active rule {kind} without a bypass")
    if not (gh_json(f"repos/{org}/{name}/private-vulnerability-reporting") or {}).get("enabled"):
        out.append("private vulnerability reporting is off")
    return out


def rendered(url):
    status, _, _, body = fetch(url)
    match = re.search(r"<article[^>]*markdown-body.*?</article>", body, flags=re.S)
    article = match.group(0) if match else ""
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", article))).strip()
    return status, article, text


def check_pages(org, name, root):
    """What a visitor sees: the repository page and each document, as GitHub shows them."""
    out = []
    status, article, text = rendered(f"{SITE}/{org}/{name}")
    readme = root / "README.md"
    if status != 200:
        out.append(f"the repository page does not open (status {status})")
    elif readme.exists():
        title = re.search(r"^# (.+)$", readme.read_text(encoding="utf-8"), flags=re.M)
        if title and not text.startswith(title.group(1)):
            out.append(f'the repository page opens on "{text[:40]}", not on its README')
    for path in root.rglob("*.md"):
        if ".git" in path.parts or path == readme:
            continue
        rel = path.relative_to(root).as_posix()
        status, article, text = rendered(f"{SITE}/{org}/{name}/blob/main/{rel}")
        if status != 200 or not text:
            out.append(f"{rel}: the page does not show its text (status {status})")
        elif re.search(r"\*\*\w|\| ---|\]\(", text):
            out.append(f"{rel}: the page shows raw formatting")
    return out


def report(name, findings):
    print(f"{name}: {'clean' if not findings else str(len(findings)) + ' finding(s)'}")
    for finding in findings:
        print(f"  - {finding}")
    return len(findings)


def main(argv):
    if len(argv) >= 2 and argv[0] == "local":
        root = pathlib.Path(argv[1]).resolve()
        types = repo_types(root)
        findings = check_text(root) + check_history(root, "origin/main..HEAD", types)
        if "--title" in argv:
            findings += check_title(argv[argv.index("--title") + 1], types)
        return 1 if report(root.name, findings) else 0
    if argv and argv[0] == "live":
        org = argv[1] if len(argv) > 1 else ORG
        total = 0
        status, _, text = rendered(f"{SITE}/{org}")
        total += report(f"{org} (organization page)", [] if status == 200 and text else ["the profile does not show"])
        for repo in gh_json(f"orgs/{org}/repos?type=public&per_page=100") or []:
            with tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp) / "copy"
                run("git", "clone", "--quiet", "--depth", "200", repo["clone_url"], str(root))
                types = repo_types(root)
                findings = (check_text(root) + check_history(root, "HEAD", types)
                            + check_settings(org, repo["name"]) + check_pages(org, repo["name"], root))
                total += report(f"{org}/{repo['name']}", findings)
        return 1 if total else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
