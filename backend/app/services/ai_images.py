"""Image generator (module 32).

* Branded text cards (always available): Pillow renders the title on the organisation's brand colour at
  every requested size — deterministic, no external API.
* AI images: builds a branded prompt (prompt library ``image.prompt``) and calls OpenAI Images or Gemini
  Imagen when connected; otherwise NOT CONNECTED with the prompt returned so it can be used elsewhere.
"""
from __future__ import annotations

import base64
import io
import os
import re

from PIL import Image, ImageDraw, ImageFont

from . import ai_engine as eng
from . import ai_prompts as prompts
from . import integrations as ih

SIZES = {
    "link_1200x628": (1200, 628, "Link share — Facebook / LinkedIn / X card"),
    "square_1080x1080": (1080, 1080, "Square post — Instagram / LinkedIn / Facebook"),
    "story_1080x1920": (1080, 1920, "Story / Reel — Instagram / Facebook / TikTok"),
    "landscape_1600x900": (1600, 900, "Landscape — X / blog header"),
    "portrait_1080x1350": (1080, 1350, "Portrait post — Instagram"),
    "pin_1000x1500": (1000, 1500, "Pinterest pin"),
}
# closest size each image API supports (generated images are then cover-cropped to the exact size)
OPENAI_SIZE = {"square": "1024x1024", "landscape": "1536x1024", "portrait": "1024x1536"}
GEMINI_ASPECT = {"square": "1:1", "landscape": "16:9", "portrait": "9:16"}

FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
                   "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"]
FONT_REG = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf"]


def _font(size: int, bold=True):
    for p in (FONT_CANDIDATES if bold else FONT_REG):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def _hex(c: str | None, default="#1F3A5F") -> tuple[int, int, int]:
    c = c if c and re.fullmatch(r"#?[0-9a-fA-F]{6}", c) else default
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _contrast(rgb) -> tuple[int, int, int]:
    lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255
    return (20, 33, 61) if lum > 0.6 else (255, 255, 255)


def _shade(rgb, k: float):
    return tuple(max(0, min(255, int(c * k))) for c in rgb)


def _wrap(draw, text, font, width) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def text_card(title: str, size_key: str, *, brand: str, primary: str | None = None, accent: str | None = None,
              subtitle: str | None = None) -> bytes:
    """Render a branded PNG card. Title is auto-sized to fit the safe area."""
    w, h, _ = SIZES[size_key]
    bg = _hex(primary)
    ac = _hex(accent, "#F2B705") if accent else _shade(bg, 1.6) if sum(bg) < 300 else _shade(bg, 0.6)
    fg = _contrast(bg)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    # subtle diagonal band for depth
    d.polygon([(int(w * 0.62), 0), (w, 0), (w, h), (int(w * 0.38), h)], fill=_shade(bg, 0.88 if sum(bg) > 200 else 1.25))
    m = int(min(w, h) * 0.08)
    d.rectangle([m, m, m + int(w * 0.12), m + max(6, h // 120)], fill=ac)
    title = re.sub(r"\s+", " ", title or "").strip() or brand
    box_w = w - 2 * m
    max_h = int(h * (0.55 if h > w else 0.5))
    size = int(min(w, h) * 0.12)
    while size > 18:
        f = _font(size)
        lines = _wrap(d, title, f, box_w)
        lh = int(size * 1.18)
        if len(lines) * lh <= max_h and all(d.textlength(line, font=f) <= box_w for line in lines):
            break
        size -= 2
    f = _font(size)
    lines = _wrap(d, title, f, box_w)
    lh = int(size * 1.18)
    y = (h - len(lines) * lh) // 2 - (int(size * 0.4) if subtitle else 0)
    for line in lines:
        d.text((m, y), line, font=f, fill=fg)
        y += lh
    if subtitle:
        sf = _font(max(16, size // 2), bold=False)
        for line in _wrap(d, subtitle, sf, box_w)[:2]:
            d.text((m, y + int(size * 0.3)), line, font=sf, fill=fg)
            y += int(size * 0.6)
    bf = _font(max(16, int(min(w, h) * 0.035)))
    d.text((m, h - m - bf.size), brand or "", font=bf, fill=fg)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def cover_crop(png: bytes, w: int, h: int) -> bytes:
    im = Image.open(io.BytesIO(png)).convert("RGB")
    k = max(w / im.width, h / im.height)
    im = im.resize((max(w, round(im.width * k)), max(h, round(im.height * k))), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    im = im.crop((x, y, x + w, y + h))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def shape_of(size_key: str) -> str:
    w, h, _ = SIZES[size_key]
    return "square" if abs(w - h) < 50 else "landscape" if w > h else "portrait"


def image_prompt(db, title: str, brand: str, colors: str, style: str, size_key: str) -> str:
    w, h, label = SIZES[size_key]
    text, _ = prompts.build(db, "image.prompt", {"title": title, "brand": brand, "colors": colors, "style": style or "modern, minimal, photographic",
                                                 "size": f"{w}×{h} ({label})"})
    return text


def image_providers(db) -> list[str]:
    conn = ih.connected_map(db)
    order = [r["key"] for r in eng.ordered_providers(db) if r["enabled"]]
    return [p for p in order if p in ("openai", "gemini") and conn.get(p) in ("CONNECTED", "RATE_LIMITED")]


def generate_ai(db, org_id, prompt: str, size_key: str) -> tuple[bytes, dict]:
    """Call the first connected image API in routing order. Raises ih.NotConnected when none is connected."""
    from .agents import BudgetExceeded  # noqa: F401  (raised by check_budget)
    provs = image_providers(db)
    if not provs:
        raise ih.NotConnected("Image generation needs OpenAI or Google Gemini — NOT CONNECTED")
    eng.check_budget(db, org_id)
    w, h, _ = SIZES[size_key]
    shape = shape_of(size_key)
    errors = []
    for p in provs:
        cr = ih.credentials(db, org_id, p)
        try:
            ih.take_token(db, org_id, p)
            if p == "openai":
                model = (cr.get("config") or {}).get("image_model") or "gpt-image-1"
                j = ih._ok(ih.http_call("POST", "https://api.openai.com/v1/images/generations", timeout=180,
                                        headers={"Authorization": f"Bearer {cr['secret']}"},
                                        json={"model": model, "prompt": prompt, "size": OPENAI_SIZE[shape], "n": 1}), "OpenAI Images").json()
                b64 = j["data"][0].get("b64_json")
                if not b64:
                    raise ih.IntegrationError("OpenAI Images returned no image data")
                raw = base64.b64decode(b64)
            else:
                model = (cr.get("config") or {}).get("image_model") or "imagen-3.0-generate-002"
                j = ih._ok(ih.http_call("POST", f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predict", timeout=180,
                                        params={"key": cr["secret"]},
                                        json={"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1, "aspectRatio": GEMINI_ASPECT[shape]}}),
                           "Gemini Imagen").json()
                raw = base64.b64decode(j["predictions"][0]["bytesBase64Encoded"])
        except (ih.IntegrationError, KeyError, IndexError, ValueError) as e:
            errors.append(f"{p}: {e}")
            continue
        rec = eng.record_usage(org_id, p, model, 0, 0, task_type="image", kind="image", units=1, cost=eng.image_cost(p, model, 1))
        return cover_crop(raw, w, h), {"provider": p, "model": model, "cost": rec["cost"]}
    raise ih.IntegrationError("All image providers failed: " + "; ".join(errors))
