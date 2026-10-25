"""Build the self-hosted fonts in public/fonts (run once; the output is committed).

Each face gets the Fontsource latin subset plus a tiny file holding only the naira sign, cut from the
latin-ext subset, so prices in ₦ don't pull in a whole second font file. Fonts are OFL-licensed.

    ../ingest/.venv/Scripts/python scripts/fonts.py     (needs fonttools and brotli)
"""

import shutil
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

SITE = Path(__file__).resolve().parents[1]
OUT = SITE / "public" / "fonts"
NAIRA = 0x20A6
FACES = {
    "barlow-condensed-700": "@fontsource/barlow-condensed/files/barlow-condensed-{s}-700-normal.woff2",
    "barlow-condensed-800": "@fontsource/barlow-condensed/files/barlow-condensed-{s}-800-normal.woff2",
    "figtree": "@fontsource-variable/figtree/files/figtree-{s}-wght-normal.woff2",
    "ibm-plex-mono-500": "@fontsource/ibm-plex-mono/files/ibm-plex-mono-{s}-500-normal.woff2",
    "ibm-plex-mono-600": "@fontsource/ibm-plex-mono/files/ibm-plex-mono-{s}-600-normal.woff2",
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, pattern in FACES.items():
        latin = SITE / "node_modules" / pattern.format(s="latin")
        ext = SITE / "node_modules" / pattern.format(s="latin-ext")
        shutil.copyfile(latin, OUT / f"{name}.woff2")
        if NAIRA not in TTFont(ext).getBestCmap():
            print(f"{name}: no naira glyph, system fallback will be used")
            continue
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.layout_features = []
        font = TTFont(ext)
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=[NAIRA])
        sub.subset(font)
        font.flavor = "woff2"
        font.save(OUT / f"{name}-naira.woff2")
    for f in sorted(OUT.iterdir()):
        print(f"{f.name:40} {f.stat().st_size / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
