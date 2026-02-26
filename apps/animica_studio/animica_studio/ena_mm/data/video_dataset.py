from __future__ import annotations

from pathlib import Path


class VideoCaptionDataset:
    """Folder layout: videos/*.mp4 and captions.txt with `filename|caption`."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def __iter__(self):
        captions_file = self.root / "captions.txt"
        if not captions_file.exists():
            return
        for row in captions_file.read_text(encoding="utf-8").splitlines():
            if "|" not in row:
                continue
            name, caption = row.split("|", 1)
            video_path = self.root / "videos" / name.strip()
            if video_path.exists():
                yield {"video_path": str(video_path), "caption": caption.strip()}
