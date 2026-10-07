"""Bounds-checked TLS ClientHello parser. No TLS decryption or certificates."""

from sentinel_agent.egress.config import normalize_host


class InvalidClientHello(ValueError):
    pass


def client_hello_sni(handshake: bytes) -> str | None:
    """Parse one complete handshake message (including its four-byte header)."""
    position = 0

    def take(size):
        nonlocal position
        if size < 0 or position + size > len(handshake):
            raise InvalidClientHello("Truncated ClientHello")
        value = handshake[position:position + size]
        position += size
        return value

    def number(size):
        return int.from_bytes(take(size), "big")

    def vector(size):
        return take(number(size))

    if number(1) != 1 or number(3) != len(handshake) - 4:
        raise InvalidClientHello("Expected one ClientHello")
    take(2 + 32)  # Legacy version and random.
    if len(vector(1)) > 32:
        raise InvalidClientHello("Invalid session ID")
    ciphers = vector(2)
    if not ciphers or len(ciphers) % 2:
        raise InvalidClientHello("Invalid cipher suites")
    if not vector(1):
        raise InvalidClientHello("Invalid compression methods")
    if position == len(handshake):
        return None
    end = number(2) + position
    if end != len(handshake):
        raise InvalidClientHello("Invalid extension length")
    seen = set()
    server_name = None
    while position < end:
        kind = number(2)
        data = vector(2)
        if kind in seen:
            raise InvalidClientHello("Duplicate TLS extension")
        seen.add(kind)
        if kind != 0:
            continue
        if len(data) < 5 or int.from_bytes(data[:2], "big") != len(data) - 2:
            raise InvalidClientHello("Invalid SNI list")
        cursor = 2
        types = set()
        while cursor < len(data):
            if cursor + 3 > len(data):
                raise InvalidClientHello("Invalid SNI entry")
            name_type = data[cursor]
            size = int.from_bytes(data[cursor + 1:cursor + 3], "big")
            cursor += 3
            name = data[cursor:cursor + size]
            cursor += size
            if not size or len(name) != size or name_type in types:
                raise InvalidClientHello("Invalid SNI entry")
            types.add(name_type)
            if name_type == 0:
                try:
                    server_name = normalize_host(name.decode("ascii"))
                except (ValueError, UnicodeError) as exc:
                    raise InvalidClientHello("Invalid SNI hostname") from exc
    return server_name


async def read_client_hello(reader, max_bytes=131072):
    """Reassemble a fragmented handshake while retaining original wire bytes."""
    wire = bytearray()
    handshake = bytearray()
    while len(wire) < max_bytes:
        header = await reader.readexactly(5)
        length = int.from_bytes(header[3:5], "big")
        if header[0] != 22 or header[1] != 3 or not 0 < length <= 18432:
            raise InvalidClientHello("Expected TLS handshake record")
        if len(wire) + 5 + length > max_bytes:
            raise InvalidClientHello("ClientHello exceeds limit")
        payload = await reader.readexactly(length)
        wire.extend(header + payload)
        handshake.extend(payload)
        if len(handshake) >= 4:
            size = 4 + int.from_bytes(handshake[1:4], "big")
            if size > max_bytes:
                raise InvalidClientHello("ClientHello exceeds limit")
            if len(handshake) >= size:
                return bytes(wire), client_hello_sni(bytes(handshake[:size]))
    raise InvalidClientHello("ClientHello exceeds limit")
