"""Finished ERP XML files move to <ERP folder>/done; unfinished ones stay."""
import shutil

from conftest import XML_53


def test_finished_xml_moves_to_done(ws, tmp_path):
    from level7mtm.services.done_folder import move_finished
    erp = tmp_path / "ERP XML"
    erp.mkdir()
    (erp / "done").mkdir()
    src = erp / "Order-9-53-1_2.xml"
    shutil.copy(XML_53, src)
    ws.db.set_setting("watch.folder", str(erp))
    ws.import_file(str(src))
    revs = [r for r in ws.list_revisions()]
    assert len(revs) == 2
    with ws.db.tx() as c:
        c.execute("UPDATE revisions SET status='completed' WHERE id=?", (revs[0]["id"],))
    assert move_finished(ws, [revs[0]["id"]]) == [] and src.exists()          # one line still on its way
    with ws.db.tx() as c:
        c.execute("UPDATE revisions SET status='completed'")
    moved = move_finished(ws, [revs[1]["id"]])
    assert moved == [str(erp / "done" / "Order-9-53-1_2.xml")] and not src.exists()
    f = ws.db.one("SELECT source_path FROM import_files")
    assert f["source_path"] == moved[0]
    # the same name again later: kept, numbered
    shutil.copy(XML_53, src)
    with ws.db.tx() as c:
        c.execute("UPDATE import_files SET source_path=?", (str(src),))
    assert move_finished(ws, [revs[1]["id"]]) == [str(erp / "done" / "Order-9-53-1_2_2.xml")]


def test_only_files_of_the_erp_folder_move(ws, tmp_path):
    from level7mtm.services.done_folder import move_finished
    other = tmp_path / "elsewhere"
    other.mkdir()
    src = other / "Order-9-53-1_2.xml"
    shutil.copy(XML_53, src)
    ws.db.set_setting("watch.folder", str(tmp_path / "ERP"))
    ws.import_file(str(src))
    with ws.db.tx() as c:
        c.execute("UPDATE revisions SET status='completed'")
    assert move_finished(ws, [r["id"] for r in ws.list_revisions()]) == [] and src.exists()


def test_xml_with_an_error_goes_to_er_then_to_done_once_finished(ws, tmp_path):
    from level7mtm.services.done_folder import move_failed, move_finished
    erp = tmp_path / "ERP XML"
    erp.mkdir()
    (erp / "ER").mkdir()
    src = erp / "Order-9-53-1_2.xml"
    shutil.copy(XML_53, src)
    ws.db.set_setting("watch.folder", str(erp))
    ws.import_file(str(src))
    rid = ws.list_revisions()[0]["id"]
    assert move_failed(ws, [rid], ["GRAFIS did not show it within 120 s"], "GRAFIS robot") == \
        [str(erp / "ER" / "Order-9-53-1_2.xml")]
    reason = erp / "ER" / "Order-9-53-1_2.txt"
    assert "within 120 s" in reason.read_text(encoding="utf-8-sig") and not src.exists()
    assert move_failed(ws, [rid], ["again"], "GRAFIS robot") == [str(erp / "ER" / "Order-9-53-1_2.xml")]
    with ws.db.tx() as c:                                         # retried, every line finished
        c.execute("UPDATE revisions SET status='completed'")
    assert move_finished(ws, [rid]) == [str(erp / "done" / "Order-9-53-1_2.xml")]
    assert not reason.exists() and not (erp / "ER" / "Order-9-53-1_2.xml").exists()


