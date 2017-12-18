from ctypes import (
    c_int, c_ushort, c_ubyte, c_void_p, c_wchar_p, POINTER, CFUNCTYPE, cast,
    Structure, memmove, py_object,
)
import sys
import os
import array

from ._compat import ensure_text, iterbytes, PY3
from . import memory


TA_Info_Func_Proto = CFUNCTYPE(
    c_int,                      # (return value)
    c_ushort,                   # platform_id
    c_ushort,                   # encoding_id
    c_ushort,                   # language_id
    c_ushort,                   # name_id
    POINTER(c_ushort),          # str_len
    POINTER(POINTER(c_ubyte)),  # str
    c_void_p                    # info_data
)


TA_Info_Post_Func_Proto = CFUNCTYPE(c_int, c_void_p)


INFO_PREFIX = u"; ttfautohint"


def build_info_string(version, detailed_info=True, **options):
    s = INFO_PREFIX + " (v%s)" % version

    if not detailed_info:
        return s

    if options["dehint"]:
        s += " -d"
        return s

    s += " -l %d" % options["hinting_range_min"]
    s += " -r %d" % options["hinting_range_max"]
    s += " -G %d" % options["hinting_limit"]
    s += " -x %d" % options["increase_x_height"]
    if options["fallback_stem_width"]:
        s += " -H %d" % options["fallback_stem_width"]
    s += " -D %s" % ensure_text(options["default_script"])
    s += " -f %s" % ensure_text(options["fallback_script"])

    control_name = options.pop("control_name", None)
    if control_name:
        s += ' -m "%s"' % os.path.basename(
            ensure_text(control_name, sys.getfilesystemencoding()))

    reference_name = options.get("reference_name")
    if reference_name:
        s += ' -R "%s"' % os.path.basename(
            ensure_text(reference_name, sys.getfilesystemencoding()))

    if options["reference_index"]:
        s += " -Z %d" % options["reference_index"]

    strong_stem_width = ""
    if options["gray_strong_stem_width"]:
        strong_stem_width += "g"
    if options["gdi_cleartype_strong_stem_width"]:
        strong_stem_width += "G"
    if options["dw_cleartype_strong_stem_width"]:
        strong_stem_width += "D"
    s += " -w %s" % strong_stem_width or '""'

    if options["windows_compatibility"]:
        s += " -W"
    if options["adjust_subglyphs"]:
        s += " -p"
    if options["hint_composites"]:
        s += " -c"
    if options["symbol"]:
        s += " -s"
    if options["fallback_scaling"]:
        s += " -S"
    if options["TTFA_info"]:
        s += " -t"
    x_excepts = ensure_text(options["x_height_snapping_exceptions"])
    s += ' -X "%s"' % x_excepts

    return s


class InfoData(Structure):

    _fields_ = [
        ("info_string", c_wchar_p),
        ("family_suffix", c_wchar_p),
        ("family_data", py_object),
    ]

    def __init__(self, info_string=None, family_suffix=None, family_data=None):
        if family_data is None:
            family_data = {}
        super(InfoData, self).__init__(info_string, family_suffix, family_data)


class MutableByteString(object):

    max_length = 0xFFFF
    StringPtr = POINTER(POINTER(c_ubyte))
    LengthPtr = POINTER(c_ushort)

    def __init__(self, string_p, length_p):
        if not isinstance(string_p, self.StringPtr):
            raise TypeError("expected %s, found %s" % (
                self.StringPtr.__name__, type(string_p).__name__))
        if not string_p:
            raise ValueError("string_p must not be NULL")
        elif not string_p[0]:
            raise ValueError("string_p[0] must not be NULL")
        self.string_p = string_p
        if not isinstance(length_p, self.LengthPtr):
            raise TypeError("expected %s, found %s" %(
                self.LengthPtr.__name__, type(length_p).__name__))
        if not length_p:
            raise ValueError("length_p must not be NULL")
        self.length_p = length_p

    def __len__(self):
        return self.length_p[0]

    def tobytes(self):
        size = len(self)
        if not size:
            return b""
        a = array.array("B", self.string_p[0][:size])
        # tobytes is PY3 only; the equivalent tostring is deprecated :(
        return a.tobytes() if PY3 else a.tostring()

    def frombytes(self, s):
        new_len = len(s)
        if new_len > self.max_length:
            raise OverflowError("string exceeds the maximum length")
        string_p = self.string_p
        current_len = len(self)
        if new_len > current_len:
            void_p = memory.realloc(string_p[0], new_len)
            if not void_p:  # pragma: no cover
                # realloc failed (unlikely)
                raise MemoryError()
            string = string_p[0] = cast(void_p, POINTER(c_ubyte))
        for i, b in enumerate(iterbytes(s)):
            string[i] = b
        self.length_p[0] = new_len


