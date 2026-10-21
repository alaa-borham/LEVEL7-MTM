"""A handler that raises never stops the watched folder; a retry finds a file moved to ER."""
import os


def test_one_bad_file_never_stops_the_others(tmp_path):
    from level7mtm.services.watcher import FolderWatcher
    for n in ("a.xml", "b.xml"):
        (tmp_path / n).write_text("x")
    seen = []

    def handler(p):
        if p.endswith("a.xml"):
            raise PermissionError("locked")
        seen.append(p)
        return {"status": "ok"}
    t = [0.0]
    w = FolderWatcher(None, str(tmp_path), settle_seconds=1, clock=lambda: t[0], handler=handler)
    w.poll()
    t[0] = 5
    w.poll()
    assert [os.path.basename(p) for p in seen] == ["b.xml"]
    bad = str(tmp_path / "a.xml")
    assert "PermissionError" in w.state.failed[bad]
    os.makedirs(tmp_path / "ER")
    os.replace(bad, tmp_path / "ER" / "a.xml")                   # the failed file was moved to ER
    handled = []
    w.handler = lambda p: handled.append(p) or {"status": "ok"}
    w.retry(bad)
    assert handled == [str(tmp_path / "ER" / "a.xml")]


def test_nesting_runs_with_one_math_thread_and_a_bounded_time(ws, monkeypatch, tmp_path):
    """'OpenBLAS error: Memory allocation still failed' and 'timed out after -24946 seconds': LEVEL7 Nesting runs
    with one math thread, is stopped after its nesting time plus room, and says so in plain words."""
    import subprocess

    import pytest

    from level7mtm.services import auto_cut
    cli = tmp_path / "level7-cli.exe"
    cli.write_text("x")
    ws.db.set_setting("nesting.cli", str(cli))
    cmd, env = auto_cut.nesting_command(ws)
    assert env["OPENBLAS_NUM_THREADS"] == "1" and env["OMP_NUM_THREADS"] == "1"

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(a[0], k["timeout"])
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(subprocess.SubprocessError, match="did not finish within 300 s"):
        auto_cut._run(["x"], env, [], 300)
