"""Streaming classic PCAP reader and Windows Npcap adapter (no packet injection)."""
import ctypes as C
import hashlib
import os
import struct
from pathlib import Path

SUPPORTED_LINKS = {0, 1, 12, 101, 108, 113, 276}


def fingerprint(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


class PcapReader:
    def __init__(self, path):
        self.path = str(path)
        self.stats = {'file': str(Path(path).resolve()), 'packets': 0, 'truncated_packets': 0}

    def __iter__(self):
        with open(self.path, 'rb') as f:
            header = f.read(24)
            if len(header) != 24:
                raise ValueError('PCAP 文件头不完整。')
            formats = {b'\xd4\xc3\xb2\xa1': ('<', 1e6), b'\xa1\xb2\xc3\xd4': ('>', 1e6),
                       b'\x4d\x3c\xb2\xa1': ('<', 1e9), b'\xa1\xb2\x3c\x4d': ('>', 1e9)}
            if header[:4] not in formats:
                raise ValueError('仅支持 classic PCAP；PCAPNG 请先在 Wireshark 另存为 PCAP。')
            endian, scale = formats[header[:4]]
            major, minor, _, _, snaplen, link = struct.unpack(endian + 'HHIIII', header[4:])
            link &= 0xffff
            if major != 2 or link not in SUPPORTED_LINKS:
                raise ValueError(f'不支持的 PCAP 版本/链路类型：{major}.{minor} / {link}')
            self.stats.update(linktype=link, snaplen=snaplen)
            order = 0
            while True:
                record = f.read(16)
                if not record:
                    break
                if len(record) != 16:
                    raise ValueError('PCAP 尾部记录头被截断。')
                sec, frac, caplen, wirelen = struct.unpack(endian + 'IIII', record)
                if caplen > 16 * 1024 * 1024:
                    raise ValueError('PCAP 单包长度异常。')
                data = f.read(caplen)
                if len(data) != caplen:
                    raise ValueError('PCAP 数据包被截断；已处理的数据仍会保留。')
                order += 1
                self.stats['packets'] = order
                self.stats['truncated_packets'] += caplen < wirelen
                yield sec + frac / scale, order, data, link, wirelen


class PcapWriter:
    def __init__(self, path, link):
        # Exclusive creation protects previously captured files.
        self.f = open(path, 'xb')
        self.f.write(struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 262144, link))

    def write(self, ts, data, wirelen):
        sec = int(ts)
        self.f.write(struct.pack('<IIII', sec, min(999999, int((ts-sec)*1e6)), len(data), wirelen))
        self.f.write(data)

    def close(self):
        self.f.close()


class Timeval(C.Structure):
    _fields_ = [('sec', C.c_int32), ('usec', C.c_int32)]


class PacketHeader(C.Structure):
    _fields_ = [('ts', Timeval), ('caplen', C.c_uint32), ('length', C.c_uint32)]


class Device(C.Structure):
    pass


Device._fields_ = [('next', C.POINTER(Device)), ('name', C.c_char_p), ('description', C.c_char_p),
                   ('addresses', C.c_void_p), ('flags', C.c_uint32)]


class BpfProgram(C.Structure):
    _fields_ = [('length', C.c_uint), ('instructions', C.c_void_p)]


class PcapStats(C.Structure):
    _fields_ = [('received', C.c_uint32), ('dropped', C.c_uint32),
                ('interface_dropped', C.c_uint32), ('captured', C.c_uint32)]


