#!/bin/sh
# Rebuild the Studio and Director srcsets from the committed masters.
# Sources: assets/ui/studio.png (2214×1598) and assets/ui/director.png (2278×1744).
# Banners and marks from the Gumroad gallery are not inputs.
set -eu
cd "$(dirname "$0")/.."
mkdir -p public/ui

encode() {
  name=$1
  src=$2
  width=$3
  ffmpeg -y -hide_banner -loglevel error -i "$src" \
    -vf "scale=${width}:-2:flags=lanczos" -frames:v 1 \
    -c:v libwebp -quality 85 "public/ui/${name}-${width}.webp"
  ffmpeg -y -hide_banner -loglevel error -i "$src" \
    -vf "scale=${width}:-2:flags=lanczos" -frames:v 1 \
    -c:v libaom-av1 -still-picture 1 -cpu-used 6 -crf 28 -pix_fmt yuv420p \
    "public/ui/${name}-${width}.avif"
}

for name in studio director; do
  src="assets/ui/${name}.png"
  native=$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of csv=p=0 "$src")
  for width in 640 960 1280 1600 1920 "$native"; do
    if [ "$width" -gt "$native" ]; then
      continue
    fi
    encode "$name" "$src" "$width"
  done
done
