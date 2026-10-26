# SPDX-License-Identifier: GPL-2.0-or-later
"""The ztOS GSI: one aarch64 root filesystem per release, shared by every
phone. Taken from a path, a URL or a release version, checked against the
.sha256 published next to it, and cached in ~/.cache/ztconflo/gsi/<version>/."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import Exit

# where released GSIs are published; {version} is filled in
GSI_URL = ("https://github.com/Zerone-Laboratories/ztos-build/releases/download/"
           "v{version}/ztos-gsi-{version}-aarch64.tar.zst")
META = "usr/share/ztos/gsi.json"
_NAME = re.compile(r"^ztos-gsi-(.+)-aarch64\.tar\.(zst|gz)$")


@dataclass
class Gsi:
    version: str
    path: Path                     # the tarball (in the cache, or the given file on a dry run)
    sha256: str
    source: str                    # what --gsi said
    meta: dict = field(default_factory=dict)
    verified: bool = False         # a published .sha256 matched

    @property
    def required(self) -> list[str]:
        return self.meta.get("ial", {}).get("required", [])

    @property
    def interfaces(self) -> dict:
        return self.meta.get("ial", {}).get("interfaces", {})

    @property
    def kernel_contract(self) -> dict:
        return self.meta.get("kernel_contract", {})

    def record(self) -> dict:
        return {"version": self.version, "sha256": self.sha256, "source": self.source, "verified": self.verified,
                "file": self.path.name}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_meta(tarball: Path) -> dict:
    """/usr/share/ztos/gsi.json from inside the tarball, without unpacking it."""
    tarball = Path(tarball)
    if tarball.name.endswith(".zst") and sys.version_info < (3, 14):
        # tarfile reads zstd only from Python 3.14; GNU tar does it by itself
        r = subprocess.run(["tar", "--zstd", "-xOf", str(tarball), "--wildcards", "*" + META],
                           capture_output=True)
        if r.returncode != 0 or not r.stdout:
            raise Exit(Exit.BUILD_FAILED, f"{tarball.name} has no /{META}: not a ztOS GSI")
        return json.loads(r.stdout)
    mode = "r:zst" if tarball.name.endswith(".zst") else "r:*"
    with tarfile.open(tarball, mode) as tar:
        for member in tar:
            if member.name.lstrip("./") == META and member.isfile():
                return json.load(tar.extractfile(member))
    raise Exit(Exit.BUILD_FAILED, f"{tarball.name} has no /{META}: not a ztOS GSI")


def _published_sha(text: str) -> str:
    """`sha256sum` output: the hash, then optionally the file name."""
    words = text.split()
    return words[0].lower() if words else ""


def _download(url: str, dest: Path, log) -> None:
    log(f"    downloading {url}")
    part = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f)
    except (urllib.error.URLError, OSError) as e:
        part.unlink(missing_ok=True)
        raise Exit(Exit.BUILD_FAILED, f"cannot download the GSI from {url}: {e}") from e
    part.rename(dest)


def _sidecar(spec: str, local: Path | None) -> str:
    """The published .sha256 next to the GSI, or "" when there is none."""
    if local is not None:
        side = local.with_name(local.name + ".sha256")
        return _published_sha(side.read_text()) if side.is_file() else ""
    try:
        with urllib.request.urlopen(spec + ".sha256", timeout=30) as r:
            return _published_sha(r.read().decode())
    except (urllib.error.URLError, OSError):
        return ""


def _cached(cache: Path, version: str) -> Path | None:
    folder = cache / version
    hits = sorted(folder.glob(f"ztos-gsi-{version}-aarch64.tar.*")) if folder.is_dir() else []
    hits = [h for h in hits if not h.name.endswith((".sha256", ".part"))]
    return hits[0] if hits else None


def fetch(spec: str, cache: Path, dry_run: bool = False, log=print) -> Gsi:
    """--gsi as a path, an http(s) URL, or a version to find in the cache or
    download from GSI_URL. A mismatch with the published .sha256 stops."""
    local, url = None, None
    if "://" in spec:
        if not spec.startswith(("http://", "https://")):
            raise Exit(Exit.BUILD_FAILED, f"--gsi {spec}: only http, https or a local file")
        url = spec
    elif Path(spec).expanduser().is_file():
        local = Path(spec).expanduser().resolve()
    elif "/" in spec or spec.endswith((".zst", ".gz")):
        raise Exit(Exit.BUILD_FAILED, f"--gsi {spec}: no such file")
    else:
        hit = _cached(cache, spec)
        if hit is not None:
            local = hit
        else:
            url = GSI_URL.format(version=spec)

    name = Path(local.name if local else url.rsplit("/", 1)[-1])
    m = _NAME.match(name.name)
    if url and dry_run:
        version = m.group(1) if m else spec
        log(f"    (would download {url} and its .sha256 into {cache / version}/)")
        return Gsi(version, cache / version / name.name, "", spec)

    cached = local is not None and local.resolve().parent.parent == Path(cache).resolve()
    published = "" if cached else _sidecar(url or spec, local)
    if url:
        version = m.group(1) if m else ""
        stage = cache / (version or "download")
        stage.mkdir(parents=True, exist_ok=True)
        _download(url, stage / name.name, log)
        local = stage / name.name

    digest = sha256_of(local)
    if cached:
        side = local.with_name(local.name + ".sha256")
        if side.is_file() and _published_sha(side.read_text()) != digest:
            raise Exit(Exit.BUILD_FAILED, f"the cached GSI changed since it was fetched: {local}")
        verified = (local.parent / "verified").is_file()
    else:
        if published and published != digest:
            raise Exit(Exit.BUILD_FAILED, f"the GSI does not match its published sha256:\n  published {published}\n"
                                          f"  file      {digest} ({local})")
        if not published:
            log(f"    note: no .sha256 published next to {name.name}; its hash is recorded, not verified")
        verified = bool(published)
    meta = read_meta(local)
    version = str(meta.get("version") or (m.group(1) if m else ""))
    if not version:
        raise Exit(Exit.BUILD_FAILED, f"{local.name}: gsi.json names no version")

    gsi = Gsi(version, local, digest, spec, meta, verified)
    if cached:
        return gsi
    folder = cache / version
    if dry_run:
        log(f"    (would cache {local.name} in {folder}/)")
        return gsi
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / local.name
    if url:
        stage = local.parent
        local.rename(dest)
        if stage != folder and not any(stage.iterdir()):
            stage.rmdir()
    else:
        shutil.copy2(local, dest)
    dest.with_name(dest.name + ".sha256").write_text(f"{digest}  {dest.name}\n")
    (folder / "gsi.json").write_text(json.dumps(meta, indent=2) + "\n")
    if verified:
        (folder / "verified").write_text(f"{published}\n")
    gsi.path = dest
    log(f"    GSI {version} cached in {folder}/")
    return gsi