class Npcap:
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('实时模式需要 Windows 和 Npcap。')
        folder = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'Npcap'
        dll = folder / 'wpcap.dll'
        if not dll.exists():
            raise RuntimeError('未找到 Npcap。请从 https://npcap.com/#download 安装后重试；离线模式不需要 Npcap。')
        self.dll_dir = os.add_dll_directory(str(folder))
        try:
            self.lib = C.CDLL(str(dll))  # libpcap uses the C calling convention.
        except OSError as e:
            raise RuntimeError(f'Npcap 加载失败，请核对 Python/Npcap 位数：{e}') from e
        sigs = {
            'pcap_findalldevs': ([C.POINTER(C.POINTER(Device)), C.c_char_p], C.c_int),
            'pcap_freealldevs': ([C.POINTER(Device)], None),
            'pcap_create': ([C.c_char_p, C.c_char_p], C.c_void_p),
            'pcap_set_snaplen': ([C.c_void_p, C.c_int], C.c_int),
            'pcap_set_promisc': ([C.c_void_p, C.c_int], C.c_int),
            'pcap_set_timeout': ([C.c_void_p, C.c_int], C.c_int),
            'pcap_set_buffer_size': ([C.c_void_p, C.c_int], C.c_int),
            'pcap_activate': ([C.c_void_p], C.c_int),
            'pcap_datalink': ([C.c_void_p], C.c_int),
            'pcap_setnonblock': ([C.c_void_p, C.c_int, C.c_char_p], C.c_int),
            'pcap_next_ex': ([C.c_void_p, C.POINTER(C.POINTER(PacketHeader)), C.POINTER(C.POINTER(C.c_ubyte))], C.c_int),
            'pcap_geterr': ([C.c_void_p], C.c_char_p),
            'pcap_compile': ([C.c_void_p, C.POINTER(BpfProgram), C.c_char_p, C.c_int, C.c_uint32], C.c_int),
            'pcap_setfilter': ([C.c_void_p, C.POINTER(BpfProgram)], C.c_int),
            'pcap_freecode': ([C.POINTER(BpfProgram)], None),
            'pcap_stats': ([C.c_void_p, C.POINTER(PcapStats)], C.c_int),
            'pcap_close': ([C.c_void_p], None),
        }
        for name, (args, result) in sigs.items():
            func = getattr(self.lib, name)
            func.argtypes, func.restype = args, result
        self.handle = None
        self.warning = ''
        self.link = None

    @staticmethod
    def decode(value):
        if not value:
            return ''
        try:
            return value.decode('utf-8')
        except UnicodeDecodeError:
            return value.decode('mbcs', errors='replace')

    def devices(self):
        head, err = C.POINTER(Device)(), C.create_string_buffer(256)
        if self.lib.pcap_findalldevs(C.byref(head), err) != 0:
            raise RuntimeError(self.decode(err.value))
        result = []
        try:
            p = head
            while p:
                d = p.contents
                result.append({'name': d.name.decode('ascii'),
                               'description': self.decode(d.description), 'flags': d.flags})
                p = d.next
        finally:
            self.lib.pcap_freealldevs(head)
        return result

    def open(self, device, port):
        err = C.create_string_buffer(256)
        self.handle = self.lib.pcap_create(device.encode('ascii'), err)
        if not self.handle:
            raise RuntimeError(self.decode(err.value))
        try:
            for name, value in [('pcap_set_snaplen', 262144), ('pcap_set_promisc', 0),
                                ('pcap_set_timeout', 100), ('pcap_set_buffer_size', 8*1024*1024)]:
                if getattr(self.lib, name)(self.handle, value) != 0:
                    raise RuntimeError(f'{name}: {self.error()}')
            result = self.lib.pcap_activate(self.handle)
            if result < 0:
                raise RuntimeError(f'网卡打开失败：{self.error()}；若 Npcap 限制管理员访问，请以管理员身份启动。')
            if result > 0:
                self.warning = self.error()
            self.link = self.lib.pcap_datalink(self.handle)
            if self.link not in SUPPORTED_LINKS:
                raise RuntimeError(f'暂不支持该网卡的链路类型 {self.link}')
            expression = f'tcp port {port}' if port else 'tcp'
            program = BpfProgram()
            if self.lib.pcap_compile(self.handle, C.byref(program), expression.encode(), 1, 0xffffffff) != 0:
                raise RuntimeError(self.error())
            try:
                if self.lib.pcap_setfilter(self.handle, C.byref(program)) != 0:
                    raise RuntimeError(self.error())
            finally:
                self.lib.pcap_freecode(C.byref(program))
            # A nonblocking handle, rather than the packet timeout, guarantees prompt stop.
            if self.lib.pcap_setnonblock(self.handle, 1, err) != 0:
                raise RuntimeError(self.decode(err.value))
        except Exception:
            self.close()
            raise

    def error(self):
        return self.decode(self.lib.pcap_geterr(self.handle))

    def next(self):
        header, data = C.POINTER(PacketHeader)(), C.POINTER(C.c_ubyte)()
        code = self.lib.pcap_next_ex(self.handle, C.byref(header), C.byref(data))
        if code == 0:
            return None
        if code < 0:
            raise RuntimeError(self.error() or 'Npcap 采集结束')
        h = header.contents
        if h.caplen > 262144:
            raise RuntimeError('Npcap 返回异常数据包长度')
        return h.ts.sec + h.ts.usec / 1e6, C.string_at(data, h.caplen), h.length

    def stats(self):
        value = PcapStats()
        if self.handle and self.lib.pcap_stats(self.handle, C.byref(value)) == 0:
            return {name: getattr(value, name) for name, _ in value._fields_[:3]}
        return {}

    def close(self):
        if self.handle:
            self.lib.pcap_close(self.handle)
            self.handle = None
