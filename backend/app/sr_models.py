"""Real-ESRGAN super-resolution networks (architectures re-implemented from the BSD-3 licensed
xinntao/Real-ESRGAN and BasicSR projects), run on the CPU with PyTorch.

Weights are downloaded once from the official GitHub releases into MODEL_DIR and verified by SHA-256.
"""
from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from . import config

MODELS = {
    "fast": {
        "label": "Fast (realesr-general-x4v3, 5 MB)",
        "file": "realesr-general-x4v3.pth",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
        "sha256": "8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292",
    },
    "quality": {
        "label": "Quality (RealESRGAN_x4plus, 64 MB)",
        "file": "RealESRGAN_x4plus.pth",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        "sha256": "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1",
    },
}
SCALE = 4


class SRVGGNetCompact(nn.Module):
    """Compact VGG-style network: conv/PReLU stack, pixel shuffle, plus nearest-upsampled residual."""

    def __init__(self, num_feat: int = 64, num_conv: int = 32, upscale: int = 4):
        super().__init__()
        self.upscale = upscale
        body: list[nn.Module] = [nn.Conv2d(3, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
        for _ in range(num_conv):
            body += [nn.Conv2d(num_feat, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
        body.append(nn.Conv2d(num_feat, 3 * upscale * upscale, 3, 1, 1))
        self.body = nn.ModuleList(body)
        self.upsampler = nn.PixelShuffle(upscale)

    def forward(self, x):
        out = x
        for layer in self.body:
            out = layer(out)
        return self.upsampler(out) + F.interpolate(x, scale_factor=self.upscale, mode="nearest")


class _RDB(nn.Module):
    def __init__(self, nf: int = 64, gc: int = 32):
        super().__init__()
        self.conv1 = nn.Conv2d(nf, gc, 3, 1, 1)
        self.conv2 = nn.Conv2d(nf + gc, gc, 3, 1, 1)
        self.conv3 = nn.Conv2d(nf + 2 * gc, gc, 3, 1, 1)
        self.conv4 = nn.Conv2d(nf + 3 * gc, gc, 3, 1, 1)
        self.conv5 = nn.Conv2d(nf + 4 * gc, nf, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(0.2, inplace=True)

    def forward(self, x):
        x1 = self.lrelu(self.conv1(x))
        x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
        x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
        x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
        x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
        return x5 * 0.2 + x


class _RRDB(nn.Module):
    def __init__(self, nf: int = 64, gc: int = 32):
        super().__init__()
        self.rdb1, self.rdb2, self.rdb3 = _RDB(nf, gc), _RDB(nf, gc), _RDB(nf, gc)

    def forward(self, x):
        return self.rdb3(self.rdb2(self.rdb1(x))) * 0.2 + x


class RRDBNet(nn.Module):
    """ESRGAN generator (23 residual-in-residual dense blocks), x4."""

    def __init__(self, nf: int = 64, nb: int = 23, gc: int = 32):
        super().__init__()
        self.conv_first = nn.Conv2d(3, nf, 3, 1, 1)
        self.body = nn.Sequential(*[_RRDB(nf, gc) for _ in range(nb)])
        self.conv_body = nn.Conv2d(nf, nf, 3, 1, 1)
        self.conv_up1 = nn.Conv2d(nf, nf, 3, 1, 1)
        self.conv_up2 = nn.Conv2d(nf, nf, 3, 1, 1)
        self.conv_hr = nn.Conv2d(nf, nf, 3, 1, 1)
        self.conv_last = nn.Conv2d(nf, 3, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(0.2, inplace=True)

    def forward(self, x):
        feat = self.conv_first(x)
        feat = feat + self.conv_body(self.body(feat))
        feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")))
        feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")))
        return self.conv_last(self.lrelu(self.conv_hr(feat)))


def weights_path(name: str) -> Path:
    return config.MODEL_DIR / "realesrgan" / MODELS[name]["file"]


def is_downloaded(name: str) -> bool:
    return weights_path(name).is_file()


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_weights(name: str, progress=None) -> Path:
    """Download (once) and verify the weights. The only network access the app ever makes."""
    m = MODELS[name]
    p = weights_path(name)
    if p.is_file():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".partial")
    req = urllib.request.Request(m["url"], headers={"User-Agent": "photo-manager"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            fh.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if _sha256(tmp) != m["sha256"]:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded {m['file']} failed its SHA-256 check")
    tmp.replace(p)
    return p


_loaded: dict[str, nn.Module] = {}


def load(name: str, progress=None) -> nn.Module:
    if name not in _loaded:
        sd = torch.load(ensure_weights(name, progress), map_location="cpu", weights_only=True)
        sd = sd.get("params_ema") or sd.get("params") or sd
        net = SRVGGNetCompact() if name == "fast" else RRDBNet()
        net.load_state_dict(sd, strict=True)
        net.eval()
        _loaded[name] = net
    return _loaded[name]


@torch.inference_mode()
def upscale(rgb, name: str = "fast", tile: int = 256, pad: int = 16, progress=None, cancelled=lambda: False):
    """rgb: HxWx3 uint8 numpy -> (4H)x(4W)x3 uint8, processed in overlapping tiles to bound memory."""
    import numpy as np

    torch.set_num_threads(config.WORKERS)
    net = load(name)
    h, w, _ = rgb.shape
    x = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1).float().div_(255).unsqueeze(0)
    out = torch.zeros((1, 3, h * SCALE, w * SCALE))
    ys, xs = list(range(0, h, tile)), list(range(0, w, tile))
    n, total = 0, len(ys) * len(xs)
    for y0 in ys:
        for x0 in xs:
            if cancelled():
                raise InterruptedError("cancelled")
            y1, x1 = min(y0 + tile, h), min(x0 + tile, w)
            py0, px0, py1, px1 = max(0, y0 - pad), max(0, x0 - pad), min(h, y1 + pad), min(w, x1 + pad)
            res = net(x[:, :, py0:py1, px0:px1])
            oy, ox = (y0 - py0) * SCALE, (x0 - px0) * SCALE
            out[:, :, y0 * SCALE : y1 * SCALE, x0 * SCALE : x1 * SCALE] = res[:, :, oy : oy + (y1 - y0) * SCALE, ox : ox + (x1 - x0) * SCALE]
            n += 1
            if progress:
                progress(n, total)
    return out.squeeze(0).clamp_(0, 1).mul_(255).round_().byte().permute(1, 2, 0).numpy()
