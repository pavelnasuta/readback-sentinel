---
name: readback-sentinel
description: "Use when triaging narrated audio against source text safely."
---

# Readback Sentinel — local narration QC preview

Используй этот скилл для локального предварительного анализа одной записи по печатному тексту. Сначала загрузи `audio-qc-systems`: ASR и эвристики создают кандидатов, а не доказанные ошибки.

## Жёсткие границы

- Не отправляй машинные кандидаты диктору без ручного подтверждения.
- Не называй P95/P10 измеренным SNR и не делай вывод о шуме: noise detector отключён до калибровки.
- Не называй исполнение выразительным по F0; просодика только диагностическая.
- Не используй `STT_ARTIFACT` как автоматическое решение на основании строковой похожести или ASR confidence.
- Не меняй пороги без размеченного eval-набора.
- Не помещай клиентские аудио, тексты, транскрипты и глоссарии в Git.
- `--reference` не реализован.

## Точный исполняемый источник

До установки релиза запускай репозиторную версию:

```bash
cd "$HOME/Documents/GitHub/readback-sentinel"
uv sync --dev
uv run python scripts/run_qc.py \
  --audio "/absolute/recording.wav" \
  --text "/absolute/source.pdf" \
  --output "/absolute/qc_runs" \
  --model small \
  --tag chapter_01
```

Не подменяй этот путь другой копией скрипта без проверки версии и хеша.

## Что создаёт анализ

Каждая попытка изолирована в `analysis_<tag>_<run_id>/`. Внутри:

- `run_manifest_<tag>.json`: `STARTED` → `COMPLETED`/`FAILED`, входные SHA-256, версии, артефакты;
- `report_<tag>.json` и `.pdf`: предварительный отчёт, `review_binding`, кандидаты;
- `triage_<tag>.docx`, `triage_markers_<tag>.csv`: только для куратора;
- transcript JSON/TXT и диагностический график просодики.

Не считай `COMPLETED` подтверждением качества: это лишь техническое завершение локального анализа.

## Ручное подтверждение без повторного ASR

Создай JSON из просмотренного отчёта:

```json
{
  "review_binding": "<из report JSON>",
  "confirmed_flag_ids": ["flag_0000"]
}
```

Затем:

```bash
uv run python scripts/run_qc.py \
  --audio "/absolute/recording.wav" \
  --text "/absolute/source.pdf" \
  --output "/absolute/qc_runs" \
  --tag chapter_01 \
  --review-report "/absolute/analysis_.../report_chapter_01.json" \
  --confirmed-flags "/absolute/confirmation.json"
```

Confirmation mode не вызывает ASR. Он сверяет input SHA-256, целостность report, `review_binding`, ID и severity. Manifest наследует модель, язык и provenance конфигурации из просмотренного отчёта, а не из CLI-defaults. Только после этого новая папка `confirmation_<tag>_<run_id>/` получает narrator-facing DOCX и Audition CSV.

## Механизм v0.4.0

1. Извлечь PDF/DOCX/TXT; DOCX-таблицы включаются.
2. Распознать локально faster-whisper и сохранить сегменты.
3. Нормализовать Unicode, `ё/е`, дефисы и только корректные составные числа; перечисления не складывать.
4. Найти обе границы фрагмента. При неоднозначности — `ALIGNMENT_FAILED`.
5. Сравнить только точное найденное окно. Любое ASR-расхождение — `REVIEW`, не автоматический вердикт.
6. Привязать диапазон флага ко всем затронутым ASR-сегментам.
7. Проверить глоссарий консервативно по целым одно- и многословным вариантам; это кандидат на прослушивание, не фонетическое доказательство.
8. Проверить нативные каналы/пики, затем строить mono/16 kHz диагностический поток; выделить тихие интервалы.
9. Посчитать F0 чанками, использовать устойчивые P5–P95 и непрерывную voiced-duration; не выдавать положительную оценку исполнения.
10. Создать предварительные артефакты и связанный review snapshot.

Подробности: `references/pipeline_spec.md`. Ограничения и команды владельцу: `README.md`.
