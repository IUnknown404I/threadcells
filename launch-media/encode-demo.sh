#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_video="$root_dir/launch-media/output/demo/threadcells-demo.webm"
demo_dir="$root_dir/launch-media/output/demo"
website_demo_dir="$root_dir/website/public/media/demo"

command -v ffmpeg >/dev/null 2>&1 || {
  echo "ffmpeg is required to regenerate MP4 and GIF derivatives" >&2
  exit 1
}

test -f "$source_video"

ffmpeg -y -i "$source_video" -an -c:v libx264 -preset slow -crf 22 -pix_fmt yuv420p -movflags +faststart "$demo_dir/threadcells-demo.mp4"
ffmpeg -y -i "$source_video" -vf "fps=12,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle" -loop 0 "$demo_dir/threadcells-demo.gif"
mkdir -p "$website_demo_dir"
install -m 0644 "$source_video" "$website_demo_dir/threadcells-demo.webm"
install -m 0644 "$demo_dir/threadcells-demo.mp4" "$website_demo_dir/threadcells-demo.mp4"

du -h "$demo_dir"/threadcells-demo.{webm,mp4,gif}
du -h "$website_demo_dir"/threadcells-demo.{webm,mp4}
