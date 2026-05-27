#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PROJECT_DAILY_ARXIV_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "daily_arxiv"))
if PROJECT_DAILY_ARXIV_DIR not in sys.path:
    sys.path.append(PROJECT_DAILY_ARXIV_DIR)

from daily_arxiv.ranking import build_priority_metadata, is_medical_paper, sort_papers  # type: ignore[reportMissingImports]


DEFAULT_URL = "https://openaccess.thecvf.com/CVPR2026?day=all"
DEFAULT_KEYWORDS = "super resolution, diffusion, VQ, ISP"
DEFAULT_KEYWORD_GROUPS = {
    "super resolution": ["super resolution", "super-resolution", "sr"],
    "diffusion": ["diffusion", "diffusion model", "denoising diffusion", "latent diffusion", "ddpm"],
    "VQ": ["vq", "vector quantization", "vector-quantized", "vector quantized", "vqgan", "vq-vae"],
    "ISP": ["isp", "image signal processing", "image signal processor", "raw image"],
}


@dataclass
class Paper:
    title: str
    authors: List[str]
    html_url: str
    pdf_url: str
    arxiv_url: str = ""
    code_url: str = ""
    code_stars: int = 0
    abstract: str = ""
    comment: str = ""
    matched_groups: List[str] | None = None
    priority_score: int = 0
    priority_reasons: List[str] | None = None
    top_conference: str = ""
    summary: Dict[str, str] | None = None

    def to_item(self) -> Dict:
        return {
            "title": self.title,
            "authors": self.authors,
            "summary": self.abstract,
            "comment": self.comment,
            "code_url": self.code_url,
            "code_stars": self.code_stars,
            "categories": [],
        }

    def apply_priority_metadata(self):
        metadata = build_priority_metadata(self.to_item())
        self.priority_score = metadata.get("priority_score", 0)
        self.priority_reasons = metadata.get("priority_reasons", [])
        self.top_conference = metadata.get("top_conference", "")
        return self

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "authors": self.authors,
            "html_url": self.html_url,
            "pdf_url": self.pdf_url,
            "arxiv_url": self.arxiv_url,
            "code_url": self.code_url,
            "code_stars": self.code_stars,
            "abstract": self.abstract,
            "comment": self.comment,
            "matched_groups": self.matched_groups or [],
            "priority_score": self.priority_score,
            "priority_reasons": self.priority_reasons or [],
            "top_conference": self.top_conference,
            "summary": self.summary or {},
        }


def normalize_text(value: str | List[str] | None) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    return " ".join(str(value).split())


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        read=3,
        connect=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }
    )
    return session


def parse_keyword_groups(raw_keywords: str) -> Dict[str, List[str]]:
    if not raw_keywords.strip():
        return DEFAULT_KEYWORD_GROUPS

    groups: Dict[str, List[str]] = {}
    for keyword in [item.strip() for item in raw_keywords.split(",") if item.strip()]:
        lowered = keyword.lower()
        aliases = {lowered, lowered.replace("-", " "), lowered.replace(" ", "-")}
        for default_name, default_aliases in DEFAULT_KEYWORD_GROUPS.items():
            if lowered == default_name.lower():
                aliases.update(alias.lower() for alias in default_aliases)
        groups[keyword] = sorted(aliases)
    return groups


def fetch_list_page(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=(20, 120))
    response.raise_for_status()
    return response.text


def parse_paper_list(list_html: str, base_url: str) -> List[Paper]:
    soup = BeautifulSoup(list_html, "html.parser")
    papers: List[Paper] = []

    for title_dt in soup.select("dt.ptitle"):
        title_link = title_dt.find("a", href=True)
        if not title_link:
            continue

        details_dd = title_dt.find_next_sibling("dd")
        if not details_dd:
            continue

        title = normalize_text(title_link.get_text(" ", strip=True))
        html_url = urljoin(base_url, title_link["href"])

        authors = []
        for form in details_dd.find_all("form", class_="authsearch"):
            author_link = form.find("a")
            if author_link:
                author = normalize_text(author_link.get_text(" ", strip=True))
                if author:
                    authors.append(author)

        pdf_url = ""
        arxiv_url = ""
        code_url = ""
        for link in details_dd.find_all("a", href=True):
            href = link["href"]
            full_url = urljoin(base_url, href)
            lowered = full_url.lower()
            if lowered.endswith(".pdf") and "/papers/" in lowered and not pdf_url:
                pdf_url = full_url
            elif "arxiv.org/abs/" in lowered and not arxiv_url:
                arxiv_url = full_url
            elif "github.com/" in lowered and not code_url:
                code_url = full_url

        papers.append(
            Paper(
                title=title,
                authors=authors,
                html_url=html_url,
                pdf_url=pdf_url,
                arxiv_url=arxiv_url,
                code_url=code_url,
                comment="Accepted to CVPR 2026",
            )
        )

    return papers


