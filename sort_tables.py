#!/usr/bin/env python3
"""
sort_tables.py
Script to sort GitHub repository markdown tables in README.md or category markdown files
by star count using the GitHub API, and re-rank rows.

Usage:
    python sort_tables.py                     # Sorts table in README.md
    python sort_tables.py README.md           # Sorts table in README.md
    python sort_tables.py categories/cli-coding-assistants.md  # Sorts specific category file
    python sort_tables.py categories/*.md     # Sorts multiple category files
"""

import sys
import os
import re
import json
import urllib.request
import urllib.error

# Cache star counts across tables/files to avoid duplicate API calls
STAR_CACHE = {}

def get_repo_stars(repo_path: str) -> int:
    """Fetch GitHub stargazers count for a repository (owner/repo)."""
    if repo_path in STAR_CACHE:
        return STAR_CACHE[repo_path]
    
    url = f"https://api.github.com/repos/{repo_path}"
    headers = {
        "User-Agent": "Top-Viral-Repositories-Sorter/1.0",
        "Accept": "application/vnd.github.v3+json"
    }
    
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
        
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            stars = data.get("stargazers_count", 0)
            STAR_CACHE[repo_path] = stars
            return stars
    except urllib.error.HTTPError as e:
        print(f"  [Warning] HTTP Error {e.code} for {repo_path}: {e.reason}")
    except Exception as e:
        print(f"  [Warning] Failed to fetch stars for {repo_path}: {e}")
        
    STAR_CACHE[repo_path] = -1
    return -1

def extract_repo_path(row_str: str) -> str:
    """Extract owner/repo string from a markdown table row."""
    # Try finding shields.io/github/stars/owner/repo
    badge_match = re.search(r'github/stars/([^/?#\s]+)/([^/?#\s&]+)', row_str)
    if badge_match:
        return f"{badge_match.group(1)}/{badge_match.group(2)}"
    
    # Try finding github.com/owner/repo
    link_match = re.search(r'github\.com/([^/?#\s]+)/([^/?#\s)]+)', row_str)
    if link_match:
        owner = link_match.group(1)
        repo = link_match.group(2).rstrip('/')
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

def sort_markdown_table(file_path: str):
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
        if line.startswith("|") and ("Rank" in line or "🏅" in line) and ("Repository" in line or "📦" in line):
            header_line = line
            separator_line = lines[i + 1] if i + 1 < len(lines) and lines[i + 1].startswith("|") else None
            
            if separator_line and ":---" in separator_line:
                new_lines.append(header_line)
                new_lines.append(separator_line)
                i += 2
                
                rows = []
                use_medals = False
                
                # Collect all consecutive table rows
                while i < len(lines) and lines[i].startswith("|") and lines[i].strip() != "":
                    row_line = lines[i]
                    if any(medal in row_line for medal in ("🥇", "🥈", "🥉")):
                        use_medals = True
                        
                    parts = [p.strip() for p in row_line.split("|")]
                    # parts: ['', 'Rank', 'Repo', 'Category/Focus', 'Description', '']
                    if len(parts) >= 5:
                        repo_path = extract_repo_path(row_line)
                        rows.append({
                            "original": row_line,
                            "parts": parts,
                            "repo_path": repo_path
                        })
                    else:
                        rows.append({
                            "original": row_line,
                            "parts": None,
                            "repo_path": ""
                        })
                    i += 1
                
                # Fetch stars and sort rows
                for r in rows:
                    if r["repo_path"]:
                        stars = get_repo_stars(r["repo_path"])
                        r["stars"] = stars
                        print(f"  {r['repo_path']:<40} -> {stars:,} stars" if stars >= 0 else f"  {r['repo_path']:<40} -> [error/unknown]")
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
                        formatted_row = f"| {rank_str} | " + " | ".join(col_cells) + " |"
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
    target_files = sys.argv[1:]
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

    for file_arg in target_files:
        sort_markdown_table(file_arg)

if __name__ == "__main__":
    main()
