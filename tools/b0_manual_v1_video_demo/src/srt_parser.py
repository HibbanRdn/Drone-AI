from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any


TIMESTAMP_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{3})"
)
BLOCK_RE = re.compile(
    r"(?ms)^\s*(?P<index>\d+)\s*\n"
    r"(?P<timing>[^\n]*-->[^\n]*)\n"
    r"(?P<text>.*?)(?=\n\s*\n\s*\d+\s*\n|\Z)"
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
DATETIME_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?\b")
KEY_VALUE_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9_ ]*?)\s*:\s*"
    r"(.+?)(?=\s+[A-Za-z][A-Za-z0-9_ ]*?\s*:|[\]\[,\n]|$)"
)
NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


FIELD_ALIASES = {
    "framecnt": "frame_counter",
    "frame_count": "frame_counter",
    "difftime": "diff_time_ms",
    "iso": "iso",
    "shutter": "shutter",
    "fnum": "aperture",
    "aperture": "aperture",
    "ev": "exposure",
    "exposure": "exposure",
    "focal_len": "focal_length",
    "focal_length": "focal_length",
    "dzoom_ratio": "digital_zoom",
    "zoom": "digital_zoom",
    "latitude": "latitude",
    "lat": "latitude",
    "longitude": "longitude",
    "lon": "longitude",
    "lng": "longitude",
    "rel_alt": "relative_altitude",
    "relative_altitude": "relative_altitude",
    "abs_alt": "absolute_altitude",
    "absolute_altitude": "absolute_altitude",
    "gps_alt": "gps_altitude",
    "gps_altitude": "gps_altitude",
    "baro_alt": "barometer_altitude",
    "barometer_altitude": "barometer_altitude",
    "heading": "heading",
    "yaw": "yaw",
    "pitch": "pitch",
    "roll": "roll",
    "gb_yaw": "gimbal_yaw",
    "gimbal_yaw": "gimbal_yaw",
    "gb_pitch": "gimbal_pitch",
    "gimbal_pitch": "gimbal_pitch",
    "gb_roll": "gimbal_roll",
    "gimbal_roll": "gimbal_roll",
    "home_distance": "home_distance",
    "home_dist": "home_distance",
    "flight_speed": "flight_speed",
    "speed": "flight_speed",
}

STRING_FIELDS = {"shutter"}
INTEGER_FIELDS = {"frame_counter", "iso", "diff_time_ms"}


@dataclass
class SRTEntry:
    index: int
    start_seconds: float
    end_seconds: float
    raw_text: str
    date_time: str | None = None
    frame_counter: int | None = None
    diff_time_ms: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    relative_altitude: float | None = None
    absolute_altitude: float | None = None
    gps_altitude: float | None = None
    barometer_altitude: float | None = None
    heading: float | None = None
    yaw: float | None = None
    pitch: float | None = None
    roll: float | None = None
    gimbal_yaw: float | None = None
    gimbal_pitch: float | None = None
    gimbal_roll: float | None = None
    iso: int | None = None
    shutter: str | None = None
    aperture: float | None = None
    focal_length: float | None = None
    digital_zoom: float | None = None
    exposure: float | None = None
    home_distance: float | None = None
    flight_speed: float | None = None
    extras: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def altitude(self) -> float | None:
        return self.relative_altitude if self.relative_altitude is not None else self.absolute_altitude


def timestamp_to_seconds(value: str) -> float:
    normalized = value.replace(",", ".")
    hours, minutes, seconds = normalized.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _convert_value(field_name: str, raw_value: str) -> object | None:
    value = raw_value.strip()
    if field_name in STRING_FIELDS:
        return value
    number = NUMBER_RE.search(value)
    if not number:
        return None
    if field_name in INTEGER_FIELDS:
        return int(float(number.group(0)))
    return float(number.group(0))


def _detect_encoding(raw: bytes) -> str:
    if all(byte < 128 for byte in raw):
        return "us-ascii"
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin-1"


def parse_srt(path: str | Path) -> tuple[list[SRTEntry], dict[str, Any]]:
    srt_path = Path(path).expanduser().resolve()
    raw_bytes = srt_path.read_bytes()
    encoding = _detect_encoding(raw_bytes)
    text = raw_bytes.decode(encoding)
    entries: list[SRTEntry] = []
    unparsable: list[dict[str, object]] = []

    for match in BLOCK_RE.finditer(text):
        timing = TIMESTAMP_RE.search(match.group("timing"))
        if not timing:
            unparsable.append({"index": int(match.group("index")), "reason": "invalid_timing"})
            continue
        raw_text = match.group("text").strip()
        plain_text = html.unescape(HTML_TAG_RE.sub("", raw_text))
        values: dict[str, Any] = {}
        extras: dict[str, str] = {}
        date_match = DATETIME_RE.search(plain_text)
        if date_match:
            value = date_match.group(0)
            try:
                datetime.fromisoformat(value.replace(" ", "T"))
                values["date_time"] = value
            except ValueError:
                extras["unparsed_date_time"] = value
        for item in KEY_VALUE_RE.finditer(plain_text):
            raw_key, raw_value = item.group(1), item.group(2)
            key = _normalize_key(raw_key)
            canonical = FIELD_ALIASES.get(key)
            if canonical:
                converted = _convert_value(canonical, raw_value)
                if converted is not None:
                    values[canonical] = converted
            else:
                extras[key] = raw_value.strip()
        entries.append(
            SRTEntry(
                index=int(match.group("index")),
                start_seconds=timestamp_to_seconds(timing.group("start")),
                end_seconds=timestamp_to_seconds(timing.group("end")),
                raw_text=raw_text,
                extras=extras,
                **values,
            )
        )

    parsed_spans = [(m.start(), m.end()) for m in BLOCK_RE.finditer(text)]
    if not entries and text.strip():
        unparsable.append({"index": None, "reason": "no_structural_blocks_parsed"})
    telemetry_fields = [
        item.name
        for item in fields(SRTEntry)
        if item.name
        not in {"index", "start_seconds", "end_seconds", "raw_text", "extras"}
    ]
    counts = {
        name: sum(getattr(entry, name) is not None for entry in entries)
        for name in telemetry_fields
    }
    report: dict[str, Any] = {
        "path": str(srt_path),
        "encoding": encoding,
        "file_size_bytes": srt_path.stat().st_size,
        "block_count": len(entries),
        "structural_matches": len(parsed_spans),
        "timestamp_start_seconds": entries[0].start_seconds if entries else None,
        "timestamp_end_seconds": entries[-1].end_seconds if entries else None,
        "coverage_seconds": entries[-1].end_seconds - entries[0].start_seconds if entries else 0.0,
        "fields_found": [name for name, count in counts.items() if count],
        "field_success_count": counts,
        "field_success_rate": {
            name: (count / len(entries) if entries else 0.0) for name, count in counts.items()
        },
        "unparsable_entries": unparsable,
        "first_three_entries": [entry.to_dict() for entry in entries[:3]],
        "last_three_entries": [entry.to_dict() for entry in entries[-3:]],
    }
    return entries, report


def write_srt_report(report: dict[str, Any], destination: str | Path) -> None:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
