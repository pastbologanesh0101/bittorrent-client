import unittest

from bittorrent import bencode


class TestBencodeRoundTrip(unittest.TestCase):
    def test_integer_round_trip(self):
        for value in (0, 42, -42, 1234567890):
            encoded = bencode.encode(value)
            self.assertEqual(bencode.decode(encoded), value)

    def test_integer_encoding_format(self):
        self.assertEqual(bencode.encode(42), b"i42e")
        self.assertEqual(bencode.encode(-3), b"i-3e")
        self.assertEqual(bencode.encode(0), b"i0e")

    def test_string_round_trip(self):
        self.assertEqual(bencode.encode(b"spam"), b"4:spam")
        self.assertEqual(bencode.decode(b"4:spam"), b"spam")

    def test_str_is_encoded_as_utf8_bytes(self):
        encoded = bencode.encode("hello")
        self.assertEqual(encoded, b"5:hello")
        self.assertEqual(bencode.decode(encoded), b"hello")

    def test_list_round_trip(self):
        value = [b"spam", 42, b"eggs"]
        encoded = bencode.encode(value)
        self.assertEqual(encoded, b"l4:spami42e4:eggse")
        self.assertEqual(bencode.decode(encoded), value)

    def test_dict_round_trip_and_key_sorting(self):
        value = {b"foo": 42, b"bar": b"spam"}
        encoded = bencode.encode(value)
        # keys must be sorted: 'bar' < 'foo'
        self.assertEqual(encoded, b"d3:bar4:spam3:fooi42ee")
        self.assertEqual(bencode.decode(encoded), value)

    def test_nested_structure_round_trip(self):
        value = {
            b"announce": b"http://tracker.example/announce",
            b"info": {
                b"name": b"file.txt",
                b"length": 100,
                b"piece length": 16384,
                b"pieces": b"\x01" * 20 + b"\x02" * 20,
                b"list": [1, 2, [3, 4, {b"x": b"y"}]],
            },
        }
        encoded = bencode.encode(value)
        self.assertEqual(bencode.decode(encoded), value)

    def test_empty_list_and_dict(self):
        self.assertEqual(bencode.decode(bencode.encode([])), [])
        self.assertEqual(bencode.decode(bencode.encode({})), {})

    def test_decode_rejects_trailing_data(self):
        with self.assertRaises(bencode.BencodeError):
            bencode.decode(b"i42eextra")

    def test_decode_rejects_malformed_input(self):
        with self.assertRaises(bencode.BencodeError):
            bencode.decode(b"x")

    def test_encode_rejects_unsupported_type(self):
        with self.assertRaises(TypeError):
            bencode.encode(3.14)


if __name__ == "__main__":
    unittest.main()
