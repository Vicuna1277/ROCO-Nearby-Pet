# -*- coding: utf-8 -*-
"""RoCo nearby protocol helpers, derived from the supplied v0.1

- Low-level helpers only. Offline/live input and state management are in other modules.
- 复现工具中观察到的 TCP 重组、TGCP 分帧、AES-128-CBC 和部分 protobuf 字段解析。
- 不导出/显示会话密钥，不默认导出原始解密负载。

依赖：Python 3.10+；AES 解码需要 cryptography（pip install cryptography）。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import struct
import sys
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Iterable

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    CRYPTO_AVAILABLE = True
except Exception:
    CRYPTO_AVAILABLE = False

APP_NAME = "洛克王国 PCAP 离线分析器"
DEFAULT_PORT = 8195
TGCP_MAGIC = b"\x33\x66"
TGCP_ZERO_PREFIX = b"\x00" * 6
MAX_FRAME_SIZE = 16 * 1024 * 1024
NEARBY_OPCODES = {330}

# Original tool's built-in cfgId -> Chinese name table, bundled separately when available.
NAME_TABLE = {}
try:
    _name_path = Path(__file__).with_name("species_names.json")
    if _name_path.exists():
        NAME_TABLE = {int(k): str(v) for k, v in json.loads(_name_path.read_text(encoding="utf-8")).items()}
except Exception:
    NAME_TABLE = {}


@dataclass
class TcpSegment:
    ts: float
    order: int
    src: tuple[str, int]
    dst: tuple[str, int]
    seq: int
    payload: bytes
    syn: bool
    ack_flag: bool
    flags: int
    wire_len: int


@dataclass
class TgcpFrame:
    ts: float
    order: int
    direction_idx: int  # 0 means endpoint[0] -> endpoint[1], 1 means endpoint[1] -> endpoint[0]
    raw: bytes
    command: int
    sequence: int
    header_length: int
    body_length: int
    body: bytes


@dataclass
class Flow:
    endpoints: tuple[tuple[str, int], tuple[str, int]]
    segments: list[TcpSegment] = field(default_factory=list)
    frames: list[TgcpFrame] = field(default_factory=list)
    packets: int = 0
    payload_packets: int = 0
    syn_initiator_idx: Optional[int] = None
    server_idx: Optional[int] = None
    diagnostics: dict = field(default_factory=dict)

    def direction_index(self, src: tuple[str, int]) -> int:
        return 0 if src == self.endpoints[0] else 1

    def label(self, direction_idx: int) -> str:
        return "S→C" if self.server_idx == direction_idx else "C→S"

    def flow_id(self) -> str:
        return f"{self.endpoints[0][0]}:{self.endpoints[0][1]} ⇄ {self.endpoints[1][0]}:{self.endpoints[1][1]}"


class PcapError(Exception):
    pass


def _parse_ipv4(data: bytes, off: int, ts: float, order: int, wire_len: int) -> Optional[TcpSegment]:
    if len(data) < off + 20:
        return None
    vihl = data[off]
    if vihl >> 4 != 4:
        return None
    ihl = (vihl & 0x0F) * 4
    if ihl < 20 or len(data) < off + ihl:
        return None
    total_len = struct.unpack_from("!H", data, off + 2)[0]
    if total_len < ihl + 20:
        return None
    frag = struct.unpack_from("!H", data, off + 6)[0]
    # Non-initial IP fragments cannot be parsed as TCP directly.
    if frag & 0x1FFF:
        return None
    if data[off + 9] != 6:
        return None
    src = ".".join(str(x) for x in data[off + 12:off + 16])
    dst = ".".join(str(x) for x in data[off + 16:off + 20])
    tcp_off = off + ihl
    ip_end = min(len(data), off + total_len)
    if ip_end < tcp_off + 20:
        return None
    sp, dp, seq, ack = struct.unpack_from("!HHII", data, tcp_off)
    data_offset = (data[tcp_off + 12] >> 4) * 4
    flags = data[tcp_off + 13]
    if data_offset < 20 or tcp_off + data_offset > ip_end:
        return None
    payload = data[tcp_off + data_offset:ip_end]
    return TcpSegment(ts, order, (src, sp), (dst, dp), seq, payload,
                      bool(flags & 0x02), bool(flags & 0x10), flags, wire_len)


def _parse_ipv6(data: bytes, off: int, ts: float, order: int, wire_len: int) -> Optional[TcpSegment]:
    if len(data) < off + 40 or data[off] >> 4 != 6:
        return None
    payload_len = struct.unpack_from("!H", data, off + 4)[0]
    end = min(len(data), off + 40 + payload_len)
    next_header = data[off + 6]
    src = ":".join(f"{int.from_bytes(data[off+8+i:off+10+i], 'big'):x}" for i in range(0, 16, 2))
    dst = ":".join(f"{int.from_bytes(data[off+24+i:off+26+i], 'big'):x}" for i in range(0, 16, 2))
    p = off + 40
    # Skip common IPv6 extension headers.
    for _ in range(12):
        if next_header == 6:
            break
        if next_header in (0, 43, 60):  # hop-by-hop, routing, destination options
            if p + 2 > end:
                return None
            nh = data[p]
            size = (data[p + 1] + 1) * 8
        elif next_header == 44:  # fragment header
            if p + 8 > end:
                return None
            nh = data[p]
            frag_field = struct.unpack_from("!H", data, p + 2)[0]
            if frag_field & 0xFFF8:
                return None
            size = 8
        elif next_header == 51:  # AH
            if p + 2 > end:
                return None
            nh = data[p]
            size = (data[p + 1] + 2) * 4
        else:
            return None
        if p + size > end:
            return None
        p += size
        next_header = nh
    if next_header != 6 or p + 20 > end:
        return None
    sp, dp, seq, ack = struct.unpack_from("!HHII", data, p)
    data_offset = (data[p + 12] >> 4) * 4
    flags = data[p + 13]
    if data_offset < 20 or p + data_offset > end:
        return None
    payload = data[p + data_offset:end]
    return TcpSegment(ts, order, (src, sp), (dst, dp), seq, payload,
                      bool(flags & 0x02), bool(flags & 0x10), flags, wire_len)


def parse_network_packet(frame: bytes, linktype: int, ts: float, order: int, wire_len: int) -> Optional[TcpSegment]:
    off = 0
    proto = None
    if linktype == 1:  # Ethernet
        if len(frame) < 14:
            return None
        proto = struct.unpack_from("!H", frame, 12)[0]
        off = 14
        while proto in (0x8100, 0x88A8, 0x9100):
            if len(frame) < off + 4:
                return None
            proto = struct.unpack_from("!H", frame, off + 2)[0]
            off += 4
        if proto == 0x0800:
            return _parse_ipv4(frame, off, ts, order, wire_len)
        if proto == 0x86DD:
            return _parse_ipv6(frame, off, ts, order, wire_len)
        return None
    if linktype in (101, 12):  # raw IP
        if not frame:
            return None
        version = frame[0] >> 4
        return _parse_ipv4(frame, 0, ts, order, wire_len) if version == 4 else _parse_ipv6(frame, 0, ts, order, wire_len) if version == 6 else None
    if linktype == 113:  # Linux cooked capture v1
        if len(frame) < 16:
            return None
        proto = struct.unpack_from("!H", frame, 14)[0]
        off = 16
    elif linktype == 276:  # Linux cooked capture v2
        if len(frame) < 20:
            return None
        proto = struct.unpack_from("!H", frame, 0)[0]
        off = 20
    elif linktype == 0:  # loopback/null
        if len(frame) < 5:
            return None
        # Usually host-endian family; infer from IP version if possible.
        off = 4
        version = frame[off] >> 4
        return _parse_ipv4(frame, off, ts, order, wire_len) if version == 4 else _parse_ipv6(frame, off, ts, order, wire_len) if version == 6 else None
    else:
        return None
    if proto == 0x0800:
        return _parse_ipv4(frame, off, ts, order, wire_len)
    if proto == 0x86DD:
        return _parse_ipv6(frame, off, ts, order, wire_len)
    return None


class TgcpStreamParser:
    def __init__(self, direction_idx: int):
        self.direction_idx = direction_idx
        self.buffer = bytearray()
        self.bad_headers = 0
        self.frames_count = 0

    @staticmethod
    def _u32be(data: bytes | bytearray, off: int) -> int:
        if off + 4 > len(data):
            return 0
        return int.from_bytes(data[off:off + 4], "big")

    def feed(self, data: bytes, ts: float, order: int) -> list[TgcpFrame]:
        if data:
            self.buffer.extend(data)
        out: list[TgcpFrame] = []
        while self.buffer:
            if self.buffer.startswith(TGCP_ZERO_PREFIX):
                del self.buffer[:6]
                continue
            idx = self.buffer.find(TGCP_MAGIC)
            if idx < 0:
                if self.buffer and self.buffer[-1] == TGCP_MAGIC[0]:
                    self.buffer[:] = self.buffer[-1:]
                else:
                    self.buffer.clear()
                break
            if idx > 0:
                del self.buffer[:idx]
            if len(self.buffer) < 21:
                break
            command = (self.buffer[6] << 8) | self.buffer[7]
            header_len = self._u32be(self.buffer, 13)
            body_len = self._u32be(self.buffer, 17)
            total = header_len + body_len
            if header_len < 21 or header_len > MAX_FRAME_SIZE or total < header_len or total > MAX_FRAME_SIZE:
                self.bad_headers += 1
                del self.buffer[:2]
                continue
            if len(self.buffer) < total:
                break
            raw = bytes(self.buffer[:total])
            del self.buffer[:total]
            frame = TgcpFrame(
                ts=ts, order=order, direction_idx=self.direction_idx, raw=raw,
                command=command, sequence=self._u32be(raw, 9),
                header_length=header_len, body_length=body_len,
                body=raw[header_len:]
            )
            out.append(frame)
            self.frames_count += 1
        # Avoid unbounded memory use if the capture is malformed.
        if len(self.buffer) > MAX_FRAME_SIZE:
            self.buffer.clear()
            self.bad_headers += 1
        return out


def read_varint(buf: bytes, pos: int) -> tuple[Optional[int], int]:
    value = 0
    shift = 0
    start = pos
    while pos < len(buf) and shift <= 70:
        b = buf[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not (b & 0x80):
            return value, pos
        shift += 7
    return None, start


def parse_proto_fields(buf: bytes, start: int = 0, max_fields: int = 2000) -> list[dict]:
    fields: list[dict] = []
    pos = start
    while pos < len(buf) and len(fields) < max_fields:
        tag, p2 = read_varint(buf, pos)
        if tag is None:
            break
        number, wire = tag >> 3, tag & 7
        if number <= 0 or wire in (3, 4, 6, 7):
            break
        pos = p2
        if wire == 0:
            value, p3 = read_varint(buf, pos)
            if value is None or p3 <= pos:
                break
            fields.append({"n": number, "w": wire, "v": value})
            pos = p3
        elif wire == 2:
            length, p3 = read_varint(buf, pos)
            if length is None or p3 + length > len(buf):
                break
            fields.append({"n": number, "w": wire, "v": buf[p3:p3 + length]})
            pos = p3 + length
        elif wire == 1:
            if pos + 8 > len(buf):
                break
            fields.append({"n": number, "w": wire, "v": buf[pos:pos + 8]})
            pos += 8
        elif wire == 5:
            if pos + 4 > len(buf):
                break
            fields.append({"n": number, "w": wire, "v": buf[pos:pos + 4]})
            pos += 4
        else:
            break
    return fields


def protobuf_candidate_stats(buf: bytes, start: int) -> tuple[int, int, bool]:
    fields = parse_proto_fields(buf, start)
    if not fields:
        return 0, 0, False
    # Estimate consumed bytes by re-parsing wire format; used only to choose a likely protobuf start.
    pos = start
    count = 0
    while pos < len(buf) and count < 400:
        tag, p2 = read_varint(buf, pos)
        if tag is None:
            break
        n, w = tag >> 3, tag & 7
        if n <= 0 or w in (3, 4, 6, 7):
            break
        pos = p2
        if w == 0:
            v, p3 = read_varint(buf, pos)
            if v is None or p3 <= pos:
                break
            pos = p3
        elif w == 2:
            length, p3 = read_varint(buf, pos)
            if length is None or p3 + length > len(buf):
                break
            pos = p3 + length
        elif w == 1:
            pos += 8
        elif w == 5:
            pos += 4
        else:
            break
        if pos > len(buf):
            break
        count += 1
    return count, pos - start, pos == len(buf)


def extract_proto_payload(plain: bytes) -> Optional[bytes]:
    if not isinstance(plain, (bytes, bytearray)) or len(plain) < 4:
        return None
    best = (0, 0, -1, None)  # consumed, count, start, complete
    upper = min(20, max(8, len(plain) - 1))
    for start in range(8, upper + 1):
        count, consumed, complete = protobuf_candidate_stats(plain, start)
        if count and (consumed > best[0] or (consumed == best[0] and count > best[1]) or (consumed == best[0] and count == best[1] and start > best[2])):
            best = (consumed, count, start, complete)
    if best[1] <= 0 or best[3] is None:
        return None
    return plain[best[2]:]


def values(fields: list[dict], number: int, wire: Optional[int] = None) -> list:
    return [f["v"] for f in fields if f["n"] == number and (wire is None or f["w"] == wire)]


def first_value(fields: list[dict], number: int, wire: Optional[int] = None):
    vs = values(fields, number, wire)
    return vs[0] if vs else None


def signed64(v) -> int:
    if v is None:
        return 0
    v = int(v)
    if v >= (1 << 63):
        v -= 1 << 64
    return v


def parse_pos_message(data: bytes, _depth: int = 0) -> Optional[dict]:
    if _depth > 3:
        return None
    f = parse_proto_fields(data)
    x = first_value(f, 1, 0)
    y = first_value(f, 2, 0)
    z = first_value(f, 3, 0)
    if x is not None and y is not None:
        return {"x": signed64(x), "y": signed64(y), "z": signed64(z or 0)}
    # Scene-teleport messages wrap the coordinate message in field 1.
    wrapped = first_value(f, 1, 2)
    if isinstance(wrapped, bytes):
        pos = parse_pos_message(wrapped, _depth + 1)
        if pos is not None:
            return pos
    return None


def parse_player_position_307(plain: bytes) -> Optional[dict]:
    """解析已验证样本中的 opcode 307 玩家位置字段。"""
    if not isinstance(plain, (bytes, bytearray)) or len(plain) <= 14:
        return None

    # 当前三份抓包中，opcode 307 的 protobuf 根结构从偏移 14 开始。
    fields = parse_proto_fields(bytes(plain[14:]))

    # 根字段 2 是嵌套坐标消息。
    pos_bytes = first_value(fields, 2, 2)
    if not isinstance(pos_bytes, bytes):
        return None

    pos = parse_pos_message(pos_bytes)
    if not pos:
        return None

    x, y = pos.get("x"), pos.get("y")
    if not isinstance(x, int) or not isinstance(y, int):
        return None

    # 仅接受当前样本中合理的世界坐标范围。
    if not (100000 <= x <= 2000000 and
            100000 <= y <= 2000000):
        return None

    return pos



def parse_scene_event(opcode: int, plain: bytes) -> Optional[dict]:
    if len(plain) < 14:
        return None
    data = plain[10:]
    best = None
    for start in range(0, min(16, max(0, len(data) - 1)) + 1):
        fs = parse_proto_fields(data, start)
        if not fs:
            continue
        varints = {f["n"]: signed64(f["v"]) for f in fs if f["w"] == 0}
        if opcode == 348:
            scene = varints.get(12, 0)
            pos_bytes = first_value(fs, 14, 2)
            pos = parse_pos_message(pos_bytes) if isinstance(pos_bytes, bytes) else None
            if 10000 <= scene <= 9999999 and pos:
                score = len(fs) + 20
                if best is None or score > best["score"]:
                    best = {"score": score, "scene_id": scene, "scene_cfg_id": varints.get(11, 0), "from_scene_id": varints.get(2, 0), "room_level": varints.get(31, 0), "pos": pos}
        elif opcode == 338:
            scene = varints.get(3, 0)
            if 10000 <= scene <= 9999999:
                score = len(fs) + (20 if varints.get(2) else 0)
                if best is None or score > best["score"]:
                    best = {"score": score, "scene_id": scene, "scene_cfg_id": varints.get(2, 0)}
    if best:
        best.pop("score", None)
    return best


def decrypt_aes_cbc(ciphertext: bytes, key: bytes, iv: bytes) -> Optional[bytes]:
    if not CRYPTO_AVAILABLE or not ciphertext or len(ciphertext) % 16 != 0 or len(key) != 16 or len(iv) != 16:
        return None
    try:
        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        return dec.update(ciphertext) + dec.finalize()
    except Exception:
        return None


def decrypt_4013(body: bytes, key: bytes) -> tuple[Optional[bytes], Optional[str]]:
    if not body or not key:
        return None, None
    # The target program tries embedded-IV mode first, then a zero IV if the first mode's length is invalid.
    if len(body) >= 32 and (len(body) - 16) % 16 == 0:
        plain = decrypt_aes_cbc(body[16:], key, body[:16])
        if plain is not None:
            return plain, "embedded_iv"
    if len(body) % 16 == 0:
        plain = decrypt_aes_cbc(body, key, b"\x00" * 16)
        if plain is not None:
            return plain, "zero_iv"
    return None, None


