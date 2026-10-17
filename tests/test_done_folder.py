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