def test_process_done_error_side_by_side(ws, tmp_path):
    """The owner's layout: XML are read from process; an error goes to error, a finished file to done, both beside
    process (not inside it)."""
    from level7mtm.services.done_folder import move_failed, move_finished
    main = tmp_path / "ERP"
    proc = main / "process"
    proc.mkdir(parents=True)
    src = proc / "Order-9-53-1_2.xml"
    shutil.copy(XML_53, src)
    ws.db.set_setting("watch.folder", str(proc))
    ws.import_file(str(src))
    rid = ws.list_revisions()[0]["id"]
    assert move_failed(ws, [rid], ["robot"], "GRAFIS robot") == [str(main / "error" / "Order-9-53-1_2.xml")]
    assert (main / "error" / "Order-9-53-1_2.txt").exists() and not src.exists()
    with ws.db.tx() as c:
        c.execute("UPDATE revisions SET status='completed'")
    assert move_finished(ws, [rid]) == [str(main / "done" / "Order-9-53-1_2.xml")]   # from error on to done
    assert not (main / "error" / "Order-9-53-1_2.txt").exists()


def test_choosing_the_main_folder_reads_its_process_folder(ws, tmp_path):
    import os

    import pytest
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    (tmp_path / "ERP" / "Process").mkdir(parents=True)
    win = MainWindow(ws)
    win.start_watch(str(tmp_path / "ERP"))
    assert win.watcher.folder == str(tmp_path / "ERP" / "Process")
    assert ws.db.get_setting("watch.folder") == str(tmp_path / "ERP" / "Process")
    win.timer.stop()
    win.watcher = None
    win.close()
    app.processEvents()


def test_finished_xml_moved_by_hand_into_process_still_goes_to_done(ws, tmp_path):
    """The owner moved the XML files into process after they were read: when cutting is done (or the watch starts)
    the file is found there by its name and goes to done."""
    from level7mtm.services.done_folder import sweep_finished
    main = tmp_path / "ERP XML"
    proc = main / "process"
    proc.mkdir(parents=True)
    src = main / "Order-9-53-1_2.xml"
    shutil.copy(XML_53, src)
    ws.import_file(str(src))                                           # read from the main folder
    shutil.move(str(src), str(proc / src.name))                        # moved by hand into process
    ws.db.set_setting("watch.folder", str(proc))
    assert sweep_finished(ws) == []                                    # not finished yet: stays
    with ws.db.tx() as c:
        c.execute("UPDATE revisions SET status='completed'")
    assert sweep_finished(ws) == [str(main / "done" / src.name)] and not (proc / src.name).exists()


def test_needs_review_goes_to_error_and_back_in_process_is_done_again(ws, tmp_path):
    """Owner: everything that needs attention goes to error — also an order held when read (a Variation without
    meaning); moved back into process, it is read again with today's settings and goes on."""
    from level7mtm.services.done_folder import sweep_held
    from level7mtm.services.grafis_orders import import_and_prepare
    from level7mtm.services.options import option_table, save_options
    main = tmp_path / "ERP"
    proc = main / "process"
    proc.mkdir(parents=True)
    ws.db.set_setting("watch.folder", str(proc))
    ws.db.set_setting("grafis.auto_mdl", False)                          # models aside: the XML's own way
    src = proc / "Order-w.xml"
    src.write_bytes(open(XML_53, "rb").read().replace(b"Collar Square", b"Collar Weird"))
    out = import_and_prepare(ws, str(src))
    assert out.lines[0]["status"] == "needs_review"
    err = main / "error" / "Order-w.xml"
    assert err.exists() and not src.exists()                              # into error, with the reason
    assert "Collar Weird" in (main / "error" / "Order-w.txt").read_text(encoding="utf-8-sig")
    g = option_table(ws)
    next(v for v in next(x for x in g if x["group"] == "collar")["values"]
         if v["value"] == "square")["erp_names"].append("Collar Weird")
    save_options(ws, g)
    shutil.move(str(err), str(src))                                       # the owner moves it back
    again = import_and_prepare(ws, str(src))
    assert again.status == "imported" and all(ln["status"] == "ready" for ln in again.lines)
    assert src.exists()                                                   # fine now: stays until it is finished
    # a file held before this version, still in process: moved when the watch starts
    other = proc / "Order-x.xml"
    other.write_bytes(open(XML_53, "rb").read().replace(b"9-53-", b"9-77-").replace(b"Collar Square", b"Collar X"))
    ws.import_file(str(other))
    assert sweep_held(ws) == [str(main / "error" / "Order-x.xml")] and not other.exists()
