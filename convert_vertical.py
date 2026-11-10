#!/usr/bin/env python3
"""
Post-processing script to convert 16:9 recordings to 9:16 (vertical/portrait)
for Reels, TikTok, Shorts, etc.

Usage:
    python convert_vertical.py                          # Convert all videos in downloads/
    python convert_vertical.py --input downloads/model\ \[CB\]/  # Convert specific folder
    python convert_vertical.py --file video.mp4         # Convert a single file
    python convert_vertical.py --position left          # Crop from left side instead of center
    python convert_vertical.py --scale 1080x1920        # Scale output to specific resolution
    python convert_vertical.py --crf 20                 # Higher quality (lower = better, default 23)
    python convert_vertical.py --preset medium           # Slower encoding, better compression
    python convert_vertical.py --dry-run                # Show commands without executing
"""

import argparse
import glob
import os
import subprocess
import sys
from pathlib import Path

# Defaults (independent of parameters.py to avoid django-environ dependency)
DOWNLOADS_DIR = os.environ.get("STRMNTR_DOWNLOAD_DIR", "downloads")
FFMPEG_PATH = os.environ.get("STRMNTR_FFMPEG_PATH", "ffmpeg")
CONTAINER = os.environ.get("STRMNTR_CONTAINER", "mp4")

# Output subdirectory name for converted files
VERTICAL_SUBDIR = "vertical_9x16"


