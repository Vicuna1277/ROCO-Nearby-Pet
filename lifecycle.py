"""Only the 1044 capture-success branch verified in the user's single-catch PCAP."""
import protocol as p


def actor_id_text(value):
    """Scene actor IDs are uint64; keep them as decimal strings, never signed ints."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    if isinstance(value, str) and not (value and value.isascii() and value.isdecimal()):
        return None
    number = int(value)
    return str(number) if 0 < number < 1 << 64 else None


def capture_success_ids(plain):
    if (len(plain) < 11 or plain[2:4] != (1044).to_bytes(2, 'big')
            or plain[4:6] != b'\x55\xaa'):
        return []
    result = set()
    for acts in p.values(p.parse_proto_fields(plain[10:]), 1, 2):
        for notice in p.values(p.parse_proto_fields(acts), 173, 2):
            fields = p.parse_proto_fields(notice)
            flags = {}
            def add(identifier, success):
                identifier = actor_id_text(identifier)
                if identifier and success is not None:
                    flags.setdefault(identifier, set()).add(success)
            add(p.first_value(fields, 3, 0), p.first_value(fields, 4, 0))
            for entry in p.values(fields, 11, 2):
                nested = p.parse_proto_fields(entry)
                add(p.first_value(nested, 1, 0), p.first_value(nested, 2, 0))
            # Explicit true only; absent/false/invalid/conflicting flags do not remove.
            result.update(identifier for identifier, values in flags.items() if values == {1})
    return sorted(result, key=int)
