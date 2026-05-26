import argparse
import html
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage

PROJECT_DAILY_ARXIV_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "daily_arxiv"))
if PROJECT_DAILY_ARXIV_DIR not in sys.path:
    sys.path.append(PROJECT_DAILY_ARXIV_DIR)

from daily_arxiv.ranking import build_priority_metadata  # type: ignore[reportMissingImports]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="AI enhanced jsonl file path")
    return parser.parse_args()


def load_keywords():
    raw = os.environ.get("INTEREST_KEYWORDS", "")
    return [keyword.strip() for keyword in raw.split(",") if keyword.strip()]


def normalize_text(value):
    if value is None:
        return ""
    return " ".join(str(value).split())


def shorten(text, limit=140):
    text = normalize_text(text)
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def ensure_sentence(prefix, text):
    content = shorten(text)
    if not content:
        return f"{prefix}暂无明确结论。"
    if content[-1] not in "。！？.!?":
        content += "。"
    return f"{prefix}{content}"


def load_papers(path):
    papers = []
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            papers.append(json.loads(line))
    return papers


def get_search_text(paper):
    ai = paper.get("AI", {})
    parts = [
        paper.get("title", ""),
        paper.get("summary", ""),
        ai.get("tldr", ""),
        ai.get("motivation", ""),
        ai.get("method", ""),
        ai.get("result", ""),
        ai.get("conclusion", ""),
    ]
    return normalize_text(" ".join(parts)).lower()


def match_papers(papers, keywords):
    matched = []
    for paper in papers:
        search_text = get_search_text(paper)
        matched_keywords = [keyword for keyword in keywords if keyword.lower() in search_text]
        if matched_keywords:
            if "priority_score" not in paper:
                paper.update(build_priority_metadata(paper))
            matched.append((paper, matched_keywords))
    matched.sort(
        key=lambda item: (
            -int(item[0].get("priority_score", 0) or 0),
            -int(item[0].get("code_stars", 0) or 0),
            normalize_text(item[0].get("title", "")).lower(),
        )
    )
    return matched


def organize_matches(matched_papers, keywords):
    high_weight_groups = {}
    keyword_groups = {keyword: [] for keyword in keywords}

    for paper, matched_keywords in matched_papers:
        ordered_keywords = [keyword for keyword in keywords if keyword in matched_keywords]
        if len(ordered_keywords) >= 2:
            group_name = " + ".join(ordered_keywords)
            high_weight_groups.setdefault(group_name, []).append((paper, ordered_keywords))
        elif len(ordered_keywords) == 1:
            keyword_groups.setdefault(ordered_keywords[0], []).append((paper, ordered_keywords))

    keyword_groups = {
        keyword: items for keyword, items in keyword_groups.items() if items
    }
    return high_weight_groups, keyword_groups


def append_paper_lines(lines, papers, start_index):
    current_index = start_index
    for paper, matched_keywords in papers:
        ai = paper.get("AI", {})
        title = normalize_text(paper.get("title", "Untitled paper"))
        paper_url = paper.get("abs") or paper.get("pdf") or f"https://arxiv.org/abs/{paper.get('id', '')}"
        problem_text = ai.get("motivation") or ai.get("tldr") or paper.get("summary", "")
        method_text = ai.get("method") or ai.get("conclusion") or paper.get("summary", "")
        result_text = ai.get("result") or ai.get("conclusion") or ai.get("tldr") or paper.get("summary", "")

        lines.extend(
            [
                f"{current_index}. {title}",
                f"匹配关键词: {', '.join(matched_keywords)}",
                f"排序分数: {paper.get('priority_score', 0)}",
                f"排序依据: {', '.join(paper.get('priority_reasons', [])) or '默认排序'}",
                ensure_sentence("问题：", problem_text),
                ensure_sentence("方法：", method_text),
                ensure_sentence("结果：", result_text),
                f"论文链接：{paper_url}",
                "",
            ]
        )
        current_index += 1

    return current_index


def append_paper_html(parts, papers, start_index):
    current_index = start_index
    for paper, matched_keywords in papers:
        ai = paper.get("AI", {})
        title = normalize_text(paper.get("title", "Untitled paper"))
        paper_url = paper.get("abs") or paper.get("pdf") or f"https://arxiv.org/abs/{paper.get('id', '')}"
        problem_text = ensure_sentence("问题：", ai.get("motivation") or ai.get("tldr") or paper.get("summary", ""))
        method_text = ensure_sentence("方法：", ai.get("method") or ai.get("conclusion") or paper.get("summary", ""))
        result_text = ensure_sentence("结果：", ai.get("result") or ai.get("conclusion") or ai.get("tldr") or paper.get("summary", ""))

        parts.extend(
            [
                "<div style=\"margin-bottom: 20px; padding: 12px; border: 1px solid #e5e7eb; border-radius: 8px;\">",
                f"<h4 style=\"margin: 0 0 8px 0;\">{current_index}. {html.escape(title)}</h4>",
                f"<p><strong>匹配关键词:</strong> {html.escape(', '.join(matched_keywords))}</p>",
                f"<p><strong>排序分数:</strong> {paper.get('priority_score', 0)}</p>",
                f"<p><strong>排序依据:</strong> {html.escape(', '.join(paper.get('priority_reasons', [])) or '默认排序')}</p>",
                f"<p>{html.escape(problem_text)}</p>",
                f"<p>{html.escape(method_text)}</p>",
                f"<p>{html.escape(result_text)}</p>",
                f"<p><strong>论文链接:</strong> <a href=\"{html.escape(paper_url, quote=True)}\">{html.escape(paper_url)}</a></p>",
                "</div>",
            ]
        )
        current_index += 1

    return current_index


