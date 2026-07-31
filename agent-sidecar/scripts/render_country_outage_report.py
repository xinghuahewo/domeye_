#!/usr/bin/env python3
"""Render a validated country-outage report document as a PDF on stdout.

Input is one UTF-8 JSON object on stdin:

    {"fontPath": "/trusted/font.ttf", "document": {...}}

The caller owns trust decisions for the Python executable and font path. This
script never invokes another process or reads event data on its own.
"""

from __future__ import annotations

import html
import io
import json
import os
import sys
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from pypdf import PdfReader


PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN_X = 18 * mm
MARGIN_TOP = 20 * mm
MARGIN_BOTTOM = 18 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN_X

INK = colors.HexColor("#17212B")
MUTED = colors.HexColor("#5C6873")
NAVY = colors.HexColor("#173A5E")
TEAL = colors.HexColor("#087E8B")
PALE_TEAL = colors.HexColor("#EAF5F5")
PALE_BLUE = colors.HexColor("#EEF3F7")
PALE_ORANGE = colors.HexColor("#FFF3DE")
ORANGE = colors.HexColor("#B66812")
RULE = colors.HexColor("#D7E0E6")
WHITE = colors.white

FONT_NAME = "DomeyeCjk"
MAX_PDF_PAGES = 40


def fail(message: str) -> "NoReturn":
    sys.stderr.write(f"country-outage PDF render failed: {message}\n")
    raise SystemExit(2)


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} must be a non-empty string")
    return value


def safe_text(value: Any) -> str:
    return html.escape(str(value), quote=False).replace("\n", "<br/>")


def evidence_text(references: Any) -> str:
    if not isinstance(references, list):
        return ""
    cleaned = [str(item).strip() for item in references if str(item).strip()]
    return "、".join(cleaned)


def make_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "DomeyeTitle",
            parent=base["Title"],
            fontName=FONT_NAME,
            fontSize=22,
            leading=30,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=5 * mm,
            wordWrap="CJK",
        ),
        "subtitle": ParagraphStyle(
            "DomeyeSubtitle",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=11,
            leading=17,
            textColor=MUTED,
            spaceAfter=5 * mm,
            wordWrap="CJK",
        ),
        "notice": ParagraphStyle(
            "DomeyeNotice",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=8.5,
            leading=13,
            textColor=ORANGE,
            wordWrap="CJK",
        ),
        "summary": ParagraphStyle(
            "DomeyeSummary",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=10.5,
            leading=17,
            textColor=INK,
            wordWrap="CJK",
        ),
        "h2": ParagraphStyle(
            "DomeyeH2",
            parent=base["Heading2"],
            fontName=FONT_NAME,
            fontSize=14,
            leading=20,
            textColor=NAVY,
            spaceBefore=7 * mm,
            spaceAfter=3 * mm,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "body": ParagraphStyle(
            "DomeyeBody",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=9.5,
            leading=16,
            textColor=INK,
            spaceAfter=2.5 * mm,
            wordWrap="CJK",
            allowWidows=0,
            allowOrphans=0,
        ),
        "evidence": ParagraphStyle(
            "DomeyeEvidence",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=7.4,
            leading=11,
            textColor=MUTED,
            leftIndent=3 * mm,
            borderColor=TEAL,
            borderWidth=0,
            borderPadding=(0, 0, 0, 3 * mm),
            spaceAfter=3 * mm,
            wordWrap="CJK",
        ),
        "table_head": ParagraphStyle(
            "DomeyeTableHead",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=8.2,
            leading=11,
            textColor=WHITE,
            alignment=TA_LEFT,
            wordWrap="CJK",
        ),
        "table": ParagraphStyle(
            "DomeyeTable",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=8,
            leading=11.5,
            textColor=INK,
            wordWrap="CJK",
        ),
        "table_value": ParagraphStyle(
            "DomeyeTableValue",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=9,
            leading=12,
            textColor=NAVY,
            alignment=TA_RIGHT,
            wordWrap="CJK",
        ),
        "table_evidence": ParagraphStyle(
            "DomeyeTableEvidence",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=6.8,
            leading=9.5,
            textColor=MUTED,
            wordWrap="CJK",
        ),
        "bullet": ParagraphStyle(
            "DomeyeBullet",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=9.2,
            leading=15,
            textColor=INK,
            leftIndent=6 * mm,
            firstLineIndent=-4 * mm,
            spaceAfter=1.2 * mm,
            wordWrap="CJK",
        ),
        "meta_key": ParagraphStyle(
            "DomeyeMetaKey",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=7.7,
            leading=11,
            textColor=MUTED,
            wordWrap="CJK",
        ),
        "meta_value": ParagraphStyle(
            "DomeyeMetaValue",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=7.5,
            leading=10.5,
            textColor=INK,
            wordWrap="CJK",
        ),
        "footer_left": ParagraphStyle(
            "DomeyeFooterLeft",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=6.8,
            leading=8,
            textColor=MUTED,
            alignment=TA_LEFT,
            wordWrap="CJK",
        ),
        "footer_right": ParagraphStyle(
            "DomeyeFooterRight",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=7,
            leading=8,
            textColor=MUTED,
            alignment=TA_RIGHT,
            wordWrap="CJK",
        ),
    }


def paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(safe_text(value), style)


def footer(canvas: Any, doc: SimpleDocTemplate, artifact_id: str) -> None:
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN_X, 12 * mm, PAGE_WIDTH - MARGIN_X, 12 * mm)
    canvas.setFont(FONT_NAME, 6.8)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN_X, 8.2 * mm, f"制品 {artifact_id}")
    canvas.drawRightString(
        PAGE_WIDTH - MARGIN_X,
        8.2 * mm,
        f"第 {doc.page} 页",
    )
    canvas.restoreState()


def render(payload: dict[str, Any]) -> bytes:
    font_path = require_string(payload.get("fontPath"), "fontPath")
    if not os.path.isfile(font_path):
        fail("fontPath does not name a readable file")
    if os.path.splitext(font_path)[1].lower() not in {".ttf", ".otf"}:
        fail("fontPath must be a TTF or OTF font")

    report = require_object(payload.get("document"), "document")
    if report.get("schemaVersion") != "country_outage_report_document_v1":
        fail("document schemaVersion is unsupported")
    draft = require_object(report.get("draft"), "document.draft")
    event = require_object(report.get("event"), "document.event")
    snapshot = require_object(report.get("snapshot"), "document.snapshot")
    model = require_object(report.get("model"), "document.model")
    validation = require_object(report.get("validation"), "document.validation")

    artifact_id = require_string(report.get("artifactId"), "document.artifactId")
    title = require_string(draft.get("title"), "document.draft.title")
    generated_at = require_string(
        report.get("generatedAt"),
        "document.generatedAt",
    )
    try:
        generated_timestamp = datetime.fromisoformat(
            generated_at.replace("Z", "+00:00")
        )
        if generated_timestamp.tzinfo is None:
            fail("document.generatedAt must include a timezone")
        os.environ["SOURCE_DATE_EPOCH"] = str(int(generated_timestamp.timestamp()))
    except ValueError:
        fail("document.generatedAt must be an ISO 8601 timestamp")
    if validation.get("passed") is not True:
        fail("document must have passed machine validation")
    if report.get("aiGenerated") is not True or report.get("humanReviewed") is not False:
        fail("document review identity is inconsistent")
    if snapshot.get("collectorId") != "rrc25":
        fail("document must be bound to the only supported collector rrc25")

    pdfmetrics.registerFont(TTFont(FONT_NAME, font_path))
    styles = make_styles()
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=MARGIN_X,
        leftMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title=title,
        author="Domeye 国家中断 Agent",
        subject="RRC25 BGP 控制面观测报告",
        creator="Domeye Country Outage Agent PDF Renderer",
        pageCompression=1,
        invariant=1,
        lang="zh-CN",
    )

    story: list[Any] = []
    story.append(Table(
        [[paragraph("DOMEYE / COUNTRY OUTAGE", styles["table_head"])]],
        colWidths=[CONTENT_WIDTH],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NAVY),
            ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ]),
    ))
    story.append(Spacer(1, 7 * mm))
    story.append(paragraph(title, styles["title"]))
    story.append(paragraph(draft.get("subtitle", ""), styles["subtitle"]))
    story.append(Table(
        [[paragraph(
            "本报告由 AI 生成并经机器校验，未经人工审核。"
            "报告只描述 RRC25 的 BGP 控制面观测，不代表用户、业务或全国数据面的实际影响。",
            styles["notice"],
        )]],
        colWidths=[CONTENT_WIDTH],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PALE_ORANGE),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#E6B76F")),
            ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
        ]),
    ))

    summary = require_object(draft.get("summary"), "document.draft.summary")
    summary_parts: list[Any] = [
        paragraph(summary.get("text", ""), styles["summary"]),
    ]
    summary_evidence = evidence_text(summary.get("evidenceRefs"))
    if summary_evidence:
        summary_parts.extend([
            Spacer(1, 2 * mm),
            paragraph(f"证据定位：{summary_evidence}", styles["evidence"]),
        ])
    story.append(Spacer(1, 6 * mm))
    story.append(Table(
        [[summary_parts]],
        colWidths=[CONTENT_WIDTH],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PALE_TEAL),
            ("LINEBEFORE", (0, 0), (0, -1), 3, TEAL),
            ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 4 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
        ]),
    ))

    story.append(paragraph("最值得关注的数字", styles["h2"]))
    highlights = draft.get("highlights")
    if not isinstance(highlights, list):
        fail("document.draft.highlights must be an array")
    highlight_rows: list[list[Any]] = [[
        paragraph("指标", styles["table_head"]),
        paragraph("观测结果", styles["table_head"]),
        paragraph("证据定位", styles["table_head"]),
    ]]
    for index, raw_item in enumerate(highlights):
        item = require_object(raw_item, f"document.draft.highlights[{index}]")
        highlight_rows.append([
            paragraph(item.get("label", ""), styles["table"]),
            paragraph(item.get("value", ""), styles["table_value"]),
            paragraph(evidence_text(item.get("evidenceRefs")) or "未提供", styles["table_evidence"]),
        ])
    highlight_table = Table(
        highlight_rows,
        colWidths=[52 * mm, 43 * mm, CONTENT_WIDTH - 95 * mm],
        repeatRows=1,
        splitByRow=1,
        hAlign="LEFT",
    )
    highlight_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, PALE_BLUE]),
        ("GRID", (0, 0), (-1, -1), 0.45, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.7 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.7 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.4 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4 * mm),
    ]))
    story.append(highlight_table)

    sections = draft.get("sections")
    if not isinstance(sections, list):
        fail("document.draft.sections must be an array")
    indexed_sections = [
        (
            section_index,
            require_object(
                raw_section,
                f"document.draft.sections[{section_index}]",
            ),
        )
        for section_index, raw_section in enumerate(sections)
    ]
    ordered_sections = [
        item for item in indexed_sections if item[1].get("id") == "key_numbers"
    ] + [
        item for item in indexed_sections if item[1].get("id") != "key_numbers"
    ]
    for section_index, section in ordered_sections:
        if section.get("id") == "key_numbers":
            story.append(Spacer(1, 4 * mm))
        else:
            story.append(paragraph(section.get("title", ""), styles["h2"]))
        paragraphs = section.get("paragraphs")
        if not isinstance(paragraphs, list):
            fail(
                f"document.draft.sections[{section_index}].paragraphs "
                "must be an array"
            )
        for paragraph_index, raw_paragraph in enumerate(paragraphs):
            item = require_object(
                raw_paragraph,
                (
                    f"document.draft.sections[{section_index}]"
                    f".paragraphs[{paragraph_index}]"
                ),
            )
            paragraph_group: list[Any] = [
                paragraph(item.get("text", ""), styles["body"]),
            ]
            refs = evidence_text(item.get("evidenceRefs"))
            if refs:
                paragraph_group.append(
                    paragraph(f"证据定位：{refs}", styles["evidence"]),
                )
            story.append(Table(
                [[paragraph_group]],
                colWidths=[CONTENT_WIDTH],
                splitByRow=1,
                style=TableStyle([
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]),
            ))

    story.append(paragraph("不能仅凭本报告回答的问题", styles["h2"]))
    unknowns = draft.get("unknowns")
    if not isinstance(unknowns, list):
        fail("document.draft.unknowns must be an array")
    for item in unknowns:
        story.append(Paragraph(f"•&nbsp;&nbsp;{safe_text(item)}", styles["bullet"]))

    story.append(paragraph("制品与证据说明", styles["h2"]))
    mutable_alias_rows = []
    if (
        model.get("adapter") == "pi-sdk"
        and model.get("runtimeIdentity") == "formal"
        and model.get("modelRevisionKind") == "mutable_alias"
        and model.get("immutableRevisionAvailable") is False
        and isinstance(model.get("limitation"), str)
        and model.get("limitation")
        and isinstance(model.get("certificationValidUntil"), str)
        and model.get("certificationValidUntil")
        and isinstance(model.get("certifiedScenarioSetId"), str)
        and model.get("certifiedScenarioSetId")
        and isinstance(model.get("certifiedInputScope"), str)
        and model.get("certifiedInputScope")
    ):
        mutable_alias_rows = [
            ("模型引用类型", "可变别名（mutable_alias）"),
            ("不可变权重 revision", "供应方未提供"),
            (
                "模型身份限制",
                model.get("limitation", ""),
            ),
            (
                "认证有效至",
                model.get("certificationValidUntil", ""),
            ),
            (
                "认证场景集",
                model.get("certifiedScenarioSetId", ""),
            ),
            (
                "认证输入范围",
                model.get("certifiedInputScope", ""),
            ),
        ]

    meta_rows = [
        ("报告制品", artifact_id),
        ("报告内容摘要", report.get("reportContentSha256", "")),
        ("事实集合", report.get("factSetId", "")),
        (
            "固定快照",
            (
                f"{snapshot.get('publicationId', '')} / "
                f"revision {snapshot.get('revision', '')}"
            ),
        ),
        ("事件", snapshot.get("incidentId", "")),
        (
            "国家",
            (
                f"{event.get('countryName', event.get('country_name', ''))} "
                f"({event.get('countryCode', event.get('country_code', ''))})"
            ).strip(),
        ),
        ("观测源", snapshot.get("collectorId", "")),
        ("数据截止", snapshot.get("dataThrough") or "未提供"),
        (
            "模型",
            (
                f"{model.get('provider', '')}/"
                f"{model.get('model', '')}/"
                f"{model.get('modelVersion', '')}"
            ),
        ),
        *mutable_alias_rows,
        ("报告规范", report.get("reportSpecificationVersion", "")),
        ("项目知识", report.get("projectKnowledgeVersion", "")),
        ("校验规则", report.get("validatorRulesVersion", "")),
        ("Skill 包摘要", report.get("skillBundleSha256", "")),
        ("生成时间", generated_at),
        ("机器校验", "通过" if validation.get("passed") is True else "未通过"),
        ("审核状态", "AI 生成；未经人工审核"),
    ]
    meta_table = Table(
        [
            [
                paragraph(key, styles["meta_key"]),
                paragraph(value, styles["meta_value"]),
            ]
            for key, value in meta_rows
        ],
        colWidths=[35 * mm, CONTENT_WIDTH - 35 * mm],
        splitByRow=1,
        hAlign="LEFT",
    )
    meta_table.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [PALE_BLUE, WHITE]),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
    ]))
    story.append(meta_table)

    def page_footer(canvas: Any, doc: SimpleDocTemplate) -> None:
        footer(canvas, doc, artifact_id)

    document.build(
        story,
        onFirstPage=page_footer,
        onLaterPages=page_footer,
    )
    pdf = output.getvalue()
    page_count = len(PdfReader(io.BytesIO(pdf)).pages)
    if page_count > MAX_PDF_PAGES:
        fail(f"generated PDF has {page_count} pages; maximum is {MAX_PDF_PAGES}")
    return pdf


def main() -> None:
    try:
        payload = json.load(sys.stdin.buffer)
        if not isinstance(payload, dict):
            fail("stdin JSON must be an object")
        pdf = render(payload)
        sys.stdout.buffer.write(pdf)
        sys.stdout.buffer.flush()
    except SystemExit:
        raise
    except Exception as exc:  # Fail closed without emitting a partial PDF.
        fail(str(exc))


if __name__ == "__main__":
    main()
