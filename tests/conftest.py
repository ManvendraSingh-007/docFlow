import os

os.environ["DOCFLOW_TESTING"] = "1"
os.environ["DOCFLOW_TEST_DB_PATH"] = f"/tmp/docflow-tests-{os.getpid()}.sqlite3"