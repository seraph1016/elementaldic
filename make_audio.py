#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_audio.py — 단어장 음원 일괄 생성기

words.js 의 단어를 읽어 미국식/영국식 mp3를 만들어 audio/ 폴더에 저장합니다.
파일 이름은 앱이 찾는 규칙과 같습니다:  audio/apple_us.mp3 , audio/apple_uk.mp3

──────────────────────────────────────────────
설치
──────────────────────────────────────────────
  # 방법 A (권장) — 키 없이 무료, 마이크로소프트 뉴럴 음성
  pip install edge-tts

  # 방법 B — 구글 클라우드 TTS (월 100만 자 무료, 계정 설정 필요)
  pip install google-cloud-texttospeech
  export GOOGLE_APPLICATION_CREDENTIALS=/경로/키파일.json

──────────────────────────────────────────────
사용법
──────────────────────────────────────────────
  # 무엇이 만들어질지 먼저 확인
  python make_audio.py words.js --dry-run

  # 앞의 40개만 시험 삼아 만들기
  python make_audio.py words.js --limit 40

  # 전체 (923단어 × 2 = 약 1,850개 파일)
  python make_audio.py words.js

  # 남자 목소리로
  python make_audio.py words.js --us-voice en-US-AndrewNeural --uk-voice en-GB-RyanNeural