def build_digest(date_str, keywords, matched_papers):
    lines = [
        f"arXiv 每日关键词简报 - {date_str}",
        "",
        f"关注关键词: {', '.join(keywords)}",
        f"命中论文数: {len(matched_papers)}",
        "",
    ]

    if not matched_papers:
        lines.extend(
            [
                "今天没有发现命中关键词的新论文。",
                "你可以考虑放宽关键词，或继续等待下一次定时抓取。",
            ]
        )
        return "\n".join(lines)

    high_weight_groups, keyword_groups = organize_matches(matched_papers, keywords)
    current_index = 1

    if high_weight_groups:
        lines.extend(["高权重论文（同时命中多组关键词）", ""])
        for group_name, papers in high_weight_groups.items():
            lines.extend([f"## {group_name}", ""])
            current_index = append_paper_lines(lines, papers, current_index)

    if keyword_groups:
        lines.extend(["其余论文（按关键词聚类）", ""])
        for keyword in keywords:
            papers = keyword_groups.get(keyword)
            if not papers:
                continue
            lines.extend([f"## {keyword}", ""])
            current_index = append_paper_lines(lines, papers, current_index)

    return "\n".join(lines).strip()


def build_html_digest(date_str, keywords, matched_papers):
    parts = [
        "<html><body>",
        f"<h2>arXiv 每日关键词简报 - {html.escape(date_str)}</h2>",
        f"<p><strong>关注关键词:</strong> {html.escape(', '.join(keywords))}</p>",
        f"<p><strong>命中论文数:</strong> {len(matched_papers)}</p>",
    ]

    if not matched_papers:
        parts.extend(
            [
                "<p>今天没有发现命中关键词的新论文。</p>",
                "<p>你可以考虑放宽关键词，或继续等待下一次定时抓取。</p>",
                "</body></html>",
            ]
        )
        return "".join(parts)

    high_weight_groups, keyword_groups = organize_matches(matched_papers, keywords)
    current_index = 1

    if high_weight_groups:
        parts.append("<h3>高权重论文（同时命中多组关键词）</h3>")
        for group_name, papers in high_weight_groups.items():
            parts.append(f"<h4>{html.escape(group_name)}</h4>")
            current_index = append_paper_html(parts, papers, current_index)

    if keyword_groups:
        parts.append("<h3>其余论文（按关键词聚类）</h3>")
        for keyword in keywords:
            papers = keyword_groups.get(keyword)
            if not papers:
                continue
            parts.append(f"<h4>{html.escape(keyword)}</h4>")
            current_index = append_paper_html(parts, papers, current_index)

    parts.append("</body></html>")
    return "".join(parts)


def build_message(date_str, recipient, sender, body, matched_count, html_body):
    message = EmailMessage()
    message["Subject"] = f"【arXiv日报】{date_str} 关键词命中 {matched_count} 篇"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body, subtype="plain", charset="utf-8")
    message.add_alternative(html_body, subtype="html", charset="utf-8")
    return message


def get_sender():
    username = os.environ.get("SMTP_USERNAME", "").strip()
    sender_name = os.environ.get("SMTP_SENDER_NAME", "").strip()
    if not sender_name:
        return username
    return f"{sender_name} <{username}>"


def send_email(message):
    host = os.environ.get("SMTP_HOST", "").strip()
    port = os.environ.get("SMTP_PORT", "").strip()
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").strip()

    missing = [
        name
        for name, value in [
            ("SMTP_HOST", host),
            ("SMTP_PORT", port),
            ("SMTP_USERNAME", username),
            ("SMTP_PASSWORD", password),
        ]
        if not value
    ]
    if missing:
        print(
            "Skip email delivery because SMTP config is incomplete: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return

    port = int(port)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as server:
            server.login(username, password)
            server.send_message(message)
        return

    with smtplib.SMTP(host, port) as server:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(username, password)
        server.send_message(message)


def main():
    args = parse_args()
    keywords = load_keywords()
    recipient = os.environ.get("EMAIL_RECIPIENT", "").strip()

    if not keywords:
        print("Skip email digest because INTEREST_KEYWORDS is empty.", file=sys.stderr)
        return

    if not recipient:
        print("Skip email digest because EMAIL_RECIPIENT is empty.", file=sys.stderr)
        return

    if not os.path.exists(args.data):
        print(f"Skip email digest because data file does not exist: {args.data}", file=sys.stderr)
        return

    papers = load_papers(args.data)
    matched_papers = match_papers(papers, keywords)
    date_str = os.path.basename(args.data).split("_AI_enhanced_")[0]
    body = build_digest(date_str, keywords, matched_papers)
    html_body = build_html_digest(date_str, keywords, matched_papers)
    sender = get_sender()
    message = build_message(date_str, recipient, sender, body, len(matched_papers), html_body)

    send_email(message)
    print(
        f"Email digest prepared for {recipient}, matched papers: {len(matched_papers)}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