def extract_abstract(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    for selector in ["#abstract", ".abstract", "#abstracts", ".paper-abstract"]:
        node = soup.select_one(selector)
        if node:
            text = normalize_text(node.get_text(" ", strip=True))
            if text:
                return text

    patterns = [
        r'<div[^>]+id="abstract"[^>]*>(.*?)</div>',
        r'<div[^>]+class="abstract"[^>]*>(.*?)</div>',
        r"<b>Abstract</b>\s*(.*?)</p>",
        r"<strong>Abstract</strong>\s*(.*?)</p>",
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, re.I | re.S)
        if match:
            text = BeautifulSoup(match.group(1), "html.parser").get_text(" ", strip=True)
            text = normalize_text(text)
            if text:
                return text
    return ""


def maybe_extract_code_url(html_text: str) -> str:
    match = re.search(r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", html_text)
    if match:
        return match.group(0).rstrip(".,)")
    return ""


def fetch_paper_details(session: requests.Session, paper: Paper) -> Paper:
    response = session.get(paper.html_url, timeout=(20, 180))
    response.raise_for_status()
    html_text = response.text
    paper.abstract = extract_abstract(html_text)
    if not paper.code_url:
        paper.code_url = maybe_extract_code_url(html_text)
    return paper


def fetch_github_stars(session: requests.Session, code_url: str, token: str = "") -> int:
    match = re.search(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", code_url)
    if not match:
        return 0
    owner, repo = match.groups()
    repo = repo.rstrip(".git")

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = session.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers=headers,
            timeout=(10, 30),
        )
        if response.ok:
            return int(response.json().get("stargazers_count", 0))
    except requests.RequestException:
        pass
    return 0


def match_groups(paper: Paper, keyword_groups: Dict[str, List[str]]) -> List[str]:
    text = normalize_text(f"{paper.title} {paper.abstract}").lower()
    matched = []
    for group_name, aliases in keyword_groups.items():
        if any(alias.lower() in text for alias in aliases):
            matched.append(group_name)
    return matched


def summarize_with_deepseek(session: requests.Session, paper: Paper, api_key: str, base_url: str, model: str) -> Dict[str, str]:
    prompt = (
        "你是一个计算机视觉会议论文整理助手。"
        "请根据给定的论文标题和摘要，用中文输出一个 JSON 对象，包含三个字段："
        "problem、method、result。每个字段只写一句简短的话，不要超过35个汉字，不要带编号。"
    )
    user_content = (
        f"标题：{paper.title}\n"
        f"摘要：{paper.abstract}\n"
        f"附加信息：{paper.comment}\n"
        "请严格只返回 JSON，例如："
        '{"problem":"...","method":"...","result":"..."}'
    )
    response = session.post(
        build_chat_url(base_url),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_content},
            ],
        },
        timeout=(20, 180),
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", content, re.S)
    payload = json.loads(match.group(0) if match else content)
    return {
        "problem": normalize_text(payload.get("problem", "")),
        "method": normalize_text(payload.get("method", "")),
        "result": normalize_text(payload.get("result", "")),
    }


def build_chat_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def organize_papers(papers: List[Paper], keyword_order: List[str]) -> Tuple[Dict[str, List[Paper]], Dict[str, List[Paper]]]:
    high_weight_groups: Dict[str, List[Paper]] = {}
    keyword_groups: Dict[str, List[Paper]] = {keyword: [] for keyword in keyword_order}

    for paper in papers:
        groups = paper.matched_groups or []
        ordered_groups = [group for group in keyword_order if group in groups]
        if len(ordered_groups) >= 2:
            name = " + ".join(ordered_groups)
            high_weight_groups.setdefault(name, []).append(paper)
        elif len(ordered_groups) == 1:
            keyword_groups.setdefault(ordered_groups[0], []).append(paper)

    return high_weight_groups, {k: v for k, v in keyword_groups.items() if v}


def ensure_sentence(prefix: str, text: str) -> str:
    text = normalize_text(text)
    if not text:
        return f"{prefix}暂无明确结论。"
    if text[-1] not in "。！？.!?":
        text += "。"
    return f"{prefix}{text}"


