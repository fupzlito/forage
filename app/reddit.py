"""Reddit JSON parsing and comment-tree rendering for Forage.

Tier-1 of the Reddit 3-tier pipeline (``app/extract.py``): parse the official
``.json`` responses (thread + comments listing, subreddit/multireddit listings)
into clean markdown with OP/MOD/ADMIN badges, threaded indentation, and
browser-nav-header-safe formatting.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Tuple


def make_reddit_status_result(sub_or_label: str, status_type: str, details: str = "", method: str = "reddit+json") -> dict:
    """Generate a clean, unified structured markdown result for Reddit status errors."""
    clean_sub = sub_or_label.strip() if sub_or_label else "Reddit Community"
    if status_type == "private":
        title = f"{clean_sub} - Private Community"
        desc = f"{clean_sub} is a private community. You must be invited or approved by its moderators to view its content."
        if details and details.lower() not in ("forbidden", "private", "error"):
            desc += f" ({details})"
    elif status_type == "banned":
        title = f"{clean_sub} - Community Banned"
        desc = f"{clean_sub} has been banned from Reddit."
        if details and details.lower() not in ("forbidden", "banned", "error"):
            desc += f" ({details})"
    elif status_type == "not_found":
        title = f"{clean_sub} - Community Not Found"
        desc = details or f"The subreddit `{clean_sub}` does not exist."
    elif status_type == "post_not_found":
        title = "Reddit - Post Not Found"
        desc = details or "The requested Reddit post was deleted or does not exist (404 Not Found)."
    else:
        title = f"{clean_sub} - Access Restricted"
        desc = details or f"Access to {clean_sub} is restricted."

    content = f"# {title}\n\n{desc}"
    return {
        "title": title,
        "content": content,
        "raw_content": content,
        "method": method,
    }


def _format_timestamp(ts: Any) -> str:
    """Format Unix epoch timestamp to a clean date/time string."""
    if not ts:
        return ""
    try:
        dt = datetime.fromtimestamp(float(ts), timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ""


def format_reddit_comments(children: list, indent: int = 0) -> list[str]:
    lines = []
    prefix = "  " * indent + "> " if indent > 0 else "### "
    for child in children:
        if not isinstance(child, dict) or child.get("kind") != "t1":
            continue
        cdata = child.get("data", {})
        author = cdata.get("author", "[deleted]")
        body = cdata.get("body", "")
        score = cdata.get("score", 0)
        created_str = _format_timestamp(cdata.get("created_utc"))
        is_op = cdata.get("is_submitter", False)
        distinguished = cdata.get("distinguished")
        flair = cdata.get("author_flair_text")
        if not body or body == "[deleted]" or body == "[removed]":
            continue

        badges = []
        if is_op:
            badges.append("[OP]")
        if distinguished == "moderator":
            badges.append("[MOD]")
        elif distinguished == "admin":
            badges.append("[ADMIN]")
        if flair:
            badges.append(f"[Flair: {flair}]")
        badge_str = (" " + " ".join(badges)) if badges else ""

        meta_parts = [f"Score: {score}"]
        if created_str:
            meta_parts.append(created_str)
        meta = " | ".join(meta_parts)

        lines.append(f"{prefix}**u/{author}**{badge_str} ({meta})\n{prefix}{body}\n")

        replies = cdata.get("replies")
        if isinstance(replies, dict) and "data" in replies:
            reply_children = replies.get("data", {}).get("children", [])
            lines.extend(format_reddit_comments(reply_children, indent + 1))
    return lines


def parse_reddit_json(raw_json: Any) -> Tuple[str, str]:
    """Parse Reddit JSON response into (markdown_content, title)."""
    # 1. Subreddit or user post listing (e.g. /r/SpaceX/.json or /search.json or /r/stocks/search.json)
    if isinstance(raw_json, dict) and raw_json.get("kind") == "Listing":
        children = raw_json.get("data", {}).get("children", [])
        posts = []
        subreddits = set()
        for item in children:
            if not isinstance(item, dict) or item.get("kind") != "t3":
                continue
            p = item.get("data", {})
            post_sub = p.get("subreddit_name_prefixed") or (f"r/{p.get('subreddit')}" if p.get("subreddit") else "Reddit")
            subreddits.add(post_sub)
            p_title = p.get("title", "")
            p_author = p.get("author", "[deleted]")
            p_score = p.get("score", 0)
            p_comments = p.get("num_comments", 0)
            p_created = _format_timestamp(p.get("created_utc"))
            permalink = p.get("permalink", "")
            p_url = f"https://www.reddit.com{permalink}" if permalink else p.get("url", "")
            p_selftext = p.get("selftext", "")

            meta_parts = [f"**Subreddit**: {post_sub}", f"**Author**: u/{p_author}"]
            if p_created:
                meta_parts.append(f"**Posted**: {p_created}")
            meta_parts.extend([f"**Score**: {p_score}", f"**Comments**: {p_comments}"])

            entry = f"### [{p_title}]({p_url})\n" + " | ".join(meta_parts)
            if p_selftext:
                preview = p_selftext[:300].strip() + ("..." if len(p_selftext) > 300 else "")
                entry += f"\n\n{preview}"
            posts.append(entry)

        if len(subreddits) == 1:
            title = f"{next(iter(subreddits))} - Reddit Posts"
        elif len(subreddits) > 1:
            title = "Reddit Search / Listing Results"
        else:
            title = "Reddit Posts"
        content = f"# {title}\n\n" + ("\n\n---\n\n".join(posts) if posts else "No posts found.")
        return content, title

    # 2. Thread + comments listing ([post_listing, comments_listing])
    if not isinstance(raw_json, list) or not raw_json:
        raise ValueError("Invalid Reddit JSON response format")

    post_children = raw_json[0].get("data", {}).get("children", [])
    if not post_children:
        return "The requested Reddit post was deleted or does not exist (404 Not Found).", "Reddit - Post Not Found"

    post_data = post_children[0].get("data", {})
    title = post_data.get("title", "Reddit Post")
    subreddit = post_data.get("subreddit_name_prefixed", post_data.get("subreddit", ""))
    author = post_data.get("author", "[deleted]")
    score = post_data.get("score", 0)
    num_comments = post_data.get("num_comments", 0)
    created_str = _format_timestamp(post_data.get("created_utc"))
    selftext = post_data.get("selftext", "")
    url = post_data.get("url", "")
    removed_by_category = post_data.get("removed_by_category")

    header_parts = [f"**Subreddit**: {subreddit}", f"**Author**: u/{author}"]
    if created_str:
        header_parts.append(f"**Posted**: {created_str}")
    header_parts.extend([f"**Score**: {score}", f"**Comments**: {num_comments}"])

    header = f"# {title}\n\n" + " | ".join(header_parts) + "\n"

    # Status notice for deleted/removed posts
    if selftext == "[deleted]" or (author == "[deleted]" and not selftext):
        header += "\n> ⚠️ *This post was deleted by the author.*\n"
    elif selftext == "[removed]" or removed_by_category:
        reason_label = f" ({removed_by_category})" if removed_by_category else ""
        header += f"\n> ⚠️ *This post was removed by Reddit moderators or filters{reason_label}.*\n"

    if url and not url.startswith("https://www.reddit.com") and not url.startswith("https://reddit.com"):
        header += f"\n**Link**: [{url}]({url})\n"

    body_parts = [header]
    if selftext and selftext not in ("[deleted]", "[removed]"):
        body_parts.append(f"\n{selftext}\n")

    if len(raw_json) > 1 and isinstance(raw_json[1], dict):
        comments_children = raw_json[1].get("data", {}).get("children", [])
        comment_lines = format_reddit_comments(comments_children)
        if comment_lines:
            body_parts.append("\n---\n\n## Comments\n\n" + "\n".join(comment_lines))

    markdown_text = "\n".join(body_parts)
    return markdown_text, title
