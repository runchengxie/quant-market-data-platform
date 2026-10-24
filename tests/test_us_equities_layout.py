import pathlib
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from quant_market_data_platform.offline_equities import identity as ident
from quant_market_data_platform.offline_equities import integrity as m


class Checks(unittest.TestCase):
    def table(self):
        return pa.table(
            {
                "text": pa.array(["", "x", None, "汉字"]),
                "n": pa.array([1, None, 3, 4], type=pa.int64()),
                "f": pa.array([-0.0, None, 1.25, 4.5]),
                "utc": pa.array([0, None, 1, 2], type=pa.timestamp("us", tz="UTC")),
                "flag": pa.array([True, None, False, True]),
            }
        )

    def test_canonical_digest_and_slice(self):
        t = self.table()
        h = m.fingerprint(t)
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "test.parquet"
            pq.write_table(t, p, compression="zstd", compression_level=6, write_page_checksum=True)
            r = pq.ParquetFile(p, page_checksum_verification=True).read()
            self.assertEqual(h, m.fingerprint(r))
        self.assertEqual(
            m.fingerprint(t.slice(1, 2)),
            m.fingerprint(pa.concat_tables([t.slice(1, 1), t.slice(2, 1)])),
        )
        for changed in [
            t.set_column(0, "text", pa.array([None, "x", None, "汉字"])),
            t.set_column(1, "n", pa.array([2, None, 3, 4], type=pa.int64())),
        ]:
            self.assertNotEqual(h, m.fingerprint(changed))

    def test_identity(self):
        for date, key in [
            ("20250926", "research:WOLF:old-equity"),
            ("20250929", "research:WOLF:post-reorganization"),
        ]:
            t = pa.table({"Date": [date], "Ticker": ["WOLF"], "AuditCandidateSecurityKey": [""]})
            self.assertEqual(ident.add_identity(t)["ResearchKey"][0].as_py(), key)
        t = pa.table(
            {
                "Date": ["20260225", "20260227"],
                "Ticker": ["RNA", "RNA"],
                "AuditCandidateSecurityKey": ["old", "new"],
            }
        )
        self.assertEqual(ident.add_identity(t)["ResearchKey"].to_pylist(), ["old", "new"])
        self.assertNotEqual(
            ident.security_token("BNY", "old", "unverified"),
            ident.security_token("BNY", "new", "unverified"),
        )
        self.assertNotEqual(
            ident.security_token("SKYD.WS", "same", "when_issued"),
            ident.security_token("SKYD.WS", "same", "regular_way"),
        )

    def test_order(self):
        t = pa.table({"Date": ["20251231", "20260102"], "TimeBarStart": ["20:00", "04:00"]})
        self.assertEqual(m.check_order(t, None), "20260102|04:00")
        with self.assertRaises(ValueError):
            m.check_order(t, "20260102|05:00")
        with self.assertRaises(ValueError):
            m.check_order(pa.table({"Date": ["20260102"] * 2, "TimeBarStart": ["09:30"] * 2}), None)


if __name__ == "__main__":
    unittest.main()
