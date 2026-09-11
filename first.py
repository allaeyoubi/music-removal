import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
from queue import Empty, Queue
from pathlib import Path
from threading import Event
from typing import Callable


class ProcessingCancelled(Exception):
    """Raised when a user stops an active media-processing job."""


def find_ffmpeg() -> str | None:
    """Return FFmpeg from PATH or from the standard Windows winget location."""
    if executable := shutil.which("ffmpeg"):
        return executable

    if sys.platform == "win32" and (local_app_data := os.getenv("LOCALAPPDATA")):
        packages_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        candidates = sorted(packages_dir.glob("Gyan.FFmpeg*_Microsoft.Winget.Source_8wekyb3d8bbwe/**/bin/ffmpeg.exe"))
        if candidates:
            return str(candidates[-1])
    return None


def run(
    command: list[str],
    cancel_event: Event | None = None,
    output_callback: Callable[[str], None] | None = None,
) -> None:
    resolved_command = command.copy()
    if resolved_command[0] == "ffmpeg":
        executable = find_ffmpeg()
        if executable is None:
            raise RuntimeError("FFmpeg is not installed or is not available on PATH.")
        resolved_command[0] = executable
    print(f"\n$ {' '.join(str(value) for value in resolved_command)}")
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessingCancelled("Processing was cancelled.")

    process = subprocess.Popen(
        resolved_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    output_queue: Queue[str | None] = Queue()

    def read_output() -> None:
        assert process.stdout is not None
        buffer = ""
        while True:
            character = process.stdout.read(1)
            if not character:
                break
            if character in "\r\n":
                if buffer:
                    output_queue.put(buffer)
                    buffer = ""
            else:
                buffer += character
        if buffer:
            output_queue.put(buffer)
        output_queue.put(None)

    threading.Thread(target=read_output, daemon=True).start()

    def drain_output() -> None:
        while True:
            try:
                line = output_queue.get_nowait()
            except Empty:
                return
            if line is None:
                return
            print(line, end="")
            if output_callback is not None:
                output_callback(line)

    while process.poll() is None:
        drain_output()
        if cancel_event is not None and cancel_event.is_set():
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise ProcessingCancelled("Processing was cancelled.")
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            continue

    drain_output()

    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, resolved_command)


def extract_audio(video_path: Path, audio_path: Path, cancel_event: Event | None = None) -> None:
    run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "44100",
        "-ac", "2",
        str(audio_path),
    ])


def separate_music(
    audio_path: Path,
    output_dir: Path,
    model: str,
    cancel_event: Event | None = None,
    progress: Callable[[str, int], None] | None = None,
) -> Path:
    def report_demucs_output(line: str) -> None:
        match = re.search(r"(\d{1,3})%", line)
        if match and progress is not None:
            demucs_percentage = min(100, int(match.group(1)))
            progress(
                f"Separating voice from background music… {demucs_percentage}%",
                22 + int(demucs_percentage * 0.69),
            )

    run([
        sys.executable, "-m", "demucs.separate",
        "-n", model,
        "-o", str(output_dir),
        str(audio_path),
    ], cancel_event, report_demucs_output)
    vocals_path = output_dir / model / audio_path.stem / "vocals.wav"
    if not vocals_path.exists():
        raise FileNotFoundError(f"Expected Demucs output not found: {vocals_path}")
    return vocals_path


def remux(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    cancel_event: Event | None = None,
) -> None:
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
    ], cancel_event)


def remove_background_music(
    video_path: Path,
    output_path: Path,
    workdir: Path,
    model: str = "htdemucs",
    progress: Callable[[str, int], None] | None = None,
    cancel_event: Event | None = None,
) -> None:
    """Create a copy of *video_path* using Demucs' vocal stem as its audio."""
    if not video_path.exists():
        raise FileNotFoundError(f"Input video not found: {video_path}")
    if find_ffmpeg() is None:
        raise RuntimeError("FFmpeg is not installed or is not available on PATH.")

    workdir.mkdir(parents=True, exist_ok=True)
    extracted_audio = workdir / "extracted_audio.wav"
    def notify(message: str, percentage: int) -> None:
        if progress is not None:
            progress(message, percentage)
        else:
            print(message)

    notify("Extracting audio from video…", 8)
    extract_audio(video_path, extracted_audio, cancel_event)

    notify("Separating voice from background music…", 22)
    vocals_path = separate_music(extracted_audio, workdir, model, cancel_event, progress)

    notify("Creating your music-free video…", 92)
    remux(video_path, vocals_path, output_path, cancel_event)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove background music from a video while keeping vocals."
    )
    parser.add_argument("input_video", type=Path)
    parser.add_argument("output_video", type=Path)
    parser.add_argument("--model", default="htdemucs")
    parser.add_argument("--workdir", type=Path, default=Path("work_tmp"))
    args = parser.parse_args()

    try:
        remove_background_music(
            args.input_video,
            args.output_video,
            args.workdir,
            args.model,
        )
    except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f"\nCould not process the video: {error}")
    print(f"\nDone. Music-free video saved to: {args.output_video}")


if __name__ == "__main__":
    main()