def get_video_info(filepath):
    """Get video width and height using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        filepath
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None, None
        parts = result.stdout.strip().split(",")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except (subprocess.TimeoutExpired, ValueError):
        pass
    return None, None


def is_already_vertical(width, height):
    """Check if video is already in portrait/vertical orientation."""
    if not width or not height:
        return False
    return width < height


def build_crop_filter(width, height, position="center", scale=None):
    """
    Build FFmpeg crop filter for 9:16 from 16:9 source.

    For a 1920x1080 source:
      - crop width = height * 9 / 16 = 607
      - crop height = 1080 (full height preserved)
      - Result: 607x1080 (9:16 ratio)
    """
    crop_w = int(height * 9 / 16)

    # Make crop width even (required by most codecs)
    if crop_w % 2 != 0:
        crop_w -= 1

    if position == "center":
        x_offset = f"(in_w-{crop_w})/2"
    elif position == "left":
        x_offset = "0"
    elif position == "right":
        x_offset = f"in_w-{crop_w}"
    else:
        x_offset = f"(in_w-{crop_w})/2"

    vf = f"crop={crop_w}:{height}:{x_offset}:0"

    if scale:
        # Ensure even dimensions
        sw, sh = scale
        sw = sw - (sw % 2)
        sh = sh - (sh % 2)
        vf += f",scale={sw}:{sh}"

    return vf


def convert_video(input_path, output_path, position="center", scale=None,
                  crf=23, preset="ultrafast", dry_run=False):
    """Convert a single video from 16:9 to 9:16."""
    width, height = get_video_info(input_path)

    if width is None:
        print(f"  [SKIP] Cannot read video info: {input_path}")
        return False

    if is_already_vertical(width, height):
        print(f"  [SKIP] Already vertical ({width}x{height}): {input_path}")
        return False

    ratio = width / height if height else 0
    if ratio < 1.2:
        print(f"  [SKIP] Not wide enough to crop ({width}x{height}): {input_path}")
        return False

    vf = build_crop_filter(width, height, position, scale)
    crop_w = int(height * 9 / 16)
    if crop_w % 2 != 0:
        crop_w -= 1

    if scale:
        out_desc = f"{scale[0]}x{scale[1]}"
    else:
        out_desc = f"{crop_w}x{height}"

    print(f"  [CONVERT] {width}x{height} -> {out_desc} | {os.path.basename(input_path)}")

    cmd = [
        FFMPEG_PATH,
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-c:a", "copy",
        "-movflags", "+faststart",  # Web-optimized MP4
        "-n",  # Don't overwrite existing files
        output_path
    ]

    if dry_run:
        print(f"  [DRY-RUN] {' '.join(cmd)}")
        return True

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if result.returncode == 0:
            input_size = os.path.getsize(input_path) / (1024 * 1024)
            output_size = os.path.getsize(output_path) / (1024 * 1024)
            print(f"  [OK] {input_size:.1f}MB -> {output_size:.1f}MB ({output_path})")
            return True
        else:
            print(f"  [ERROR] FFmpeg failed: {result.stderr[-200:]}")
            # Clean up failed output
            if os.path.exists(output_path):
                os.remove(output_path)
            return False
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] Timeout converting: {input_path}")
        if os.path.exists(output_path):
            os.remove(output_path)
        return False


def find_videos(search_path, container=CONTAINER):
    """Find all video files in the given path."""
    # Escape glob special characters in the path (brackets like [SC] are interpreted as patterns)
    escaped_path = glob.escape(search_path)
    videos = []
    for ext in [container, "mp4", "mkv"]:
        videos.extend(glob.glob(os.path.join(escaped_path, "**", f"*.{ext}"), recursive=True))
    # Deduplicate and exclude already-converted files
    seen = set()
    result = []
    for v in videos:
        real = os.path.realpath(v)
        if real not in seen and VERTICAL_SUBDIR not in v:
            seen.add(real)
            result.append(v)
    return sorted(result)


def main():
    parser = argparse.ArgumentParser(
        description="Convert 16:9 recordings to 9:16 vertical format (Reels/TikTok/Shorts)"
    )
    parser.add_argument(
        "--input", "-i",
        default=DOWNLOADS_DIR,
        help=f"Input directory to scan (default: {DOWNLOADS_DIR})"
    )
    parser.add_argument(
        "--file", "-f",
        help="Convert a single file instead of scanning a directory"
    )
    parser.add_argument(
        "--output-dir", "-o",
        help="Custom output directory (default: <input>/{vertical_subdir}/ next to each file)"
    )
    parser.add_argument(
        "--position", "-p",
        choices=["center", "left", "right"],
        default="center",
        help="Crop position: center (default), left, or right of the frame"
    )
    parser.add_argument(
        "--scale", "-s",
        help="Scale output to WIDTHxHEIGHT (e.g., 1080x1920). Omit to keep cropped size."
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=23,
        help="Quality (0-51, lower=better, default: 23)"
    )
    parser.add_argument(
        "--preset",
        default="medium",
        choices=["ultrafast", "superfast", "veryfast", "faster", "fast",
                 "medium", "slow", "slower", "veryslow"],
        help="Encoding speed/quality tradeoff (default: medium)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually converting"
    )

    args = parser.parse_args()

    scale = None
    if args.scale:
        try:
            w, h = args.scale.lower().split("x")
            scale = (int(w), int(h))
        except ValueError:
            print(f"Error: Invalid scale format '{args.scale}'. Use WIDTHxHEIGHT (e.g., 1080x1920)")
            sys.exit(1)

    # Single file mode
    if args.file:
        if not os.path.isfile(args.file):
            print(f"Error: File not found: {args.file}")
            sys.exit(1)

        if args.output_dir:
            os.makedirs(args.output_dir, exist_ok=True)
            output_path = os.path.join(args.output_dir, os.path.basename(args.file))
        else:
            parent = os.path.dirname(args.file)
            out_dir = os.path.join(parent, VERTICAL_SUBDIR)
            os.makedirs(out_dir, exist_ok=True)
            output_path = os.path.join(out_dir, os.path.basename(args.file))

        success = convert_video(
            args.file, output_path,
            position=args.position, scale=scale,
            crf=args.crf, preset=args.preset, dry_run=args.dry_run
        )
        sys.exit(0 if success else 1)

    # Directory scan mode
    if not os.path.isdir(args.input):
        print(f"Error: Directory not found: {args.input}")
        sys.exit(1)

    videos = find_videos(args.input)
    if not videos:
        print(f"No videos found in {args.input}")
        sys.exit(0)

    print(f"Found {len(videos)} video(s) in {args.input}")
    print(f"Settings: position={args.position}, crf={args.crf}, preset={args.preset}")
    if scale:
        print(f"Scale: {scale[0]}x{scale[1]}")
    print()

    converted = 0
    skipped = 0
    errors = 0

    for video_path in videos:
        if args.output_dir:
            # Preserve folder structure under custom output dir
            rel = os.path.relpath(video_path, args.input)
            output_path = os.path.join(args.output_dir, rel)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
        else:
            parent = os.path.dirname(video_path)
            out_dir = os.path.join(parent, VERTICAL_SUBDIR)
            os.makedirs(out_dir, exist_ok=True)
            output_path = os.path.join(out_dir, os.path.basename(video_path))

        if os.path.exists(output_path):
            skipped += 1
            continue

        result = convert_video(
            video_path, output_path,
            position=args.position, scale=scale,
            crf=args.crf, preset=args.preset, dry_run=args.dry_run
        )
        if result:
            converted += 1
        elif result is False:
            # Could be skip or error - count skips separately
            skipped += 1

    print(f"\nDone: {converted} converted, {skipped} skipped")


if __name__ == "__main__":
    main()
