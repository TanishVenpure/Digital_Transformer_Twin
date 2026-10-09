"""Verify against a disposable local PostgreSQL cluster, never the configured database.

Uses installed PostgreSQL binaries; creates no synthetic-data generator.
The cluster is loopback-only and removed after verification.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get("POSTGRES_BIN", r"C:\Program Files\PostgreSQL\18\bin"))


def run(command, env):
    # Windows postgres descendants can inherit capture_output pipes and keep
    # communicate() blocked after pg_ctl exits. File redirection avoids that.
    log = Path(env["TRANSFORMER_TEST_LOG"])
    offset = log.stat().st_size if log.exists() else 0
    with log.open("a", encoding="utf-8") as output:
        completed = subprocess.run(
            command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=120,
        )
    with log.open(encoding="utf-8", errors="replace") as output:
        output.seek(offset)
        print(output.read(), end="", flush=True)
    completed.check_returncode()


def main():
    for name in ("initdb", "pg_ctl"):
        if not (BIN / (name + (".exe" if os.name == "nt" else ""))).exists():
            raise RuntimeError("Set POSTGRES_BIN to the installed PostgreSQL binary directory")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    temp_parent = Path(tempfile.gettempdir()).resolve()
    # Check the resolved path before the temporary cluster's recursive cleanup.
    cluster_root = Path(tempfile.mkdtemp(prefix="transformer-rule-test-", dir=temp_parent)).resolve()
    if cluster_root.parent != temp_parent or not cluster_root.name.startswith("transformer-rule-test-"):
        raise RuntimeError("Unexpected test-cluster path")
    cluster = cluster_root / "cluster"
    env = os.environ.copy()
    url = f"postgresql+psycopg://codex_rule_test@127.0.0.1:{port}/postgres"
    env["TRANSFORMER_TEST_LOG"] = str(cluster_root / "commands.log")
    env["DATABASE_URL"] = url
    env["TEST_DATABASE_URL"] = url
    env["TRANSFORMER_RULE_VERIFICATION"] = "1"
    started = False
    try:
        run([str(BIN / "initdb"), "-D", str(cluster), "-U", "codex_rule_test", "-A", "trust", "--encoding=UTF8", "--no-locale"], env)
        run([str(BIN / "pg_ctl"), "-D", str(cluster), "-l", str(cluster_root / "server.log"),
             "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"], env)
        started = True
        run([sys.executable, "-m", "alembic", "upgrade", "head"], env)
        run([sys.executable, "-m", "alembic", "check"], env)
        run([sys.executable, "-m", "pytest", "-q"], env)
        # Run only the supplied numerical examples through the real API/service pipeline.
        run([sys.executable, str(ROOT / "scripts" / "rule_examples.py")], env)
        run([sys.executable, str(ROOT / "scripts" / "oil_rule_examples.py")], env)
    finally:
        try:
            if started:
                run([str(BIN / "pg_ctl"), "-D", str(cluster), "-m", "fast", "-w", "stop"], env)
        finally:
            # Never delete a cluster that may still be running.
            if not (cluster / "postmaster.pid").exists():
                shutil.rmtree(cluster_root)


if __name__ == "__main__":
    main()