def append_text_section(lines: List[str], papers: List[Paper], start_index: int) -> int:
    current_index = start_index
    for paper in papers:
        summary = paper.summary or {}
        lines.extend(
            [
                f"{current_index}. {paper.title}",
                f"匹配关键词: {', '.join(paper.matched_groups or [])}",
                f"排序分数: {paper.priority_score}",
                f"排序依据: {', '.join(paper.priority_reasons or []) or '默认排序'}",
                ensure_sentence("问题：", summary.get("problem", "")),
                ensure_sentence("方法：", summary.get("method", "")),
                ensure_sentence("结果：", summary.get("result", "")),
                f"论文链接：{paper.html_url}",
                "",
            ]
        )
        current_index += 1
    return current_index


def append_html_section(parts: List[str], papers: List[Paper], start_index: int) -> int:
    current_index = start_index
    for paper in papers:
        summary = paper.summary or {}
        parts.extend(
            [
                '<div style="margin-bottom:20px;padding:12px;border:1px solid #e5e7eb;border-radius:8px;">',
                f'<h4 style="margin:0 0 8px 0;"><a href="{html.escape(paper.html_url, quote=True)}">{current_index}. {html.escape(paper.title)}</a></h4>',
                f"<p><strong>匹配关键词:</strong> {html.escape(', '.join(paper.matched_groups or []))}</p>",
                f"<p><strong>排序分数:</strong> {paper.priority_score}</p>",
                f"<p><strong>排序依据:</strong> {html.escape(', '.join(paper.priority_reasons or []) or '默认排序')}</p>",
                f"<p>{html.escape(ensure_sentence('问题：', summary.get('problem', '')))}</p>",
                f"<p>{html.escape(ensure_sentence('方法：', summary.get('method', '')))}</p>",
                f"<p>{html.escape(ensure_sentence('结果：', summary.get('result', '')))}</p>",
                f'<p><strong>论文链接:</strong> <a href="{html.escape(paper.html_url, quote=True)}">{html.escape(paper.html_url)}</a></p>',
                "</div>",
            ]
        )
        current_index += 1
    return current_index


def build_email_bodies(papers: List[Paper], keyword_order: List[str], source_url: str) -> Tuple[str, str]:
    lines = [
        f"CVPR 关键词简报 - {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"来源页面: {source_url}",
        f"关键词: {', '.join(keyword_order)}",
        f"命中论文数: {len(papers)}",
        "",
    ]
    html_parts = [
        "<html><body>",
        f"<h2>CVPR 关键词简报 - {datetime.now().strftime('%Y-%m-%d')}</h2>",
        f"<p><strong>来源页面:</strong> <a href=\"{html.escape(source_url, quote=True)}\">{html.escape(source_url)}</a></p>",
        f"<p><strong>关键词:</strong> {html.escape(', '.join(keyword_order))}</p>",
        f"<p><strong>命中论文数:</strong> {len(papers)}</p>",
    ]

    if not papers:
        lines.append("没有命中关键词的论文。")
        html_parts.append("<p>没有命中关键词的论文。</p></body></html>")
        return "\n".join(lines), "".join(html_parts)

    high_weight_groups, keyword_groups = organize_papers(papers, keyword_order)
    current_index = 1

    if high_weight_groups:
        lines.extend(["高权重论文（同时命中多组关键词）", ""])
        html_parts.append("<h3>高权重论文（同时命中多组关键词）</h3>")
        for group_name, group_papers in high_weight_groups.items():
            lines.extend([f"## {group_name}", ""])
            html_parts.append(f"<h4>{html.escape(group_name)}</h4>")
            current_index = append_text_section(lines, group_papers, current_index)
            html_start = current_index - len(group_papers)
            append_html_section(html_parts, group_papers, html_start)

    if keyword_groups:
        lines.extend(["其余论文（按关键词聚类）", ""])
        html_parts.append("<h3>其余论文（按关键词聚类）</h3>")
        for keyword in keyword_order:
            group_papers = keyword_groups.get(keyword)
            if not group_papers:
                continue
            lines.extend([f"## {keyword}", ""])
            html_parts.append(f"<h4>{html.escape(keyword)}</h4>")
            current_index = append_text_section(lines, group_papers, current_index)
            html_start = current_index - len(group_papers)
            append_html_section(html_parts, group_papers, html_start)

    html_parts.append("</body></html>")
    return "\n".join(lines).strip(), "".join(html_parts)


