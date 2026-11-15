#!/bin/sh
# Build one episode: news -> script -> voice -> video.   run_episode.sh [YYYY-MM-DD]
# Publishing (YouTube, podcast) is done by separate steps so a publish failure
# never loses the built episode.
set -eu
DATE="${1:-$(TZ=Asia/Kolkata date +%F)}"
OUT="out/$DATE"
mkdir -p "$OUT"

if [ -n "${BRIEF_FILE:-}" ]; then
  echo "== using ready-made script $BRIEF_FILE (news and script steps skipped)"
  cp "$BRIEF_FILE" "$OUT/brief.json"
else
  echo "== 1/5 collect news"
  python pipeline/collect.py --outdir "$OUT"
  echo "== 2/5 write script"
  python pipeline/write_script.py --outdir "$OUT"
fi
echo "== 3/5 narrate"
python voice/speak.py --brief "$OUT/brief.json" --outdir "$OUT"
echo "== 4/5 clean and master audio"
sh voice/master.sh "$OUT/voice-raw.wav" "$OUT/voice.wav"
echo "== 5/5 render video"
python video/render.py --brief "$OUT/brief.json" --timings "$OUT/timings.json" \
  --audio "$OUT/voice.wav" --out "$OUT/video.mp4"
echo "episode built in $OUT"
