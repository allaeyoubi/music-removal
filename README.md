# Music Removal

Remove background music from a video while keeping the vocal track. The program uses FFmpeg to extract and rebuild the video, and Demucs to separate vocals from instruments.

## Requirements

- Windows, macOS, or Linux
- Python 3.10-3.12 recommended
- FFmpeg installed and available on your `PATH`
- A few hundred MB of disk space for the Demucs model (downloaded on first use)

## Install

Clone the repository and enter the project folder:

```bash
git clone https://github.com/allaeyoubi/music-removal.git
cd music-removal
```

Create and activate a virtual environment:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Install the Python dependency:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Install FFmpeg separately and verify it:

```bash
ffmpeg -version
```

## Usage

Put a video in the project folder and run:

```bash
python first.py input.mp4 output_no_music.mp4
```

The first run downloads the `htdemucs` model. Processing time depends on the video length and your hardware.

You can choose a different Demucs model or temporary directory:

```bash
python first.py input.mp4 output_no_music.mp4 --model htdemucs --workdir work_tmp
```

## Notes

Demucs separates vocals from instruments; it does not specifically identify spoken dialogue. Singing is treated as vocals and may remain in the output. Separation is not perfect, so some music artifacts may remain.

Input videos, output videos, temporary audio, and Python cache files are ignored by Git because they can be large. The output keeps the original video stream and replaces its audio with the separated vocal track.