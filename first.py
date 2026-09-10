import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def run(command: list[str]) -> None:
    print(f"\n$ {' '.join(str(value) for value in command)}")
    subprocess.run(command, check=True)


def extract_audio(video_path: Path, audio_path: Path) -> None:
    run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "44100",
        "-ac", "2",
        str(audio_path),
    ])


def separate_music(audio_path: Path, output_dir: Path, model: str) -> Path:
    run([
        sys.executable, "-m", "demucs.separate",
        "-n", model,
        "-o", str(output_dir),
        str(audio_path),
    ])
    vocals_path = output_dir / model / audio_path.stem / "vocals.wav"
    if not vocals_path.exists():
        raise FileNotFoundError(f"Expected Demucs output not found: {vocals_path}")
    return vocals_path


def remux(video_path: Path, audio_path: Path, output_path: Path) -> None:
    run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        str(output_path),
    ])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove background music from a video while keeping vocals."
    )
    parser.add_argument("input_video", type=Path)
    parser.add_argument("output_video", type=Path)
    parser.add_argument("--model", default="htdemucs")
    parser.add_argument("--workdir", type=Path, default=Path("work_tmp"))
    args = parser.parse_args()

    if not args.input_video.exists():
        parser.error(f"Input video not found: {args.input_video}")
    if shutil.which("ffmpeg") is None:
        sys.exit("FFmpeg not found. Restart PowerShell after installing FFmpeg, then try again.")

    args.workdir.mkdir(parents=True, exist_ok=True)
    extracted_audio = args.workdir / "extracted_audio.wav"

    print("Step 1/3 - Extracting audio from video...")
    extract_audio(args.input_video, extracted_audio)

    print("Step 2/3 - Separating speech from music with Demucs...")
    vocals_path = separate_music(extracted_audio, args.workdir, args.model)

    print("Step 3/3 - Rebuilding the video with music-free audio...")
    remux(args.input_video, vocals_path, args.output_video)
    print(f"\nDone. Music-free video saved to: {args.output_video}")


if __name__ == "__main__":
    main()
