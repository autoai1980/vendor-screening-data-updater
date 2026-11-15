import csv
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, MagicMock, patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("updater",ROOT/"scripts/update_dataset.py")
updater=importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)

class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.policy={"requireAllSources":True,"minimumRecordRatioAgainstPrevious":0.8}
        self.config=[{"id":"a","required":True},{"id":"b","required":True}]
        self.previous={"recordCount":10000,"sources":[{"id":"a","recordCount":5000},{"id":"b","recordCount":5000}]}
    def test_normal_changes_pass(self):
        updater.validate_source_counts([{"id":"a","recordCount":4900},{"id":"b","recordCount":5100}],self.previous,self.policy,self.config)
    def test_one_source_drop_is_blocked_even_when_total_is_large(self):
        with self.assertRaisesRegex(RuntimeError,"Source record count"):
            updater.validate_source_counts([{"id":"a","recordCount":3900},{"id":"b","recordCount":6100}],self.previous,self.policy,self.config)
    def test_missing_required_source_is_blocked(self):
        with self.assertRaisesRegex(RuntimeError,"Required sources missing"):
            updater.validate_source_counts([{"id":"a","recordCount":10000}],self.previous,self.policy,self.config)
    def test_empty_required_source_is_blocked(self):
        with self.assertRaisesRegex(RuntimeError,"no records"):
            updater.validate_source_counts([{"id":"a","recordCount":0},{"id":"b","recordCount":10000}],self.previous,self.policy,self.config)
    def test_duplicate_source_is_blocked(self):
        with self.assertRaisesRegex(RuntimeError,"Duplicate source"):
            updater.validate_source_counts([{"id":"a","recordCount":5000},{"id":"a","recordCount":5000}],self.previous,self.policy,self.config)

class DownloadTests(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_idb_downloads_and_parses_official_csv(self,urlopen):
        output=io.StringIO()
        writer=csv.DictWriter(output,fieldnames=sorted(updater.IDB_COLUMNS))
        writer.writeheader()
        for i in range(500):
            writer.writerow({"Title":f"Firm {i}","Entity":"Firm","Country":"Costa Rica",
                             "From":"2026-01-01","To":"2027-01-01",
                             "Prohibited Practice":"Fraud","IDB Sanction Source":"IDB"})
        response=MagicMock()
        response.__enter__.return_value.read.return_value=output.getvalue().encode()
        urlopen.return_value=response
        rows,_=updater.idb({"name":"IDB","url":"https://data.iadb.org/example.csv"},None)
        self.assertEqual(len(rows),500)
        self.assertEqual(rows[0][2],"Firm 0")
        self.assertEqual(rows[0][5],"Costa Rica")
        self.assertEqual(rows[0][7],"2026-01-01")
    @patch("urllib.request.urlopen")
    def test_idb_access_denial_propagates(self,urlopen):
        from urllib.error import HTTPError
        urlopen.side_effect=HTTPError("https://data.iadb.org/example.csv",403,"Forbidden",{},None)
        with self.assertRaises(HTTPError):
            updater.idb({"name":"IDB","url":"https://data.iadb.org/example.csv"},None)
        self.assertEqual(urlopen.call_count,1)

    @patch.object(updater.time,"sleep")
    @patch.object(updater.requests,"get")
    def test_transient_failure_is_retried(self,get,sleep):
        failed=Mock(status_code=503)
        ok=Mock(status_code=200,content=b"valid")
        get.side_effect=[failed,ok]
        self.assertIs(updater.get("https://example.test"),ok)
        self.assertEqual(get.call_count,2)
    @patch.object(updater.time,"sleep")
    @patch.object(updater.requests,"get")
    def test_access_denial_is_not_retried_or_suppressed(self,get,sleep):
        denied=updater.requests.Response()
        denied.status_code=403
        denied._content=b"Forbidden"
        denied.url="https://example.test"
        get.return_value=denied
        with self.assertRaisesRegex(RuntimeError,"Source denied access"):
            updater.get("https://example.test")
        self.assertEqual(get.call_count,1)
        sleep.assert_not_called()

if __name__=="__main__":
    unittest.main()
