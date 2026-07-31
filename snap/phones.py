"""Iranian mobile number normalization.

The phone number *is* the roll identity (one roll per number), so every spelling
of the same number must collapse to one canonical form — otherwise `09123456789`
and `+989123456789` would hand the same guest two separate rolls.

Canonical form is `09` + 9 digits.
"""

import re

# Guests type on Persian keyboards, so numbers arrive in Persian (۰۱۲) or
# Arabic-Indic (٠١٢) digits as often as ASCII.
_DIGIT_MAP = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)

# Anything a human might use as a separator: spaces, dashes, dots, parens.
_SEPARATORS = re.compile(r"[\s\-().]")

_CANONICAL = re.compile(r"^09\d{9}$")


def normalize_phone(raw):
    """Return the canonical `09xxxxxxxxx` form, or None if not a valid mobile.

    Accepts `09xxxxxxxxx`, `+989xxxxxxxxx`, `00989xxxxxxxxx`, `989xxxxxxxxx`
    and `9xxxxxxxxx`, in ASCII, Persian, or Arabic-Indic digits.
    """
    if not raw:
        return None

    s = str(raw).translate(_DIGIT_MAP)
    s = _SEPARATORS.sub("", s)

    # Strip the country code in any of its spellings down to a bare `9…`.
    for prefix in ("+98", "0098", "98"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    else:
        # Not country-coded: drop the trunk `0` if present.
        if s.startswith("0"):
            s = s[1:]

    s = "0" + s
    return s if _CANONICAL.match(s) else None
