"""Token metadata upload: bonk.fun uploader (keyless) or Pinata (needs a free JWT) for pump.fun."""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import httpx

from heavymetal import paths

BONK_IMG = "https://nft-storage.letsbonk22.workers.dev/upload/img"
BONK_META = "https://nft-storage.letsbonk22.workers.dev/upload/meta"
PINATA = "https://uploads.pinata.cloud/v3/files"


def placeholder_png(rgb: tuple[int, int, int] = (153, 69, 255), size: int = 256) -> bytes:
    """A solid-colour PNG with no image library."""
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


class ImageSourceError(ValueError):
    pass


def resolve_local_image(image: str, images_dir: Path | None = None) -> Path:
    """Allow only files inside the images directory ($HEAVYMETAL_HOME/images).

    The `image` argument is written by the model, so any text the agent reads (token descriptions, memos,
    web pages) can steer it. Whatever this function returns is uploaded to public IPFS, so it must never
    resolve to wallet.json, config.toml or anything else outside the images folder.
    """
    root = (images_dir or paths.images_dir()).resolve()
    try:
        target = (root / Path(image).expanduser()).resolve(strict=True)
    except OSError as e:
        raise ImageSourceError(f"image not found: {image}") from e
    if root not in target.parents:
        raise ImageSourceError(f"image must be a URL or a file inside {root} (got {image})")
    if not target.is_file():
        raise ImageSourceError(f"image is not a file: {image}")
    return target


async def load_image(http: httpx.AsyncClient, image: str | None, images_dir: Path | None = None) -> bytes:
    if not image:
        return placeholder_png()
    if image.startswith(("http://", "https://")):
        r = await http.get(image, timeout=30)
        r.raise_for_status()
        return r.content
    return resolve_local_image(image, images_dir).read_bytes()


async def upload_bonk(http: httpx.AsyncClient, img: bytes, meta: dict) -> str:
    r = await http.post(BONK_IMG, files={"image": ("image.png", img, "image/png")}, timeout=60)
    r.raise_for_status()
    image_uri = r.text.strip()
    body = {"createdOn": "https://bonk.fun", "image": image_uri, **meta}
    r = await http.post(BONK_META, content=json.dumps(body), headers={"Content-Type": "application/json"}, timeout=60)
    r.raise_for_status()
    return r.text.strip()


async def upload_pinata(http: httpx.AsyncClient, jwt: str, img: bytes, meta: dict) -> str:
    if not jwt:
        raise RuntimeError("pump.fun launches need keys.pinata_jwt (free at pinata.cloud); or use pool='bonk'")
    h = {"Authorization": f"Bearer {jwt}"}
    r = await http.post(
        PINATA, headers=h, data={"network": "public"}, files={"file": ("image.png", img, "image/png")}, timeout=60
    )
    r.raise_for_status()
    image = f"https://ipfs.io/ipfs/{r.json()['data']['cid']}"
    r = await http.post(
        PINATA,
        headers=h,
        data={"network": "public"},
        files={"file": ("metadata.json", json.dumps({**meta, "image": image}), "application/json")},
        timeout=60,
    )
    r.raise_for_status()
    return f"https://ipfs.io/ipfs/{r.json()['data']['cid']}"
