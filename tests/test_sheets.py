"""The size table and the piece options written to Excel for the owner, and read back (also from CSV)."""
from level7mtm.services.options import option_table, save_options
from level7mtm.services.sheets import (
    OPT_HEADER,
    X_HEADER,
    options_from_sheet,
    options_sheet,
    read_rows,
    write_xlsx,
    x_from_sheet,
    x_sheet,
)


def test_an_exported_size_table_reads_back(tmp_path):
    rows = [{"part": 2, "x": 1, "name": "basic thobe", "default": 1500.0, "codes": ["FL"], "formula": "{M}",
             "confirmed": True},
            {"part": 2, "x": 7, "name": "collar--E1", "default": 400.0, "codes": ["COS", "COG"], "formula": "{M}+5",
             "confirmed": False},
            {"part": 2, "x": 8, "name": "front width 1", "default": None, "codes": [], "formula": "{M}",
             "confirmed": True}]
    p = str(tmp_path / "x.xlsx")
    write_xlsx(p, ["الجزء", "X", "الاسم في GRAFIS", "القيمة الافتراضية في GRAFIS", "رموز XML", "القيمة / المعادلة",
                   "مؤكد"], x_sheet(rows))
    back = x_from_sheet(read_rows(p))
    assert [(r["part"], r["x"], r["codes"], r["formula"], r["confirmed"]) for r in back] == [
        (2, 1, ["FL"], "{M}", True), (2, 7, ["COS", "COG"], "{M}+5", False), (2, 8, [], "{M}", True)]


def test_a_hand_made_csv_size_table_with_x_like_002_x1(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("X;XML codes\n002/X1;fl\nX4;dsr, dsl\n", encoding="utf-8-sig")
    back = x_from_sheet(read_rows(str(p)))
    assert [(r["part"], r["x"], r["codes"]) for r in back] == [(2, 1, ["FL"]), (None, 4, ["DSR", "DSL"])]
    assert X_HEADER[1] == "X"


def test_options_round_trip_through_excel_and_save(ws, tmp_path):
    groups = option_table(ws)
    p = str(tmp_path / "o.xlsx")
    write_xlsx(p, OPT_HEADER, options_sheet(groups))
    back = options_from_sheet(read_rows(p))
    plain = lambda gs: [(g["group"], g["required"], g["default"],
                         [(v["value"], v["erp_names"]) for v in g["values"]]) for g in gs]
    assert plain(back) == plain(groups)
    save_options(ws, back)
    assert plain(option_table(ws)) == plain(groups)


def test_options_sheet_need_not_repeat_the_group(tmp_path):
    p = tmp_path / "o.csv"
    p.write_text("رمز المجموعة,مطلوب,رمز القيمة,الاسم (عربي),الاسم في XML (Variation),أرقام البارتات\n"
                 "neck_button,لا,hidden,مخفي,Neck Button Hidden,36\n"
                 ",,visible,ظاهر,Neck Button Visible; Neck Visible,\n", encoding="utf-8")
    (g,) = options_from_sheet(read_rows(str(p)))
    assert g["group"] == "neck_button" and g["required"] is False
    assert [(v["value"], v["label"]["ar"], v["erp_names"], v["parts_text"]) for v in g["values"]] == [
        ("hidden", "مخفي", ["Neck Button Hidden"], "36"),
        ("visible", "ظاهر", ["Neck Button Visible", "Neck Visible"], "")]

