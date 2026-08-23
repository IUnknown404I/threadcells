#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_video="$root_dir/launch-media/output/demo/threadcells-demo.webm"
demo_dir="$root_dir/launch-media/output/demo"
website_demo_dir="$root_dir/website/public/media/demo"

command -v ffmpeg >/dev/null 2>&1 || {
  echo "ffmpeg is required to regenerate the MP4 derivative" >&2
  exit 1
}

test -f "$source_video"

ffmpeg -y -i "$source_video" -an -c:v libx264 -preset slow -crf 22 -pix_fmt yuv420p -movflags +faststart "$demo_dir/threadcells-demo.mp4"
mkdir -p "$website_demo_dir"
install -m 0644 "$source_video" "$website_demo_dir/threadcells-demo.webm"
install -m 0644 "$demo_dir/threadcells-demo.mp4" "$website_demo_dir/threadcells-demo.mp4"

du -h "$demo_dir"/threadcells-demo.{webm,mp4}
du -h "$website_demo_dir"/threadcells-demo.{webm,mp4}