def info_name_id_5(platform_id, encoding_id, str_len_p, string_p, data):
    name_string = MutableByteString(string_p, str_len_p)
    string = name_string.tobytes()

    if (platform_id == 1 or
            (platform_id == 3 and not (
                encoding_id == 1 or encoding_id == 10))):
        # one-byte or multi-byte encodings
        encoding = "ascii"
        offset = 1
    else:
        # (two-byte) UTF-16BE for everything else
        encoding = "utf-16be"
        offset = 2

    info_string = data.info_string.encode(encoding)
    info_prefix = INFO_PREFIX.encode(encoding)
    semicolon = u";".encode(encoding)
    # if we already have an ttfautohint info string, remove it up to a
    # following `;' character (or end of string)
    start = string.find(info_prefix)
    if start != -1:
        new_string = string[:start] + info_string
        string_end = string[start+offset:]
        last_semicolon_index = string_end.rfind(semicolon)
        if last_semicolon_index != -1:
            new_string += string_end[last_semicolon_index:]
    else:
        new_string = string + info_string

    try:
        name_string.frombytes(new_string)
    except OverflowError:
        # do nothing if the string would become too long
        pass
    except MemoryError:  # pragma: no cover
        # return non-zero in the unlikely event of landing on water
        return 1
    return 0


class Family(object):

    related_name_ids = frozenset([1, 4, 6, 16, 21])

    def __init__(self):
        for name_id in self.related_name_ids:
            setattr(self, "name_id_%d" % name_id, None)


def _info_callback(platform_id, encoding_id, language_id, name_id, str_len_p,
                   string_p, info_data_p):
    # cast void pointer to a pointer to InfoData struct
    data = cast(info_data_p, POINTER(InfoData))[0]

    # if ID is a version string, append our data
    if data.info_string and name_id == 5:
        return info_name_id_5(platform_id,
                              encoding_id,
                              str_len_p,
                              string_p,
                              data)

    # if ID is related to a family name, collect the data
    if data.family_suffix and name_id in Family.related_name_ids:
        triplet = (platform_id, encoding_id, language_id)
        family = data.family_data.setdefault(triplet, Family())
        name_string = MutableByteString(string_p, str_len_p)
        setattr(family, "name_id_%d" % name_id, name_string)

    return 0


info_callback = TA_Info_Func_Proto(_info_callback)


def insert_suffix(suffix, family_name, name_string):
    # TODO insert suffix after substring, not always at the end
    new_string = family_name + suffix
    try:
        name_string.frombytes(new_string)
    except (OverflowError, MemoryError):
        pass  # warn?


def _info_post_callback(info_data_p):
    # cast void pointer to a pointer to InfoData struct
    data = cast(info_data_p, POINTER(InfoData))[0]
    family_data = data.family_data

    family_suffix = data.family_suffix.encode("ascii")
    family_suffix_wide = data.family_suffix.encode("utf-16be")

    family_suffix_stripped = data.family_suffix.replace(" ", "")
    family_ps_suffix = family_suffix_stripped.encode("ascii")
    family_ps_suffix_wide = family_suffix_stripped.encode("utf-16be")

    for family in family_data.values():
        if family.name_id_16:
            family.family_name = family.name_id_16.tobytes()
        elif family.name_id_1:
            family.family_name = family.name_id_1.tobytes()

    for (plat_id, enc_id, lang_id), family in family_data.items():
        if hasattr(family, "family_name"):
            family_name = family.family_name
        else:
            for (pid, eid, _), f in family_data.items():
                if (pid == family.platform_id and
                        eid == family.encoding_id and
                        hasattr(f, "family_name")):
                    family_name = f.family_name
                    break
            else:
                continue
        if (plat_id == 1 or (plat_id == 3 and
                not (enc_id == 1 or enc_id == 10))):
            suffix = family_suffix
            ps_suffix = family_ps_suffix
            family_ps_name = family_name.replace(b" ", b"")
        else:
            suffix = family_suffix_wide
            ps_suffix = family_ps_suffix_wide
            family_ps_name = family_name.replace(b"\0 ", b"")

        if family.name_id_1:
            insert_suffix(suffix, family_name, family.name_id_1)
        if family.name_id_4:
            insert_suffix(suffix, family_name, family.name_id_4)
        if family.name_id_6:
            insert_suffix(ps_suffix, family_ps_name, family.name_id_6)
        if family.name_id_16:
            insert_suffix(suffix, family_name, family.name_id_16)
        if family.name_id_21:
            insert_suffix(suffix, family_name, family.name_id_21)

    return 0


info_post_callback = TA_Info_Post_Func_Proto(_info_post_callback)
