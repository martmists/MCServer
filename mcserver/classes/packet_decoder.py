import io
import struct

from mcserver.events.init import HandshakeEvent
from mcserver.events.login import ConfirmEncryptionEvent, LoginStartEvent
from mcserver.events.status import PingEvent, StatusEvent, Connect16Event
from mcserver.objects.server_core import ServerCore
from mcserver.utils.cryptography import decrypt_secret
from mcserver.utils.logger import debug


class PacketDecoder:
    def __init__(self, protocol: int, status: int):
        self.protocol = protocol
        self.status = status
        self.buffer: io.BytesIO = None

    def read(self, fmt: str):
        fmt = ">" + fmt
        size = struct.calcsize(fmt)
        vals = struct.unpack(fmt, self.buffer.read(size))
        return vals if len(vals) != 1 else vals[0]

    def read_varint(self) -> int:
        number = 0
        for i in range(10):
            b = self.read("B")
            number |= (b & 0x7F) << 7*i
            if not b & 0x80:
                break

        if number & (1 << 31):
            number -= 1 << 32

        return number

    def read_string(self) -> str:
        size = self.read_varint()
        return self.buffer.read(size).decode()

    def read_position(self):
        def unpack_twos_comp(bits, number):
            if (number & (1 << (bits - 1))) != 0:
                number = number - (1 << bits)
            return number

        number = self.read('Q')
        x = unpack_twos_comp(26, (number >> 38))
        y = unpack_twos_comp(12, (number >> 26 & 0xFFF))
        z = unpack_twos_comp(26, (number & 0x3FFFFFF))
        return x, y, z

    def decode(self, packet: bytes):
        assert packet != b""

        self.buffer = io.BytesIO(packet)

        if packet[0] == 254:
            # TODO: handle 1.6 connection attempt
            data = self.decode_connection_16()
            return self.buffer.read(), data

        packet_length = self.read_varint()
        pos = self.buffer.tell()
        assert len(self.buffer.read()) >= packet_length
        self.buffer.seek(pos)

        packet_id = self.read_varint()
        # debug(f"Packet identifier: {packet_id}")
        if packet_id == 0:
            if self.status == 0:
                data = self.decode_handshake()
            elif self.status == 1:
                data = self.decode_status()
            elif self.status == 2:
                data = self.decode_start_login()
        elif packet_id == 1:
            if self.status == 1:
                data = self.decode_ping()
            elif self.status == 2:
                data = self.decode_encryption()

        try:
            data
        except NameError:
            debug(packet)
            raise Exception(f"Unhandled packet ID {packet_id} with data {self.buffer.read()} "
                            f"while in state {self.status}")

        # Seek in case we leave some data by accident
        self.buffer.seek(pos+packet_length)
        return self.buffer.read(), data  # read the buffer to return remaining bytes

    def decode_connection_16(self):
        ident = self.buffer.read(1)
        payload = self.buffer.read(1)
        sub_ident = self.buffer.read(1)
        size = self.read("h")
        enc = self.buffer.read(size*2)
        ping_host = enc.decode("UTF-16BE")
        size_remain = self.read("h")
        protocol = self.buffer.read(1)[0]
        len_host = self.read("h") * 2
        hostname = self.buffer.read(len_host).decode("UTF-16BE")
        port = self.read("i")
        return Connect16Event("connect_16", protocol, hostname, port)

    def decode_handshake(self):
        protocol = self.read_varint()
        if protocol in ServerCore.supported_protocols():
            self.protocol = protocol
        else:
            raise Exception("Invalid protocol")
        hostname = self.read_string()
        port = self.read("H")
        self.status = self.read_varint()
        return HandshakeEvent("handshake", hostname, port)

    def decode_status(self):
        return StatusEvent("status")

    def decode_ping(self):
        return PingEvent("ping", self.read("q"))

    def decode_start_login(self):
        return LoginStartEvent("login_start", self.read_string())

    def decode_encryption(self):
        secret = decrypt_secret(ServerCore.keypair,
                                self.buffer.read(self.read_varint()))
        verify = decrypt_secret(ServerCore.keypair,
                                self.buffer.read(self.read_varint()))
        return ConfirmEncryptionEvent("login_encryption", secret, verify)
