# CSS Named Color Reference

Name → hex mapping for common CSS named colors. The `color_conv.py` script
does not know color names; look the hex up here first, then convert it with
the `hex-to-rgb` / `luminance` subcommands.

Names are matched case-insensitively; spaces in names are single spaces.

| Name | Hex |
|------|-----|
| aliceblue | #F0F8FF |
| antiquewhite | #FAEBD7 |
| aqua | #00FFFF |
| black | #000000 |
| blue | #0000FF |
| cadetblue | #5F9EA0 |
| crimson | #DC143C |
| darkcyan | #008B8B |
| darkmagenta | #8B008B |
| darkslategray | #2F4F4F |
| dimgray | #696969 |
| firebrick | #B22222 |
| forestgreen | #228B22 |
| fuchsia | #FF00FF |
| gold | #FFD700 |
| goldenrod | #DAA520 |
| gray | #808080 |
| green | #008000 |
| hotpink | #FF69B4 |
| indigo | #4B0082 |
| ivory | #FFFFF0 |
| khaki | #F0E68C |
| lavender | #E6E6FA |
| lime | #00FF00 |
| maroon | #800000 |
| midnightblue | #191970 |
| navy | #000080 |
| olive | #808000 |
| orange | #FFA500 |
| orangered | #FF4500 |
| plum | #DDA0DD |
| purple | #800080 |
| rebeccapurple | #663399 |
| red | #FF0000 |
| royalblue | #4169E1 |
| salmon | #FA8072 |
| sandybrown | #F4A460 |
| seagreen | #2E8B57 |
| sienna | #A0522D |
| silver | #C0C0C0 |
| slateblue | #6A5ACD |
| slategray | #708090 |
| steelblue | #4682B4 |
| teal | #008080 |
| thistle | #D8BFD8 |
| tomato | #FF6347 |
| turquoise | #40E0D0 |
| violet | #EE82EE |
| wheat | #F5DEB3 |
| white | #FFFFFF |
| yellow | #FFFF00 |

Note: CSS defines the keyword as `rebeccapurple` (one word). When the user
writes "rebecca purple", "RebeccaPurple" etc., treat it as `rebeccapurple`.

If a name is not listed here, it is not a CSS named color — tell the user
instead of guessing a hex value.
