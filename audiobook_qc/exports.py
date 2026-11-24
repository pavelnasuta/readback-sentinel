"""Report exports: JSON, PDF, DOCX and Audition CSV."""
from __future__ import annotations

import csv
import math
import os
import re
import tempfile
from collections.abc import Sequence
from numbers import Real
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import librosa
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from docx import Document as DocxDocument
from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def write_marker_csv(flags: Sequence[dict[str, Any]], out_path: str, tag: str, stem: str) -> Path:
    path = Path(out_path) / f"{stem}_{tag}.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Start", "Duration", "Type", "Comment"])
        for fl in flags:
            ts = float(fl["timestamp"])
            h = int(ts // 3600)
            m = int((ts % 3600) // 60)
            s = ts % 60
            duration = max(0.0, float(fl.get("timestamp_end", ts)) - ts)
            w.writerow([
                fl["id"],
                f"{h:02d}:{m:02d}:{s:06.3f}",
                round(duration, 3),
                "Cue",
                f"{fl['category']}: {fl['description'][:80]}",
            ])
    print(f"[CSV] {path} ({len(flags)} markers)", flush=True)
    return path


_CUE_HEADLINES = {
    "triage_markers": "REM READBACK_SENTINEL PRELIMINARY_TRIAGE_REQUIRES_HUMAN_REVIEW",
    "audition_markers": "REM READBACK_SENTINEL HUMAN_CONFIRMED_ONLY",
}


def _cue_safe_text(value: object) -> str:
    """Keep quoted CUE fields single-line and free of quote delimiters."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value)).replace('"', "'")


def _cue_timestamp(timestamp: object) -> tuple[float, str]:
    if isinstance(timestamp, bool) or not isinstance(timestamp, Real):
        raise ValueError("CUE marker timestamp must be a finite non-negative numeric value.")
    seconds = float(timestamp)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("CUE marker timestamp must be a finite non-negative numeric value.")
    total_frames_value = seconds * 75
    if not math.isfinite(total_frames_value):
        raise ValueError("CUE marker timestamp is too large to convert to 75 fps.")
    total_frames = math.floor(total_frames_value)
    minutes, remainder = divmod(total_frames, 60 * 75)
    seconds_part, frames = divmod(remainder, 75)
    return seconds, f"{minutes:02d}:{seconds_part:02d}:{frames:02d}"


def write_cue(
    flags: Sequence[dict[str, Any]],
    out_path: str | Path,
    tag: str,
    base_name: str,
    audio_filename: str | Path,
) -> Path:
    """Write a standard CUE listening map without candidate body content."""
    try:
        headline = _CUE_HEADLINES[base_name]
    except KeyError as error:
        raise ValueError(f"Unsupported CUE base name: {base_name}") from error
    if not isinstance(tag, str) or not tag or not re.fullmatch(r"[\w.-]+", tag, flags=re.UNICODE):
        raise ValueError("CUE tag may contain only letters, digits, underscore, dot, and hyphen.")

    emitted: list[tuple[float, str, dict[str, Any], str]] = []
    for flag in flags:
        if flag.get("severity") not in {"CRITICAL", "REVIEW"}:
            continue
        timestamp, index = _cue_timestamp(flag.get("timestamp"))
        identifier = str(flag["id"])
        emitted.append((timestamp, identifier, flag, index))
    emitted.sort(key=lambda marker: (marker[0], marker[1]))
    if len(emitted) > 99:
        raise ValueError("Standard CUE supports at most 99 emitted markers.")

    audio_name = _cue_safe_text(Path(audio_filename).name)
    lines = [
        headline,
        'PERFORMER "Readback Sentinel"',
        f'TITLE "Review markers — {audio_name}"',
        f'FILE "{audio_name}" WAVE',
    ]
    for track_number, (_timestamp, identifier, flag, index) in enumerate(emitted, start=1):
        severity = _cue_safe_text(flag["severity"])
        category = _cue_safe_text(flag["category"])
        safe_identifier = _cue_safe_text(identifier)
        lines.extend([
            f"  TRACK {track_number:02d} AUDIO",
            f'    TITLE "[{severity}] {category} — {safe_identifier}"',
            "    REM HUMAN_REVIEW_REQUIRED",
            f"    INDEX 01 {index}",
        ])

    path = Path(out_path) / f"{base_name}_{tag}.cue"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write("\n".join(lines) + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    print(f"[CUE] {path} ({len(emitted)} markers)", flush=True)
    return path


def _write_flag_table(doc: DocxDocument, flags: Sequence[dict[str, Any]]) -> None:
    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    hdr[0].text = "Таймкод"
    hdr[1].text = "Категория"
    hdr[2].text = "Описание"
    hdr[3].text = "Действие"
    for fl in flags:
        row = table.add_row().cells
        ts = float(fl["timestamp"])
        m = int(ts // 60)
        s = ts % 60
        row[0].text = f"{m:02d}:{s:05.2f}"
        row[1].text = str(fl["category"])
        row[2].text = str(fl["description"])
        row[3].text = str(fl.get("action", ""))


def write_triage_docx(flags: Sequence[dict[str, Any]], out_path: str, tag: str, audio_name: str) -> Path:
    path = Path(out_path) / f"triage_{tag}.docx"
    doc = DocxDocument()
    doc.add_heading("Предварительный лист триажа", 0)
    doc.add_paragraph(f"Файл: {audio_name}")
    doc.add_paragraph("Статус: кандидаты автоматической проверки.")
    doc.add_paragraph("Не направлять диктору до ручного подтверждения флагов.")
    if flags:
        _write_flag_table(doc, flags)
    else:
        doc.add_paragraph("Кандидатов CRITICAL или REVIEW нет.")
    doc.save(str(path))
    print(f"[DOCX] {path} ({len(flags)} triage candidates)", flush=True)
    return path


def write_confirmed_docx(flags: Sequence[dict[str, Any]], out_path: str, tag: str, audio_name: str) -> Path:
    path = Path(out_path) / f"перепись_{tag}.docx"
    doc = DocxDocument()
    doc.add_heading("Лист перезаписи", 0)
    doc.add_paragraph(f"Файл: {audio_name}")
    doc.add_paragraph("Статус: флаги подтверждены ручным триажем.")
    doc.add_paragraph("Диктор: ______________")
    doc.add_paragraph("Дата: ______________")
    if flags:
        _write_flag_table(doc, flags)
    else:
        doc.add_paragraph("Нет подтверждённых замечаний для перезаписи.")
    doc.save(str(path))
    print(f"[DOCX] {path} ({len(flags)} confirmed flags)", flush=True)
    return path


def _register_reportlab_fonts() -> None:
    font_base = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
    pdfmetrics.registerFont(TTFont("DJ", os.path.join(font_base, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DJB", os.path.join(font_base, "DejaVuSans-Bold.ttf")))


def write_pdf(report_data: dict[str, Any], out_path: str, tag: str) -> Path:
    pdf_path = os.path.join(out_path, f"report_{tag}.pdf")
    _register_reportlab_fonts()

    cell = ParagraphStyle("cell", fontName="DJ", fontSize=8.5, leading=10.5)
    cellh = ParagraphStyle("cellh", fontName="DJB", fontSize=9, leading=11, textColor=colors.white)
    title = ParagraphStyle(
        "t", fontName="DJB", fontSize=18, leading=22, spaceAfter=4,
        textColor=colors.HexColor("#1a1a2e"),
    )
    h1 = ParagraphStyle(
        "h1", fontName="DJB", fontSize=13, leading=16, spaceBefore=10, spaceAfter=5,
        textColor=colors.HexColor("#0f3460"),
    )
    body = ParagraphStyle(
        "b", fontName="DJ", fontSize=9, leading=12, alignment=TA_JUSTIFY, spaceAfter=4,
    )
    small = ParagraphStyle("s", fontName="DJ", fontSize=7.5, leading=9.5, textColor=colors.HexColor("#555"))

    def tbl(rows: list[list[str]], widths: list[float]):
        new_rows = []
        for ri, row in enumerate(rows):
            new_rows.append([Paragraph(escape(str(c)), cellh if ri == 0 else cell) for c in row])
        t = Table(new_rows, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f3460")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f4f8")]),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
    )
    E: list = []
    def P(t: str, st=body):
        E.append(Paragraph(t, st))

    R = report_data
    P("Предварительный отчёт AI QC", title)
    P(
        f"Файл: <b>{escape(str(R['audio_name']))}</b> &nbsp;|&nbsp; Длительность: <b>{R['duration']:.1f} мин</b> &nbsp;|&nbsp; Тег: {escape(str(R['tag']))}",
        small,
    )
    P(
        "<b>Статус:</b> кандидаты для ручного триажа. Ни один флаг из этого отчёта не является автоматическим заданием диктору.",
        body,
    )
    E.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#bbbbbb"), spaceBefore=4, spaceAfter=8))

    # 1. Summary
    P("1. Резюме", h1)
    matched_display = "не определено" if R["matched_pct"] is None else f"{R['matched_pct']:.1f}% совпадения"
    ratio = R["acoustics"].get("amplitude_percentile_ratio_db")
    ratio_display = "не определено" if ratio is None else f"{ratio:.1f} дБ (диагностика)"
    res = [
        ["Параметр", "Результат", "Статус"],
        ["Длительность", f"{R['duration']:.1f} мин ({R['n_words']} слов, {R['n_seg']} сегментов)", "справочно"],
        ["Сверка текста", matched_display, R["verdict_accuracy"]],
        ["Шум", ratio_display, R["verdict_noise"]],
        ["Пики выше порога", f"{R['acoustics']['n_clipped']} сэмплов ({R['acoustics']['frac_clipped']*100:.4f}%)", R["verdict_clip"]],
        ["Тихие интервалы", f"{R['acoustics']['silence_count']} интервалов", R["verdict_pause"]],
        ["Просодия", R["prosody"].get("status", "NOT_EVALUATED"), R["verdict_intonation"]],
        ["Флагов всего", str(R["n_flags"]), "кандидаты"],
        ["CRITICAL", str(R["n_critical"]), "требует триажа"],
        ["REVIEW", str(R["n_review"]), "требует триажа"],
        ["STT_ARTIFACT", str(R["n_artifact"]), "не передавать"],
    ]
    E.append(tbl(res, [42 * mm, 104 * mm, 28 * mm]))
    E.append(Spacer(1, 8))

    # 2. Acoustics
    P("2. Акустические метрики", h1)
    ac = R["acoustics"]
    ratio = ac.get("amplitude_percentile_ratio_db")
    ratio_text = "не определено" if ratio is None else f"{ratio:.1f} дБ"
    clip_threshold = float(ac.get("clip_sample_amplitude", 0.99))
    ac_rows = [
        ["Метрика", "Значение", "Статус"],
        ["Исходный файл", f"{ac['source_sample_rate']} Гц / {ac['source_channels']} канал(а)", "нативные данные"],
        ["Поток анализа", f"{ac['analysis_sample_rate']} Гц / моно", "служебное преобразование"],
        ["Пик / порог", f"{ac['peak']:.3f} / {clip_threshold:.3f}", "есть превышение" if ac["n_clipped"] else "превышений не обнаружено"],
        ["Сэмплы выше порога", f"{ac['n_clipped']} ({ac['frac_clipped']*100:.4f}%)", R["verdict_clip"]],
        ["RMS общий", f"{ac['rms_all']:.5f}", "диагностическая величина"],
        ["Отношение P95/P10", ratio_text, "не является измеренным SNR"],
        ["Оценка шума", "методика не откалибрована", R["verdict_noise"]],
        ["Тишина в начале/конце", f"{ac['lead_silence']:.2f} с / {ac['trail_silence']:.2f} с", R["verdict_pause"]],
    ]
    E.append(tbl(ac_rows, [48 * mm, 58 * mm, 58 * mm]))
    E.append(Spacer(1, 8))

    # 3. Prosody
    P("3. Просодия (справочно)", h1)
    pr = R["prosody"]
    if pr.get("status") in {"NOT_EVALUATED", "INSUFFICIENT_EVIDENCE"}:
        P("Просодия не оценена: недостаточно надёжно озвученных кадров.", body)
    else:
        P(
            f"Медиана F0: <b>{pr['pitch_median']} Гц</b>, устойчивый диапазон P5–P95: "
            f"<b>{pr['pitch_range_semitones']} полутонов</b> (от {pr['pitch_min']} до {pr['pitch_max']} Гц), "
            f"озвучено ~{pr['voiced_pct']}% времени. Метрики справочные и не доказывают художественную выразительность.",
            body,
        )
        if pr.get("monotony_flag"):
            P("<b>Кандидат REVIEW:</b> устойчивый диапазон F0 ниже настроенного порога на длинном озвученном участке.", body)
        else:
            P("Автоматический кандидат на монотонность не сформирован; это не является положительной оценкой исполнения.", body)

    prosody_png = os.path.join(out_path, f"prosody_{tag}.png")
    if os.path.exists(prosody_png):
        E.append(Image(prosody_png, width=170 * mm, height=60 * mm))
    E.append(PageBreak())

    # 4. Flags
    P("4. Флаги для ручной проверки", h1)
    P(f"Всего флагов: {R['n_flags']} (CRITICAL: {R['n_critical']}, REVIEW: {R['n_review']}, STT_ARTIFACT: {R['n_artifact']})", body)

    review_flags = [f for f in R["flags"] if f["severity"] in ("CRITICAL", "REVIEW")]
    if review_flags:
        flag_rows = [["Таймкод", "Sev", "Категория", "Описание"]]
        for fl in review_flags[:25]:
            flag_rows.append([
                f"{fl['timestamp']:.1f}",
                fl["severity"][:4],
                fl["category"][:20],
                fl["description"][:60],
            ])
        E.append(tbl(flag_rows, [20 * mm, 16 * mm, 34 * mm, 104 * mm]))
    else:
        P("Кандидатов CRITICAL или REVIEW автоматически не сформировано. Это не подтверждает отсутствие дефектов; требуется предусмотренный процесс выборочной проверки.", body)

    E.append(Spacer(1, 8))
    P("5. Рекомендации", h1)
    if R["alignment"]["status"] == "ALIGNMENT_FAILED":
        P(
            "Текстовые расхождения не рассчитаны: система не смогла надёжно привязать аудио к исходнику. "
            "Сначала проверить пару «аудио — текст», затем повторить запуск.",
            body,
        )
    elif R["n_critical"] == 0:
        P(
            f"Автоматических кандидатов CRITICAL нет. До закрытия файла провести ручную проверку "
            f"{R['n_review']} зон REVIEW и выборочную переслушку остального материала.",
            body,
        )
    else:
        P(
            f"Обнаружено {R['n_critical']} критических кандидатов и {R['n_review']} зон REVIEW. "
            "Сначала провести ручной триаж; только подтверждённые ID можно передать через --confirmed-flags "
            "для создания листа перезаписи и финальных Audition-маркеров.",
            body,
        )

    doc.build(E)
    print(f"[PDF] {pdf_path}", flush=True)
    return Path(pdf_path)


def save_prosody_figure(
    audio_path: str,
    out_path: str,
    *,
    analysis: dict[str, Any] | None = None,
) -> Path:
    if analysis is None:
        y, sr = librosa.load(audio_path, sr=16000, mono=True)
        f0, _voiced, _ = librosa.pyin(
            y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6"),
            sr=sr, frame_length=2048, hop_length=512,
        )
        times = librosa.times_like(f0, sr=sr, hop_length=512)
        rmse = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
        rms_times = librosa.times_like(rmse, sr=sr, hop_length=512)
    else:
        f0 = analysis.get("_f0", [])
        times = analysis.get("_f0_times", [])
        rmse = analysis.get("_rms", [])
        rms_times = analysis.get("_rms_times", [])

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 5), sharex=True)
    ax1.plot(times, f0, color="#1f77b4", lw=0.7)
    finite_f0 = [float(value) for value in f0 if math.isfinite(float(value))]
    axis_max = max(400.0, max(finite_f0, default=0.0) * 1.1)
    ax1.set_ylim(0, axis_max)
    ax1.set_ylabel("Тон, Гц")
    ax1.set_title("Диагностический контур высоты тона")
    ax2.plot(rms_times, rmse, color="#d62728", lw=0.7)
    ax2.set_ylabel("RMS")
    ax2.set_xlabel("Время, сек")
    ax2.set_title("Диагностическая энергия сигнала")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"[FIG] {out_path}", flush=True)
    return Path(out_path)
