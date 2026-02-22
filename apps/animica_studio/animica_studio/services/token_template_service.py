from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from pathlib import Path


@dataclass(frozen=True)
class TokenTemplateParam:
    key: str
    label: str
    required: bool = True
    default: str = ""
    help_text: str = ""


@dataclass(frozen=True)
class TokenTemplateDef:
    id: str
    name: str
    description: str
    folder: str
    main_file: str
    params: tuple[TokenTemplateParam, ...] = field(default_factory=tuple)


class TokenTemplateService:
    def __init__(self) -> None:
        root = Path(__file__).resolve().parent.parent / "templates" / "tokens"
        self._root = root
        self._templates: dict[str, TokenTemplateDef] = {
            "nft": TokenTemplateDef(
                id="nft",
                name="Animica NFT",
                description="ERC-721-like NFT with mint/burn/transfer/approve/tokenURI.",
                folder="nft",
                main_file="contract.py",
                params=(
                    TokenTemplateParam("NAME", "Name", default="Animica NFT"),
                    TokenTemplateParam("SYMBOL", "Symbol", default="ANFT"),
                    TokenTemplateParam("BASE_URI", "Base URI", required=False, default="ipfs://collection/"),
                    TokenTemplateParam("ROYALTY_BPS", "Royalty BPS", required=False, default="0"),
                ),
            ),
            "ft": TokenTemplateDef(
                id="ft",
                name="Animica FT",
                description="ERC-20-like fungible token with approvals and optional mint/burn.",
                folder="ft",
                main_file="contract.py",
                params=(
                    TokenTemplateParam("NAME", "Name", default="Animica Token"),
                    TokenTemplateParam("SYMBOL", "Symbol", default="ATKN"),
                    TokenTemplateParam("DECIMALS", "Decimals", default="18"),
                    TokenTemplateParam("TOTAL_SUPPLY", "Initial Total Supply", default="1000000"),
                ),
            ),
            "multitoken": TokenTemplateDef(
                id="multitoken",
                name="Animica MultiToken",
                description="ERC-1155-like multi-token with batch transfer and mint/burn.",
                folder="multitoken",
                main_file="contract.py",
                params=(
                    TokenTemplateParam("NAME", "Name", default="Animica MultiToken"),
                    TokenTemplateParam("BASE_URI", "Base URI", required=False, default="ipfs://multitoken/{id}/"),
                    TokenTemplateParam("COLLECTION_ID", "Default Token ID", default="1"),
                ),
            ),
            "membership": TokenTemplateDef(
                id="membership",
                name="Membership Pass",
                description="NFT membership pass with optional soulbound mode.",
                folder="membership",
                main_file="contract.py",
                params=(
                    TokenTemplateParam("NAME", "Name", default="Membership Pass"),
                    TokenTemplateParam("SYMBOL", "Symbol", default="MPASS"),
                    TokenTemplateParam("SOULBOUND", "Soulbound (true/false)", default="true"),
                ),
            ),
            "factory": TokenTemplateDef(
                id="factory",
                name="Token Factory Registry",
                description="Factory/registry stub to register multiple token collections.",
                folder="factory",
                main_file="contract.py",
                params=(
                    TokenTemplateParam("NAME", "Factory Name", default="TokenFactory"),
                    TokenTemplateParam("OWNER", "Owner Address", default="owner"),
                ),
            ),
        }

    def list_templates(self) -> list[TokenTemplateDef]:
        return sorted(self._templates.values(), key=lambda t: t.name.lower())

    def get(self, template_id: str) -> TokenTemplateDef:
        return self._templates[template_id]

    def render(self, template_id: str, params: dict[str, str]) -> dict[str, str]:
        template = self.get(template_id)
        values = self._validated_values(template, params)
        rendered: dict[str, str] = {}
        tmpl_dir = self._root / template.folder
        for path in tmpl_dir.rglob("*.tmpl"):
            rel = path.relative_to(tmpl_dir)
            out = rel.as_posix().replace(".tmpl", "")
            text = path.read_text(encoding="utf-8")
            for key, value in values.items():
                text = text.replace("{{" + key + "}}", value)
            if out.endswith("manifest.json"):
                parsed = json.loads(text)
                text = json.dumps(parsed, indent=2) + "\n"
            rendered[out] = text
        return rendered

    def write_to_project(
        self,
        rendered_files: dict[str, str],
        output_dir: Path,
        *,
        overwrite: bool = False,
    ) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        existing = [output_dir / rel for rel in rendered_files if (output_dir / rel).exists()]
        if existing and not overwrite:
            names = ", ".join(sorted(str(p.name) for p in existing))
            raise FileExistsError(f"Refusing to overwrite existing files: {names}")
        written: list[Path] = []
        for rel, content in rendered_files.items():
            dst = output_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(content, encoding="utf-8")
            written.append(dst)
        return written

    def _validated_values(self, template: TokenTemplateDef, params: dict[str, str]) -> dict[str, str]:
        values: dict[str, str] = {}
        for param in template.params:
            raw = (params.get(param.key, param.default) or "").strip()
            if param.required and not raw:
                raise ValueError(f"{param.label} is required")
            values[param.key] = raw

        symbol = values.get("SYMBOL")
        if symbol and not re.fullmatch(r"[A-Z0-9]{2,10}", symbol):
            raise ValueError("Symbol must be 2-10 chars: A-Z or 0-9")

        decimals = values.get("DECIMALS")
        if decimals:
            d = int(decimals)
            if d < 0 or d > 30:
                raise ValueError("Decimals must be between 0 and 30")

        total_supply = values.get("TOTAL_SUPPLY")
        if total_supply:
            if int(total_supply) < 0:
                raise ValueError("Total supply must be >= 0")

        royalty = values.get("ROYALTY_BPS")
        if royalty:
            r = int(royalty)
            if r < 0 or r > 10000:
                raise ValueError("Royalty BPS must be 0..10000")

        soulbound = values.get("SOULBOUND")
        if soulbound:
            norm = soulbound.lower()
            if norm not in {"true", "false"}:
                raise ValueError("Soulbound must be true or false")
            values["SOULBOUND"] = "true" if norm == "true" else "false"

        for key in ("NAME", "OWNER"):
            if key in values:
                values[key] = self._sanitize_text(values[key])
        return values

    def _sanitize_text(self, text: str) -> str:
        if not text:
            return text
        return re.sub(r"[^\w .:/{}-]", "", text).strip()
