#!/usr/bin/env python3
"""
sort_tables.py
Script to sort GitHub repository markdown tables in README.md or category markdown files
by star count using the GitHub API, and re-rank rows.

Features:
- Reads GitHub token from .env file (or environment variables) for authenticated API requests.
- Falls back to unauthenticated calls only if .env and environment tokens are not found.
- Caches fetched star data locally in .star_cache.json with a 24-hour TTL.
- Repositories fetched within the last 24 hours use local cache instead of making new API calls.
- Gracefully falls back to cached data if rate-limited or offline.
- Supports --refresh / --no-cache to bypass cache when desired.
- Defaults to README.md if no file argument is passed.

Usage:
    python sort_tables.py                     # Sorts table in README.md (cached < 24h)
    python sort_tables.py README.md           # Sorts table in README.md
    python sort_tables.py categories/cli-coding-assistants.md  # Sorts specific category file
    python sort_tables.py categories/*.md     # Sorts multiple category files
    python sort_tables.py --refresh           # Forces bypass of cache
"""

import sys
import os
import re
import json
import time
import urllib.request
import urllib.error

# Cache configuration
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".star_cache.json")
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours

STAR_CACHE = {}
CACHE_DIRTY = False
GITHUB_TOKEN = None


def load_github_token() -> str:
    """
    Search for a GitHub token in .env file (in current dir or script dir)
    or in environment variables. Returns the token if found, else empty string.
    """
    # 1. Check existing environment variables first
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "github_token", "gh_token"):
        if os.environ.get(key):
            return os.environ[key].strip()

    # 2. Look for .env file
    candidate_paths = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    ]

    # Deduplicate while preserving order
    seen = set()
    for env_path in candidate_paths:
        norm_path = os.path.normpath(env_path)
        if norm_path in seen:
            continue
        seen.add(norm_path)

        if os.path.isfile(norm_path):
            try:
                with open(norm_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if k.upper() in ("GITHUB_TOKEN", "GH_TOKEN"):
                                if v:
                                    return v
            except Exception as e:
                print(f"[Warning] Failed reading .env from {norm_path}: {e}")

    return ""


def load_cache() -> dict:
    """Load local star cache from disk."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Warning] Could not read cache file {CACHE_FILE}: {e}")
    return {}


def save_cache():
    """Save star cache to disk."""
    global CACHE_DIRTY
    if not CACHE_DIRTY:
        return
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(STAR_CACHE, f, indent=2)
        CACHE_DIRTY = False
    except Exception as e:
        print(f"[Warning] Could not write cache file {CACHE_FILE}: {e}")


def get_repo_stars(repo_path: str, force_refresh: bool = False) -> tuple[int, str]:
    """
    Fetch GitHub stargazers count for a repository (owner/repo).
    Returns (stars, source_info).
    """
    global CACHE_DIRTY
    now = time.time()

    # Check local cache first (valid for 24 hours)
    if not force_refresh and repo_path in STAR_CACHE:
        entry = STAR_CACHE[repo_path]
        if isinstance(entry, dict):
            cached_stars = entry.get("stars", -1)
            cached_time = entry.get("timestamp", 0)
            age_seconds = now - cached_time
            if cached_stars >= 0 and age_seconds < CACHE_TTL_SECONDS:
                hours_ago = age_seconds / 3600.0
                return cached_stars, f"cache ({hours_ago:.1f}h ago)"
        elif isinstance(entry, int) and entry >= 0:
            return entry, "cache"

    url = f"https://api.github.com/repos/{repo_path}"
    headers = {
        "User-Agent": "Top-Viral-Repositories-Sorter/1.0",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            stars = data.get("stargazers_count", 0)
            STAR_CACHE[repo_path] = {
                "stars": stars,
                "timestamp": now,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            }
            CACHE_DIRTY = True
            source = "API (authenticated)" if GITHUB_TOKEN else "API (unauthenticated)"
            return stars, source
    except urllib.error.HTTPError as e:
        auth_status = "authenticated" if GITHUB_TOKEN else "unauthenticated"
        print(f"  [Warning] HTTP Error {e.code} ({auth_status}) for {repo_path}: {e.reason}")
    except Exception as e:
        print(f"  [Warning] Failed to fetch stars for {repo_path}: {e}")

    # Fallback to stale cached stars if API call fails
    if repo_path in STAR_CACHE and isinstance(STAR_CACHE[repo_path], dict):
        stale_stars = STAR_CACHE[repo_path].get("stars", -1)
        if stale_stars >= 0:
            hours_ago = (now - STAR_CACHE[repo_path].get("timestamp", now)) / 3600.0
            return stale_stars, f"stale cache ({hours_ago:.1f}h ago)"

    return -1, "error"


def extract_repo_path(row_str: str) -> str:
    """Extract owner/repo string from a markdown table row."""
    # Try finding shields.io/github/stars/owner/repo
    badge_match = re.search(r"github/stars/([^/?#\s]+)/([^/?#\s&]+)", row_str)
    if badge_match:
        return f"{badge_match.group(1)}/{badge_match.group(2)}"

    # Try finding github.com/owner/repo
    link_match = re.search(r"github\.com/([^/?#\s]+)/([^/?#\s)]+)", row_str)
    if link_match:
        owner = link_match.group(1)
        repo = link_match.group(2).rstrip("/")
        if repo not in ("stargazers", "network", "issues", "pulls"):
            return f"{owner}/{repo}"

    return ""


def format_rank(rank_num: int, use_medals: bool) -> str:
    """Format rank with optional medal emojis for top 3."""
    if use_medals:
        if rank_num == 1:
            return "🥇 1"
        elif rank_num == 2:
            return "🥈 2"
        elif rank_num == 3:
            return "🥉 3"
    return str(rank_num)


def sort_markdown_table(file_path: str, force_refresh: bool = False):
    """Parses, sorts by stars, and updates repository tables in a markdown file."""
    if not os.path.exists(file_path):
        print(f"[Error] File not found: {file_path}")
        return

    print(f"\nProcessing {file_path}...")
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    new_lines = []
    i = 0
    modified = False

    while i < len(lines):
        line = lines[i]
        # Check for table header containing Rank & Repository
        if (
            line.startswith("|")
            and ("Rank" in line or "🏅" in line)
            and ("Repository" in line or "📦" in line)
        ):
            header_line = line
            separator_line = (
                lines[i + 1]
                if i + 1 < len(lines) and lines[i + 1].startswith("|")
                else None
            )

            if separator_line and ":---" in separator_line:
                new_lines.append(header_line)
                new_lines.append(separator_line)
                i += 2

                rows = []
                use_medals = False

                # Collect all consecutive table rows
                while (
                    i < len(lines)
                    and lines[i].startswith("|")
                    and lines[i].strip() != ""
                ):
                    row_line = lines[i]
                    if any(medal in row_line for medal in ("🥇", "🥈", "🥉")):
                        use_medals = True

                    parts = [p.strip() for p in row_line.split("|")]
                    # parts: ['', 'Rank', 'Repo', 'Category/Focus', 'Description', '']
                    if len(parts) >= 5:
                        repo_path = extract_repo_path(row_line)
                        rows.append(
                            {
                                "original": row_line,
                                "parts": parts,
                                "repo_path": repo_path,
                            }
                        )
                    else:
                        rows.append(
                            {"original": row_line, "parts": None, "repo_path": ""}
                        )
                    i += 1

                # Fetch stars and sort rows
                for r in rows:
                    if r["repo_path"]:
                        stars, source = get_repo_stars(
                            r["repo_path"], force_refresh=force_refresh
                        )
                        r["stars"] = stars
                        if stars >= 0:
                            print(f"  {r['repo_path']:<42} -> {stars:,} stars [{source}]")
                        else:
                            print(f"  {r['repo_path']:<42} -> [error/unknown]")
                    else:
                        r["stars"] = -1

                # If all stars failed (e.g. offline/rate limit), keep original order but re-number
                valid_star_counts = [r["stars"] for r in rows if r["stars"] >= 0]
                if valid_star_counts:
                    rows.sort(key=lambda x: x["stars"], reverse=True)

                # Reconstruct table rows with updated ranks
                for idx, r in enumerate(rows, start=1):
                    if r["parts"] and len(r["parts"]) >= 5:
                        rank_str = format_rank(idx, use_medals)
                        # parts[0] is empty, parts[1] is rank, parts[2..-2] are content cols, parts[-1] is empty
                        col_cells = r["parts"][2:-1]
                        formatted_row = (
                            f"| {rank_str} | " + " | ".join(col_cells) + " |"
                        )
                        new_lines.append(formatted_row)
                    else:
                        new_lines.append(r["original"])

                modified = True
                continue

        new_lines.append(line)
        i += 1

    if modified:
        output_text = "\n".join(new_lines) + ("\n" if content.endswith("\n") else "")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(output_text)
        print(f"✅ Successfully sorted and updated {file_path}")
    else:
        print(f"ℹ️ No repository tables found in {file_path}")


def main():
    global STAR_CACHE, GITHUB_TOKEN
    STAR_CACHE = load_cache()
    GITHUB_TOKEN = load_github_token()

    if GITHUB_TOKEN:
        masked = GITHUB_TOKEN[:8] + "..." + GITHUB_TOKEN[-4:] if len(GITHUB_TOKEN) > 12 else "***"
        print(f"🔑 Using authenticated GitHub API (token: {masked})")
    else:
        print("ℹ️ No .env or GITHUB_TOKEN found; falling back to unauthenticated API calls")

    args = sys.argv[1:]
    force_refresh = False

    filtered_args = []
    for arg in args:
        if arg in ("--refresh", "--no-cache", "-r"):
            force_refresh = True
        else:
            filtered_args.append(arg)

    target_files = filtered_args
    if not target_files:
        default_file = "README.md"
        if os.path.exists(default_file):
            target_files = [default_file]
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            readme_in_script_dir = os.path.join(script_dir, "README.md")
            if os.path.exists(readme_in_script_dir):
                target_files = [readme_in_script_dir]
            else:
                target_files = ["README.md"]

    try:
        for file_arg in target_files:
            sort_markdown_table(file_arg, force_refresh=force_refresh)
    finally:
        save_cache()


if __name__ == "__main__":
    main()
