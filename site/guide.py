#!/usr/bin/env python3
"""Turns the user guide (docs/GUIDE.md, docs/PRZEWODNIK.md) into a page of the project site.

WHAT: a small Markdown-to-HTML converter for exactly what the guide uses - headings, paragraphs,
      lists, images, tables, code blocks, quotes, links, bold, italics and inline code - plus a
      table of contents built from the "##" headings.
WHY:  the guide is written once, in Markdown, so GitHub shows it in the repository; the site page
      is generated from the same file when publishing (apt-repo.sh). No Markdown library needed -
      nothing new to install for the person who publishes.

Usage: guide.py <guide.md> <output.html> <lang: en|pl> <prefix for img/ paths> <path to site root>
"""
import html
import re
import sys

LABELS = {
    "en": {"title": "LamaDeck user guide", "contents": "Contents", "home": "Home", "install": "Install",
           "guide": "Guide", "other": "Polski", "other_lang": "pl"},
    "pl": {"title": "LamaDeck - instrukcja", "contents": "Spis treści", "home": "Strona główna",
           "install": "Instalacja", "guide": "Instrukcja", "other": "English", "other_lang": "en"},
}


def slug(text):
    # WHAT: heading anchor the same way GitHub makes it (lower case, letters/digits/hyphens),
    #       so "#router-litellm" links work both in the repository and on the site.
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def inline(text, img_prefix):
    # WHAT: inline Markdown -> HTML. Code first, so nothing inside `...` is touched.
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue
        part = html.escape(part, quote=False)
        part = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", part)
        part = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", part)

        def link(m):
            label, url = m.group(1), m.group(2)
            # WHY: links between the two guide files point to the other site page instead
            #      (pages: /guide/ in English, /pl/guide/ in Polish)
            if url.endswith(".md"):
                url = "../../guide/" if url == "GUIDE.md" else "../pl/guide/"
            return f'<a href="{html.escape(url)}">{label}</a>'

        part = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, part)
        out.append(part)
    return "".join(out)


def convert(markdown, img_prefix):
    # WHAT: block-level parsing, line by line. Returns (title, body html, [(anchor, heading)]).
    lines = markdown.splitlines()
    body, toc, title = [], [], ""
    i = 0
    paragraph = []

    def flush():
        if paragraph:
            body.append(f"<p>{inline(' '.join(paragraph), img_prefix)}</p>")
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush()
            i += 1
            continue
        if stripped.startswith("```"):
            flush()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            body.append(f"<pre><code>{html.escape(chr(10).join(code))}</code></pre>")
            i += 1
            continue
        heading = re.match(r"^(#{1,4}) (.+)$", stripped)
        if heading:
            flush()
            level, text = len(heading.group(1)), heading.group(2)
            if level == 1:
                title = text
            else:
                anchor = slug(text)
                if level == 2:
                    toc.append((anchor, inline(text, img_prefix)))
                body.append(f'<h{level} id="{anchor}">{inline(text, img_prefix)}</h{level}>')
            i += 1
            continue
        image = re.match(r"^!\[([^\]]*)\]\(([^)\s]+)\)$", stripped)
        if image:
            flush()
            alt, src = image.group(1), image.group(2)
            if src.startswith("img/"):
                src = img_prefix + src[len("img/"):]
            # WHY: a link to the full-size picture - screenshots are 1600 px wide
            body.append(f'<figure class="shot"><a href="{html.escape(src)}"><img src="{html.escape(src)}" '
                        f'alt="{html.escape(alt)}" loading="lazy"></a><figcaption>{html.escape(alt)}</figcaption></figure>')
            i += 1
            continue
        if stripped.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            body.append(f'<p class="callout">{inline(" ".join(quote), img_prefix)}</p>')
            continue
        if stripped.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            head, *rest = rows
            table = ["<table><thead><tr>"] + [f"<th>{inline(c, img_prefix)}</th>" for c in head]
            table.append("</tr></thead><tbody>")
            for row in rest:
                table.append("<tr>" + "".join(f"<td>{inline(c, img_prefix)}</td>" for c in row) + "</tr>")
            table.append("</tbody></table>")
            body.append('<div class="table-wrap">' + "".join(table) + "</div>")
            continue
        item = re.match(r"^(- |\d+\. )(.*)$", stripped)
        if item:
            flush()
            ordered = item.group(1)[0].isdigit()
            items = []
            while i < len(lines):
                current = lines[i]
                match = re.match(r"^(- |\d+\. )(.*)$", current.strip())
                if match and not current.startswith("  "):
                    items.append(match.group(2))
                elif current.startswith("  ") and current.strip() and items:
                    items[-1] += " " + current.strip()  # WHY: continuation line of the same item
                else:
                    break
                i += 1
            tag = "ol" if ordered else "ul"
            body.append(f"<{tag}>" + "".join(f"<li>{inline(t, img_prefix)}</li>" for t in items) + f"</{tag}>")
            continue
        paragraph.append(stripped)
        i += 1
    flush()
    return title, "\n".join(body), toc


def page(lang, title, body, toc, root):
    t = LABELS[lang]
    other = f"{root}guide/" if lang == "pl" else f"{root}pl/guide/"
    home = f"{root}pl/" if lang == "pl" else root
    spis = "".join(f'<li><a href="#{a}">{h}</a></li>' for a, h in toc)
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(t["title"])}</title>
<link rel="icon" href="{root}img/logo.png">
<link rel="stylesheet" href="{root}style.css">
</head>
<body>
<header class="top">
  <div class="wrap-wide">
    <a class="brand" href="{home}"><img src="{root}img/logo.png" alt="" width="36" height="36">LamaDeck</a>
    <nav aria-label="Page">
      <a href="{home}">{t["home"]}</a>
      <a href="{home}#{'instalacja' if lang == 'pl' else 'install'}">{t["install"]}</a>
      <a href="https://github.com/cyryllo/LamaDeck">GitHub</a>
      <a class="lang" href="{other}" lang="{t["other_lang"]}">{t["other"]}</a>
    </nav>
  </div>
</header>
<main class="guide">
  <div class="wrap">
    <h1>{inline(title, "")}</h1>
    <nav class="toc" aria-label="{t["contents"]}"><h2>{t["contents"]}</h2><ol>{spis}</ol></nav>
    {body}
  </div>
</main>
<footer>
  <div class="wrap-wide">
    <span>LamaDeck</span>
    <span><a href="https://github.com/cyryllo/LamaDeck/issues">GitHub Issues</a></span>
  </div>
</footer>
</body>
</html>
"""


def main():
    source, target, lang, img_prefix, root = sys.argv[1:6]
    with open(source, encoding="utf-8") as f:
        title, body, toc = convert(f.read(), img_prefix)
    with open(target, "w", encoding="utf-8") as f:
        f.write(page(lang, title, body, toc, root))


if __name__ == "__main__":
    main()
