import csv
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import zipfile

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
    def archive(self,files):
        out=io.BytesIO()
        with zipfile.ZipFile(out,"w") as z:
            for name,body in files.items():
                z.writestr(name,body)
        return out.getvalue()
    def csv_body(self):
        out=io.StringIO()
        writer=csv.writer(out)
        writer.writerow(sorted(updater.IDB_COLUMNS))
        writer.writerow(["Example"]*len(updater.IDB_COLUMNS))
        return out.getvalue()
    def test_one_sanctions_csv_selected_with_other_metadata(self):
        payload=self.archive({"data/sanctions.csv":self.csv_body(),"metadata.csv":"title,date\nExample,2026\n"})
        name,body=updater.idb_csv_from_archive(payload)
        self.assertEqual(name,"data/sanctions.csv")
        self.assertIn(b"Title",body)
    def test_ambiguous_archive_is_blocked(self):
        with self.assertRaisesRegex(RuntimeError,"found 2"):
            updater.idb_csv_from_archive(self.archive({"a.csv":self.csv_body(),"b.csv":self.csv_body()}))
    def test_wrong_schema_is_blocked(self):
        with self.assertRaisesRegex(RuntimeError,"found 0"):
            updater.idb_csv_from_archive(self.archive({"a.csv":"wrong,columns\n1,2\n"}))
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
        denied=Mock(status_code=403)
        denied.raise_for_status.side_effect=updater.requests.HTTPError("403")
        get.return_value=denied
        with self.assertRaises(updater.requests.HTTPError):
            updater.get("https://example.test")
        self.assertEqual(get.call_count,1)
        sleep.assert_not_called()

if __name__=="__main__":
    unittest.main()
