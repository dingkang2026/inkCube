#!/usr/bin/env python3
"""color-conv — color conversion CLI (standard library only).

Subcommands print a single JSON object on stdout so the calling agent can
parse results without guessing at formatting.
"""
import argparse
import json
import sys


def _parse_hex(value: str) -> tuple[int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        raise argparse.ArgumentTypeError(f"invalid hex color: {value!r}")
    try:
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid hex color: {value!r}")


def _norm_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


def cmd_hex_to_rgb(args: argparse.Namespace) -> None:
    r, g, b = args.hex  # already parsed via type=_parse_hex
    print(json.dumps({"hex": _norm_hex(r, g, b), "rgb": [r, g, b]}))


def cmd_rgb_to_hex(args: argparse.Namespace) -> None:
    for name in ("r", "g", "b"):
        val = getattr(args, name)
        if not 0 <= val <= 255:
            print(json.dumps({"error": f"{name} must be in 0-255, got {val}"}))
            sys.exit(1)
    print(json.dumps({
        "hex": _norm_hex(args.r, args.g, args.b),
        "rgb": [args.r, args.g, args.b],
    }))


def _relative_luminance(r: int, g: int, b: int) -> float:
    def lin(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def cmd_luminance(args: argparse.Namespace) -> None:
    r, g, b = args.hex  # already parsed via type=_parse_hex
    lum = _relative_luminance(r, g, b)
    print(json.dumps({
        "hex": _norm_hex(r, g, b),
        "luminance": round(lum, 4),
        "level": "light" if lum > 0.5 else "dark",
    }))


def main() -> None:
    parser = argparse.ArgumentParser(prog="color_conv.py", description="Color conversion toolkit")
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("hex-to-rgb", help="Convert #RRGGBB to RGB components")
    p1.add_argument("--hex", required=True, type=_parse_hex, help="Hex color, e.g. #663399")
    p1.set_defaults(func=cmd_hex_to_rgb)

    p2 = sub.add_parser("rgb-to-hex", help="Convert RGB (0-255 each) to hex")
    p2.add_argument("--r", required=True, type=int, help="Red 0-255")
    p2.add_argument("--g", required=True, type=int, help="Green 0-255")
    p2.add_argument("--b", required=True, type=int, help="Blue 0-255")
    p2.set_defaults(func=cmd_rgb_to_hex)

    p3 = sub.add_parser("luminance", help="WCAG relative luminance of a hex color")
    p3.add_argument("--hex", required=True, type=_parse_hex, help="Hex color, e.g. #663399")
    p3.set_defaults(func=cmd_luminance)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