만들어진 audio 폴더를 HTML 파일과 같은 위치에 두면 앱이 자동으로 사용합니다.
중간에 멈춰도 다시 실행하면 없는 파일만 이어서 만듭니다.
"""

import argparse
import asyncio
import csv
import json
import re
import sys
from pathlib import Path

EDGE_VOICES = {"us": "en-US-AvaNeural", "uk": "en-GB-SoniaNeural"}
GOOGLE_VOICES = {"us": ("en-US", "en-US-Neural2-F"), "uk": ("en-GB", "en-GB-Neural2-A")}


def load_sentences(path: Path):
    """words.js 에서 (단어, 예문) 쌍을 뽑아냅니다. 예문 음원용."""
    text = path.read_text(encoding="utf-8")
    start, end = text.index("["), text.rindex("]")
    out = []
    for item in json.loads(text[start:end + 1]):
        if item.get("en"):
            out.append((item["w"], item["en"]))
    return out


def load_words(path: Path):
    """words.js / html / txt / csv / json 에서 단어를 뽑아냅니다."""
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    words = []

    if suffix in (".js", ".html", ".htm"):
        words = re.findall(r'"w"\s*:\s*"([^"]+)"', text)
        if not words:
            words = re.findall(r'\bw\s*:\s*"([^"]+)"', text)
    elif suffix == ".json":
        for item in json.loads(text):
            if isinstance(item, str):
                words.append(item)
            elif isinstance(item, dict):
                for key in ("w", "word", "단어"):
                    if key in item:
                        words.append(str(item[key]))
                        break
    elif suffix == ".csv":
        for row in csv.reader(text.splitlines()):
            if row and row[0].strip():
                words.append(row[0].strip())
    else:
        words = [line.strip() for line in text.splitlines()]

    seen, out = set(), []
    for w in words:
        # "a(an)" 처럼 괄호가 붙은 표제어는 괄호를 떼고 읽습니다
        spoken = re.sub(r"\(.*?\)", "", w).strip()
        if not spoken or not re.fullmatch(r"[A-Za-z][A-Za-z'\- ]*", spoken):
            continue
        if w.lower() in seen:
            continue
        seen.add(w.lower())
        out.append((w, spoken))
    return out


def out_path(outdir: Path, word: str, accent: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9'\-]+", "_", word)
    return outdir / f"{safe}_{accent}.mp3"   # accent 가 "ex" 면 예문 음원


async def run_edge(jobs, voices, rate, concurrency):
    import edge_tts

    sem = asyncio.Semaphore(concurrency)
    done, failed = [], []

    async def one(spoken, accent, dest):
        async with sem:
            for attempt in range(3):
                try:
                    tts = edge_tts.Communicate(spoken, voices[accent], rate=rate)
                    await tts.save(str(dest))
                    if dest.stat().st_size > 0:
                        done.append(dest)
                        return
                    dest.unlink(missing_ok=True)
                except Exception as e:
                    if attempt == 2:
                        failed.append((spoken, accent, str(e)))
                    else:
                        await asyncio.sleep(1.5 * (attempt + 1))

    total = len(jobs)
    step = max(1, total // 20)
    tasks = []
    for i, (spoken, accent, dest) in enumerate(jobs):
        tasks.append(one(spoken, accent, dest))
        if (i + 1) % step == 0:
            pass
    # 진행률 표시를 위해 묶음 단위로 처리
    CH = 100
    for i in range(0, total, CH):
        await asyncio.gather(*tasks[i:i + CH])
        print(f"  … {min(i+CH, total)}/{total}개 처리 (성공 {len(done)}, 실패 {len(failed)})")
    return done, failed


def run_google(jobs, speed):
    from google.cloud import texttospeech

    client = texttospeech.TextToSpeechClient()
    cfg = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3, speaking_rate=speed
    )
    done, failed = [], []
    for n, (spoken, accent, dest) in enumerate(jobs, 1):
        lang, name = GOOGLE_VOICES[accent]
        try:
            res = client.synthesize_speech(
                input=texttospeech.SynthesisInput(text=spoken),
                voice=texttospeech.VoiceSelectionParams(language_code=lang, name=name),
                audio_config=cfg,
            )
            dest.write_bytes(res.audio_content)
            done.append(dest)
        except Exception as e:
            failed.append((spoken, accent, str(e)))
        if n % 100 == 0:
            print(f"  … {n}/{len(jobs)}개 처리")
    return done, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", default="words.js")
    ap.add_argument("-o", "--outdir", default="audio")
    ap.add_argument("--engine", choices=["edge", "google"], default="edge")
    ap.add_argument("--accents", default="us,uk")
    ap.add_argument("--what", choices=["words", "sentences", "both"], default="both",
                    help="words=단어만, sentences=예문만, both=둘 다 (기본)")
    ap.add_argument("--us-voice", default=EDGE_VOICES["us"])
    ap.add_argument("--uk-voice", default=EDGE_VOICES["uk"])
    ap.add_argument("--rate", default="-10%", help="edge 속도 (기본 -10%%)")
    ap.add_argument("--speed", type=float, default=0.9, help="google 속도")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.exists():
        sys.exit(f"파일을 찾을 수 없습니다: {src}")

    words = load_words(src)
    if args.limit:
        words = words[: args.limit]
    if not words:
        sys.exit("단어를 하나도 읽지 못했습니다.")

    accents = [a.strip() for a in args.accents.split(",") if a.strip() in ("us", "uk")]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    jobs, skipped = [], 0
    if args.what in ("words", "both"):
        for label, spoken in words:
            for a in accents:
                dest = out_path(outdir, label, a)
                if dest.exists() and dest.stat().st_size > 0 and not args.force:
                    skipped += 1
                    continue
                jobs.append((spoken, a, dest))

    sentences = []
    if args.what in ("sentences", "both") and src.suffix.lower() == ".js":
        sentences = load_sentences(src)
        if args.limit:
            sentences = sentences[: args.limit]
        for label, text in sentences:          # 예문은 미국 발음으로만 만듭니다
            dest = out_path(outdir, label, "ex")
            if dest.exists() and dest.stat().st_size > 0 and not args.force:
                skipped += 1
                continue
            jobs.append((text, "us", dest))

    print(f"\n단어 {len(words)}개 · 발음 {'/'.join(accents)} · 예문 {len(sentences)}개")
    print(f"만들 파일 {len(jobs)}개, 이미 있어 건너뜀 {skipped}개")
    print(f"저장 위치 {outdir.resolve()} · 엔진 {args.engine}\n")

    if args.dry_run:
        for spoken, a, d in jobs[:10]:
            print(f"  · {d.name}  ({spoken} / {a})")
        if len(jobs) > 10:
            print(f"  · … 외 {len(jobs)-10}개")
        print("\n--dry-run 이라 실제로 만들지 않았습니다.")
        return
    if not jobs:
        print("새로 만들 파일이 없습니다.")
        return

    if args.engine == "edge":
        voices = {"us": args.us_voice, "uk": args.uk_voice}
        done, failed = asyncio.run(run_edge(jobs, voices, args.rate, args.concurrency))
    else:
        done, failed = run_google(jobs, args.speed)

    mb = sum(p.stat().st_size for p in done if p.exists()) / 1024 / 1024
    print(f"\n완료: {len(done)}개 생성 ({mb:.1f} MB), 실패 {len(failed)}개")
    if failed:
        log = outdir / "failed.txt"
        log.write_text("\n".join(f"{w}\t{a}\t{e}" for w, a, e in failed), encoding="utf-8")
        print(f"실패 목록: {log} — 다시 실행하면 실패분만 재시도합니다.")
    print(f"\n{outdir} 폴더를 HTML 과 같은 위치에 두면 앱이 바로 사용합니다.")


if __name__ == "__main__":
    main()
