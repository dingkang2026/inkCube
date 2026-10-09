---
name: color-conv
description: "Color conversion toolkit — converts colors between HEX and RGB, computes WCAG relative luminance, and resolves named CSS colors to hex codes. Use when the user asks to convert a color value, check whether a color is light or dark, or translate a color name like 'rebecca purple' into a hex code."
license: MIT
compatibility: "Requires Python 3.10+; uses only the standard library (argparse, colorsys). No third-party packages."
---

# Color Conversion Toolkit

Convert color values between HEX and RGB, compute WCAG relative luminance,
and resolve named CSS colors.

## When to use

- Convert a hex code like `#663399` to RGB, or RGB values back to hex
- Check a color's relative luminance / whether it reads as light or dark
- Translate a named CSS color (e.g. "rebecca purple") into a hex code

## Subcommands

Run `scripts/color_conv.py` with one of:

| Subcommand | Arguments | Output |
|------------|-----------|--------|
| `hex-to-rgb` | `--hex <#RRGGBB>` (required) | RGB components |
| `rgb-to-hex` | `--r --g --b <0-255>` (all required) | HEX code |
| `luminance` | `--hex <#RRGGBB>` (required) | relative luminance + light/dark |

Every subcommand prints a single JSON object on stdout.

## Named colors (progressive disclosure)

The script does NOT know color names. To resolve a named color:

1. Read `references/COLOR_NAMES.md` and look up the name → hex mapping.
2. Feed the hex to `hex-to-rgb` or `luminance` as needed.

Only read the reference file when a color name is actually involved —
do not load it for plain hex/RGB conversions.

## Examples

**User**: "把 #663399 转成 RGB"
**Run**: `scripts/color_conv.py hex-to-rgb --hex "#663399"`

**User**: "rebecca purple 是什么颜色代码？亮度如何？"
**Steps**:
1. Read `references/COLOR_NAMES.md` → `rebecca purple` maps to `#663399`
2. Run `scripts/color_conv.py luminance --hex "#663399"`
3. Answer with the hex code and the luminance result
