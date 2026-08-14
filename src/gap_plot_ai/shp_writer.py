from __future__ import annotations

"""Pure-Python ESRI Shapefile writer.
Zero external dependencies.
Handles: SHP (geometry), SHX (index), DBF (attributes), PRJ (projection)."""

import struct
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SHAPE_TYPE_POINT = 1
SHAPE_TYPE_POLYLINE = 3
SHAPE_TYPE_POLYGON = 5

DBF_TYPE_CHAR = 'C'
DBF_TYPE_NUMERIC = 'N'
DBF_TYPE_FLOAT = 'F'
DBF_TYPE_DATE = 'D'
DBF_TYPE_INTEGER = 'I'


class _ShpWriter:
    def __init__(self, path: Path):
        self._f = open(str(path), 'wb')
        self._f.write(b'\x00' * 100)
        self._record_count = 0
        self._content_length = 50  # header is 100 bytes = 50 words

    def write_point(self, x: float, y: float):
        # SHP record header: BIG-endian record number and content length
        record = struct.pack('>i', self._record_count + 1)
        record += struct.pack('>i', 10)  # content length in words (Point = 20 bytes)
        record += struct.pack('<i', SHAPE_TYPE_POINT)
        record += struct.pack('<dd', x, y)
        self._f.write(record)
        self._record_count += 1
        self._content_length += 14  # 8 (record header) + 20 (point content) = 28 bytes = 14 words

    def finalize(self, x_min: float, y_min: float, x_max: float, y_max: float):
        self._f.seek(0)
        header = struct.pack('>i', 9994)  # file code (big-endian)
        header += b'\x00' * 20  # unused
        header += struct.pack('>i', self._content_length)  # file length in 16-bit words (big-endian)
        header += struct.pack('<i', 1000)  # version
        header += struct.pack('<i', SHAPE_TYPE_POINT)  # shape type
        header += struct.pack('<dddd', x_min, y_min, x_max, y_max)  # bounding box
        header += struct.pack('<dddd', 0.0, 0.0, 0.0, 0.0)  # z/m range
        self._f.write(header)
        self._f.close()


class _ShxWriter:
    def __init__(self, path: Path):
        self._f = open(str(path), 'wb')
        self._f.write(b'\x00' * 100)
        self._record_count = 0
        self._content_length = 50

    def write_record(self, offset_bytes: int, content_length_bytes: int):
        # SHX record: BIG-endian offset and content length (in words)
        offset_words = offset_bytes // 2
        length_words = content_length_bytes // 2
        self._f.write(struct.pack('>ii', offset_words, length_words))
        self._record_count += 1
        self._content_length += 4  # 8 bytes per record = 4 words

    def finalize(self):
        self._f.seek(0)
        header = struct.pack('>i', 9994)
        header += b'\x00' * 20
        header += struct.pack('>i', self._content_length)
        header += struct.pack('<i', 1000)
        header += struct.pack('<i', SHAPE_TYPE_POINT)
        header += struct.pack('<dddd', 0, 0, 0, 0)
        header += struct.pack('<dddd', 0, 0, 0, 0)
        self._f.write(header)
        self._f.close()


class _DbfWriter:
    def __init__(self, path: Path, fields: List[Tuple[str, str, int, int]]):
        self._f = open(str(path), 'wb')
        self._fields = fields
        self._record_count = 0
        self._record_length = 1  # deletion flag
        self._field_specs = []
        for name, typ, length, decimals in fields:
            name = name[:10].upper()
            self._field_specs.append((name, typ, length, decimals))
            if typ == DBF_TYPE_CHAR:
                self._record_length += length
            elif typ in (DBF_TYPE_NUMERIC, DBF_TYPE_FLOAT):
                self._record_length += length
            elif typ == DBF_TYPE_INTEGER:
                self._record_length += 4
            elif typ == DBF_TYPE_DATE:
                self._record_length += 8

        header_size = 32 + len(fields) * 32 + 1
        now = datetime.now()
        header = struct.pack('<BBBBIHH20x',
            3,  # version
            now.year % 100, now.month, now.day,
            0,  # num_records (filled in finalize)
            header_size,
            self._record_length)
        self._f.write(header)

        for name, typ, length, decimals in self._field_specs:
            field_desc = name.ljust(11, '\x00').encode('ascii')
            field_desc += typ.encode('ascii')
            field_desc += b'\x00' * 4
            field_desc += struct.pack('<BB', length, decimals)
            field_desc += b'\x00' * 14
            self._f.write(field_desc)

        self._f.write(b'\x0d')  # terminator
        self._header_written = True

    def write_record(self, values: Dict[str, Any]):
        self._f.write(b' ')  # not deleted
        norm = {k.upper(): v for k, v in values.items()}
        for name, typ, length, decimals in self._field_specs:
            val = norm.get(name, values.get(name, ''))
            if typ == DBF_TYPE_CHAR:
                s = str(val)[:length]
                self._f.write(s.ljust(length, ' ').encode('ascii', errors='replace'))
            elif typ in (DBF_TYPE_NUMERIC, DBF_TYPE_FLOAT):
                if val is None or val == '':
                    self._f.write(b' ' * length)
                else:
                    try:
                        fmt = '%%%d.%df' % (length, decimals)
                        s = fmt % float(val)
                    except (ValueError, TypeError):
                        self._f.write(b' ' * length)
                        self._record_count += 1
                        continue
                    if len(s) > length:
                        s = s[:length]
                    self._f.write(s.encode('ascii'))
            elif typ == DBF_TYPE_INTEGER:
                v = int(val) if val is not None else 0
                self._f.write(struct.pack('<i', v))
            elif typ == DBF_TYPE_DATE:
                if val is None:
                    self._f.write(b' ' * 8)
                elif isinstance(val, date):
                    self._f.write(val.strftime('%Y%m%d').encode('ascii'))
                else:
                    self._f.write(str(val)[:8].ljust(8, ' ').encode('ascii'))
        self._record_count += 1

    def finalize(self):
        self._f.seek(4)
        self._f.write(struct.pack('<i', self._record_count))
        self._f.close()


def write_shapefile(
    output_dir: Path,
    base_name: str,
    points: List[Tuple[float, float]],
    fields: List[Tuple[str, str, int, int]],
    records: List[Dict[str, Any]],
    prj_wkt: str,
):
    n = len(points)
    if n != len(records):
        raise ValueError("points (%d) and records (%d) must have same length" % (n, len(records)))

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    shp_path = output_dir / (base_name + '.shp')
    shx_path = output_dir / (base_name + '.shx')
    dbf_path = output_dir / (base_name + '.dbf')
    prj_path = output_dir / (base_name + '.prj')

    if n == 0:
        x_min = y_min = x_max = y_max = 0.0
    else:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)

    shp = _ShpWriter(shp_path)
    shx = _ShxWriter(shx_path)
    dbf = _DbfWriter(dbf_path, fields)

    for i, (x, y) in enumerate(points):
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        record_start = shp._f.tell()
        shp.write_point(x, y)
        record_end = shp._f.tell()
        content_len = record_end - record_start - 8  # content only, excluding 8-byte record header
        shx.write_record(record_start, content_len)
        dbf.write_record(records[i] if i < len(records) else {})

    shp.finalize(x_min, y_min, x_max, y_max)
    shx.finalize()
    dbf.finalize()

    with open(str(prj_path), 'w', encoding='ascii') as f:
        f.write(prj_wkt)


def wgs84_prj() -> str:
    return 'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]'