def send_email(recipient: str, sender: str, subject: str, text_body: str, html_body: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_port = os.environ.get("SMTP_PORT", "").strip()
    smtp_username = os.environ.get("SMTP_USERNAME", "").strip()
    smtp_password = os.environ.get("SMTP_PASSWORD", "").strip()
    if not all([smtp_host, smtp_port, smtp_username, smtp_password, recipient]):
        raise RuntimeError("SMTP_HOST/SMTP_PORT/SMTP_USERNAME/SMTP_PASSWORD/recipient 未完整配置")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(text_body, subtype="plain", charset="utf-8")
    message.add_alternative(html_body, subtype="html", charset="utf-8")

    port = int(smtp_port)
    if port == 465:
        with smtplib.SMTP_SSL(smtp_host, port, context=ssl.create_default_context()) as server:
            server.login(smtp_username, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(smtp_host, port) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(smtp_username, smtp_password)
            server.send_message(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch OpenAccess papers, summarize matched keywords, and email the digest.")
    parser.add_argument("--url", default=DEFAULT_URL, help="CVPR/OpenAccess paper list URL")
    parser.add_argument(
        "--keywords",
        default=DEFAULT_KEYWORDS,
        help="Comma-separated keyword groups. Defaults to super resolution, diffusion, VQ, ISP",
    )
    parser.add_argument("--recipient", default=os.environ.get("EMAIL_RECIPIENT", ""), help="Email recipient")
    parser.add_argument("--model", default=os.environ.get("MODEL_NAME", "deepseek-chat"), help="DeepSeek/OpenAI-compatible model name")
    parser.add_argument("--output-dir", default="conference_outputs", help="Directory to store generated JSON and HTML previews")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    github_token = os.environ.get("TOKEN_GITHUB", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()

    if not api_key or not base_url:
        raise RuntimeError("请设置 OPENAI_API_KEY 和 OPENAI_BASE_URL")

    keyword_groups = parse_keyword_groups(args.keywords)
    keyword_order = list(keyword_groups.keys())

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    session = build_session()
    list_html = fetch_list_page(session, args.url)
    papers = parse_paper_list(list_html, args.url)

    matched_papers: List[Paper] = []
    for paper in papers:
        try:
            paper = fetch_paper_details(session, paper)
        except requests.RequestException:
            continue

        if is_medical_paper({"title": paper.title, "summary": paper.abstract, "comment": paper.comment, "categories": []}):
            continue

        matched_groups = match_groups(paper, keyword_groups)
        if not matched_groups:
            continue

        paper.matched_groups = matched_groups
        if paper.code_url:
            paper.code_stars = fetch_github_stars(session, paper.code_url, github_token)
        paper.apply_priority_metadata()
        if len(matched_groups) >= 2:
            paper.priority_score += 25
            reasons = paper.priority_reasons or []
            reasons.append("多关键词同时命中")
            paper.priority_reasons = reasons

        paper.summary = summarize_with_deepseek(session, paper, api_key, base_url, args.model)
        matched_papers.append(paper)

    matched_papers = [
        paper
        for paper in sort_papers([paper.to_dict() for paper in matched_papers])
    ]
    matched_papers = [
        Paper(
            title=item["title"],
            authors=item.get("authors", []),
            html_url=item.get("html_url", ""),
            pdf_url=item.get("pdf_url", ""),
            arxiv_url=item.get("arxiv_url", ""),
            code_url=item.get("code_url", ""),
            code_stars=item.get("code_stars", 0),
            abstract=item.get("abstract", ""),
            comment=item.get("comment", ""),
            matched_groups=item.get("matched_groups", []),
            priority_score=item.get("priority_score", 0),
            priority_reasons=item.get("priority_reasons", []),
            top_conference=item.get("top_conference", ""),
            summary=item.get("summary", {}),
        )
        for item in matched_papers
    ]

    text_body, html_body = build_email_bodies(matched_papers, keyword_order, args.url)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"conference_digest_{timestamp}.json"
    html_path = output_dir / f"conference_digest_{timestamp}.html"
    json_path.write_text(json.dumps([paper.to_dict() for paper in matched_papers], ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(html_body, encoding="utf-8")

    if args.recipient:
        sender_name = os.environ.get("SMTP_SENDER_NAME", "Conference Digest").strip()
        sender_email = os.environ.get("SMTP_USERNAME", "").strip()
        sender = f"{sender_name} <{sender_email}>" if sender_email else sender_name
        send_email(
            recipient=args.recipient,
            sender=sender,
            subject=f"【CVPR关键词简报】{datetime.now().strftime('%Y-%m-%d')} 命中 {len(matched_papers)} 篇",
            text_body=text_body,
            html_body=html_body,
        )

    print(f"Matched papers: {len(matched_papers)}")
    print(f"Saved JSON to: {json_path}")
    print(f"Saved HTML to: {html_path}")


if __name__ == "__main__":
    main()
