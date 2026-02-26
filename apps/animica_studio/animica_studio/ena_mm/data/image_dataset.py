from __future__ import annotations

from pathlib import Path


class ImageCaptionFolderDataset:
    """Folder layout: images/*.png and captions.txt with `filename|caption`."""

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
            image_path = self.root / "images" / name.strip()
            if image_path.exists():
                yield {"image_path": str(image_path), "caption": caption.strip()}
