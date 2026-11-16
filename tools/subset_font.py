#!/usr/bin/env python3
"""Generate a small, self-hosted UI font from an OFL Noto Sans SC source."""

import argparse
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source")
args = parser.parse_args()
site = Path(__file__).resolve().parents[1] / "site"
text = "".join(path.read_text() for path in [site / "index.html", site / "assets/app.js", site / "assets/crypto.js"])
text += "".join(chr(value) for value in range(32, 127))
font = TTFont(args.source)
options = subset.Options()
options.flavor = "woff2"
subsetter = subset.Subsetter(options=options)
subsetter.populate(text=text)
subsetter.subset(font)
for record in font["name"].names:
    if record.nameID in (1, 4, 6, 16):
        record.string = "YXMonitorSans".encode(record.getEncoding())
font.flavor = "woff2"
output = site / "assets/fonts/noto-sans-sc.woff2"
output.parent.mkdir(parents=True, exist_ok=True)
font.save(output)
print("Generated UI font: %d bytes" % output.stat().st_size)
